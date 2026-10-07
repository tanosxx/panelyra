import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-window-extension.py"
spec = importlib.util.spec_from_file_location("panelyra_extension_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


class ExtensionInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.metadata = {"uuid": installer.UUID, "shell-version": ["50"],
                         "session-modes": ["user", "unlock-dialog"]}
        (self.source / "metadata.json").write_text(json.dumps(self.metadata))
        (self.source / "extension.js").write_text("export default class Example {}\n")
        self.destination = self.root / "extensions" / installer.UUID
        self.backups = self.root / "backups"

    def install(self):
        return installer.install_files(self.source, self.destination, self.backups)

    def test_install_and_repeated_install_preserve_unrelated_extension(self):
        unrelated = self.destination.parent / "other@example.org"
        unrelated.mkdir(parents=True)
        (unrelated / "state").write_text("untouched")
        result = self.install()
        self.assertTrue(result.changed)
        self.assertFalse(result.replaced)
        self.assertEqual(installer.inventory(self.source), installer.inventory(self.destination))
        self.assertFalse(self.install().changed)
        self.assertFalse(self.backups.exists())
        self.assertEqual((unrelated / "state").read_text(), "untouched")

    def test_update_makes_exact_copy_before_replacing(self):
        self.install()
        old_file = self.destination / "previous-only.js"
        old_file.write_text("previous data\n")
        old_file.chmod(0o600)
        previous = installer.inventory(self.destination)
        (self.source / "extension.js").write_text("updated\n")
        result = self.install()
        self.assertTrue(result.replaced)
        self.assertEqual(installer.inventory(result.backup), previous)
        self.assertEqual((result.backup / "previous-only.js").stat().st_mode & 0o777, 0o600)
        self.assertEqual(installer.inventory(self.destination), installer.inventory(self.source))

    def test_failed_replacement_restores_previous_directory(self):
        self.install()
        previous = installer.inventory(self.destination)
        (self.source / "extension.js").write_text("updated\n")
        replace = installer.os.replace

        def fail_staged(source, destination):
            if Path(source).name == "new":
                raise OSError("simulated disk error")
            replace(source, destination)

        with patch.object(installer.os, "replace", side_effect=fail_staged):
            with self.assertRaisesRegex(OSError, "disk error"):
                self.install()
        self.assertEqual(installer.inventory(self.destination), previous)

    def test_does_not_replace_other_uuid_even_in_our_directory(self):
        self.install()
        (self.destination / "metadata.json").write_text('{"uuid": "other@example.org"}')
        with self.assertRaisesRegex(ValueError, "другому расширению"):
            self.install()

    def test_rejects_symlinked_source_file_and_install_parent(self):
        victim = self.root / "victim"
        victim.write_text("private")
        (self.source / "linked.js").symlink_to(victim)
        with self.assertRaisesRegex(ValueError, "Символическая ссылка"):
            self.install()
        (self.source / "linked.js").unlink()
        real = self.root / "real"
        real.mkdir()
        self.destination.parent.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Символическая ссылка"):
            self.install()
        self.assertEqual(list(real.iterdir()), [])
        self.assertEqual(victim.read_text(), "private")

    def test_rejects_symlink_in_old_extension_before_backup_or_replace(self):
        self.install()
        (self.destination / "linked").symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Символическая ссылка"):
            self.install()
        self.assertFalse(self.backups.exists())

    def test_backup_destination_uses_checkout_or_user_data_for_read_only_package(self):
        user_data = self.root / "user-data"
        with patch.object(installer.os, "access", return_value=True):
            self.assertEqual(installer.default_backup_root(user_data, self.root), self.backups)
        with patch.object(installer.os, "access", return_value=False):
            self.assertEqual(installer.default_backup_root(user_data, self.root),
                             user_data / "panelyra" / "backups")


class ExtensionActivationTests(unittest.TestCase):
    def setUp(self):
        self.settings = Mock()
        self.settings.get_boolean.side_effect = lambda key: key == "allow-extension-installation"
        self.values = {"enabled-extensions": ["other@example.org"],
                       "disabled-extensions": ["disabled@example.org", installer.UUID]}
        self.settings.get_strv.side_effect = lambda key: self.values[key].copy()
        self.settings.is_writable.return_value = True
        self.settings.set_strv.return_value = True
        self.connection = Mock()
        self.Gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0),
                                   Settings=SimpleNamespace(sync=Mock()))
        self.GLib = SimpleNamespace(Variant=lambda signature, value: value)

    def enable(self):
        return installer.enable_extension(self.Gio, self.GLib, settings=self.settings,
                                          connection=self.connection)

    def respond(self, info, enabled):
        self.connection.call_sync.side_effect = [Mock(unpack=lambda: (info,)),
                                                  Mock(unpack=lambda: (enabled,))]

    def test_new_extension_queues_only_our_uuid_preserving_other_preferences(self):
        self.respond({}, False)
        self.assertEqual(self.enable()[0], "pending")
        self.settings.set_strv.assert_any_call("enabled-extensions", ["other@example.org", installer.UUID])
        self.settings.set_strv.assert_any_call("disabled-extensions", ["disabled@example.org"])
        self.assertEqual(self.settings.set_strv.call_count, 2)
        self.Gio.Settings.sync.assert_called_once()

    def test_existing_extension_uses_public_api_without_settings_rewrite(self):
        self.respond({"state": 2}, True)
        self.assertEqual(self.enable()[0], "requested")
        self.settings.set_strv.assert_not_called()
        self.assertEqual(self.connection.call_sync.call_args.args[3], "EnableExtension")

    def test_globally_disabled_extensions_are_not_enabled(self):
        self.settings.get_boolean.side_effect = lambda key: True
        self.assertEqual(self.enable()[0], "disabled")
        self.settings.set_strv.assert_not_called()
        self.connection.call_sync.assert_not_called()

    def test_locked_setting_prevents_partial_changes(self):
        self.respond({}, False)
        self.settings.is_writable.side_effect = lambda key: key != "disabled-extensions"
        self.assertEqual(self.enable()[0], "failed")
        self.settings.set_strv.assert_not_called()

    def test_already_queued_uuid_is_not_duplicated(self):
        self.values = {"enabled-extensions": ["other@example.org", installer.UUID],
                       "disabled-extensions": ["disabled@example.org"]}
        self.respond({}, False)
        self.assertEqual(self.enable()[0], "pending")
        self.settings.set_strv.assert_not_called()


if __name__ == "__main__":
    unittest.main()

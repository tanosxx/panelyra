"""The tablet Internet switch must not disconnect USB or alter saved profiles."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    from gi.repository import Gio, GLib
except ImportError:
    Gio = GLib = None

from usbdisplay import internet
from usbdisplay.network import UsbLink


def variant_settings(dns=False):
    settings = {
        "connection": {"uuid": GLib.Variant("s", "tablet-uuid"),
                       "id": GLib.Variant("s", "USB tablet")},
        "ipv4": {"method": GLib.Variant("s", "auto"),
                 "address-data": GLib.Variant("aa{sv}", []),
                 "route-metric": GLib.Variant("x", 90),
                 "never-default": GLib.Variant("b", False)},
        "ipv6": {"method": GLib.Variant("s", "auto"),
                 "addr-gen-mode": GLib.Variant("i", 1)},
        "802-3-ethernet": {"mtu": GLib.Variant("u", 1500)},
    }
    if dns:
        settings["ipv4"].update({"dns": GLib.Variant("au", [134744072]),
                                 "dns-search": GLib.Variant("as", ["~."] )})
        settings["ipv6"]["dns"] = GLib.Variant("aay", [list(range(16))])
    return settings


class FakeNM:
    """A typed D-Bus fake enforcing the NM applied-connection version contract."""
    def __init__(self, settings):
        self.applied = settings
        self.profile = {name: values.copy() for name, values in settings.items()}
        self.owner = ":1.5"
        self.bus_id = "boot-bus-id"
        self.device = "/device/7"
        self.active = "/active/5"
        self.version = 6
        self.calls = []
        self.reapply_error = None
        self.before_reapply = None
        self.after_get_profile = None
        self.managed = True
        self.driver = "rndis_host"

    def call_sync(self, destination, path, interface, method, args, reply, flags, timeout, cancel):
        self.calls.append((destination, path, interface, method, args, timeout))
        if destination not in ("org.freedesktop.DBus", self.owner):
            raise AssertionError("NetworkManager calls must pin its unique bus owner")
        if method == "GetNameOwner":
            return GLib.Variant("(s)", (self.owner,))
        if method == "GetId":
            return GLib.Variant("(s)", (self.bus_id,))
        if method == "GetDeviceByIpIface":
            return GLib.Variant("(o)", (self.device,))
        if method == "GetAll" and path == self.device:
            return GLib.Variant("(a{sv})", ({
                "State": GLib.Variant("u", 100),
                "Managed": GLib.Variant("b", self.managed),
                "Interface": GLib.Variant("s", "usb0"),
                "Driver": GLib.Variant("s", self.driver),
                "ActiveConnection": GLib.Variant("o", self.active),
            },))
        if method == "GetAll" and path == self.active:
            return GLib.Variant("(a{sv})", ({
                "Connection": GLib.Variant("o", "/profile/11"),
                "Uuid": GLib.Variant("s", "tablet-uuid"),
                "State": GLib.Variant("u", 2),
            },))
        if method == "GetAppliedConnection":
            return GLib.Variant("(a{sa{sv}}t)", (self.applied, self.version))
        if method == "GetSettings":
            if self.after_get_profile:
                self.after_get_profile()
            return GLib.Variant("(a{sa{sv}})", (self.profile,))
        if method == "Reapply":
            if self.before_reapply:
                self.before_reapply()
            if self.reapply_error:
                raise self.reapply_error
            if args.get_child_value(1).unpack() != self.version:
                raise Gio.dbus_error_new_for_dbus_error(
                    internet.NM + ".Device.VersionMismatch", "Applied connection version mismatch")
            if args.get_child_value(2).unpack() != 1:
                raise AssertionError("Reapply must preserve external IP addresses/routes")
            self.applied = internet._settings(args.get_child_value(0))
            self.version += 1
            return GLib.Variant("()", ())
        raise AssertionError((destination, path, interface, method))

    @property
    def mutations(self):
        return [call for call in self.calls if call[3] not in (
            "GetNameOwner", "GetId", "GetDeviceByIpIface", "GetAll",
            "GetAppliedConnection", "GetSettings")]


@unittest.skipUnless(GLib is not None, "PyGObject required for typed NetworkManager tests")
class InternetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.link = UsbLink("usb0", "192.168.42.20", 24, "192.168.42.129")
        self.links = [self.link]
        self.bus = FakeNM(variant_settings(dns=True))
        self.controller = self.new_controller()

    def new_controller(self):
        return internet.InternetController(
            connection=self.bus, gio=Gio, glib=GLib, runtime_dir=self.runtime,
            discover=lambda interface=None: self.links)

    def expect_error(self, code, operation):
        with self.assertRaises(internet.InternetError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def disable(self):
        return self.controller.set_enabled(self.controller.inspect(), False)

    def test_constructor_performs_no_io_and_inspection_is_read_only(self):
        self.assertEqual(self.bus.calls, [])
        state = self.controller.inspect()
        self.assertTrue(state.enabled)
        self.assertEqual(state.interface, "usb0")
        self.assertEqual(state.version_id, 6)
        self.assertEqual(self.bus.mutations, [])
        self.assertFalse((self.runtime / "panelyra-internet").exists())

    def test_typed_round_trip_restores_flags_dns_and_missing_properties(self):
        before = GLib.Variant("a{sa{sv}}", self.bus.applied)
        profile = GLib.Variant("a{sa{sv}}", self.bus.profile)
        disabled = self.disable()
        self.assertFalse(disabled.enabled)
        for family in internet.FAMILIES:
            self.assertTrue(self.bus.applied[family]["never-default"].unpack())
            self.assertTrue(self.bus.applied[family]["ignore-auto-dns"].unpack())
            self.assertFalse(any(self.bus.applied[family].get(key) for key in internet.DNS))
        self.assertEqual(self.bus.applied["ipv4"]["route-metric"].get_type_string(), "x")
        self.assertEqual(self.bus.applied["802-3-ethernet"]["mtu"].get_type_string(), "u")
        restored = self.controller.set_enabled(disabled, True)
        self.assertTrue(restored.enabled)
        self.assertEqual(GLib.Variant("a{sa{sv}}", self.bus.applied).unpack(), before.unpack())
        self.assertEqual(GLib.Variant("a{sa{sv}}", self.bus.profile), profile)
        self.assertEqual([call[3] for call in self.bus.mutations], ["Reapply", "Reapply"])
        self.assertFalse((self.runtime / "panelyra-internet/restore.json").exists())

    def test_restart_gui_keeps_recovery_data_private_and_typed(self):
        before = GLib.Variant("a{sa{sv}}", self.bus.applied).unpack()
        state = self.disable()
        folder = self.runtime / "panelyra-internet"
        self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
        self.assertEqual((folder / "restore.json").stat().st_mode & 0o777, 0o600)
        content = json.loads((folder / "restore.json").read_text())
        self.assertEqual(set(content["before"]), {"ipv4", "ipv6"})
        self.assertEqual(content["before"]["ipv4"]["dns"][0], "au")
        self.assertEqual(content["before"]["ipv6"]["dns"][0], "aay")
        self.new_controller().set_enabled(state, True)
        self.assertEqual(GLib.Variant("a{sa{sv}}", self.bus.applied).unpack(), before)

    def test_disable_idempotent_does_not_overwrite_original_or_reapply(self):
        state = self.disable()
        path = self.runtime / "panelyra-internet/restore.json"
        original = path.read_bytes()
        self.assertEqual(self.controller.set_enabled(state, False), state)
        self.assertEqual(len(self.bus.mutations), 1)
        self.assertEqual(path.read_bytes(), original)

    def test_cli_disabled_connection_restores_saved_profile_without_changing_it(self):
        original = GLib.Variant("a{sa{sv}}", self.bus.profile).unpack()
        for family in internet.FAMILIES:
            for key in internet.FLAGS:
                self.bus.applied[family][key] = GLib.Variant("b", True)
            for key in internet.DNS:
                self.bus.applied[family].pop(key, None)
        self.assertFalse(self.controller.inspect().enabled)
        state = self.controller.set_enabled(self.controller.inspect(), True)
        self.assertTrue(state.enabled)
        self.assertEqual(GLib.Variant("a{sa{sv}}", self.bus.applied).unpack(), original)
        self.assertEqual(GLib.Variant("a{sa{sv}}", self.bus.profile).unpack(), original)

    def test_saved_profile_forbids_internet_but_explicit_enable_works_temporarily(self):
        self.disable()
        (self.runtime / "panelyra-internet/restore.json").unlink()
        self.bus.profile = {name: values.copy() for name, values in self.bus.applied.items()}
        profile = GLib.Variant("a{sa{sv}}", self.bus.profile).unpack()
        state = self.controller.set_enabled(self.controller.inspect(), True)
        self.assertTrue(state.enabled)
        for family in internet.FAMILIES:
            for key in internet.FLAGS:
                self.assertFalse(self.bus.applied[family][key].unpack())
        self.assertEqual(GLib.Variant("a{sa{sv}}", self.bus.profile).unpack(), profile)

    def test_fallback_profile_default_routes_forbidden_with_dns_allowed_can_enable(self):
        self.disable()
        (self.runtime / "panelyra-internet/restore.json").unlink()
        for family in internet.FAMILIES:
            self.bus.profile[family]["never-default"] = GLib.Variant("b", True)
            self.bus.profile[family]["ignore-auto-dns"] = GLib.Variant("b", False)
        self.controller.set_enabled(self.controller.inspect(), True)
        for family in internet.FAMILIES:
            self.assertFalse(self.bus.applied[family]["never-default"].unpack())

    def test_snapshot_with_missing_settings_fails_safely(self):
        state = self.disable()
        path = self.runtime / "panelyra-internet/restore.json"
        data = json.loads(path.read_text())
        data.pop("before")
        path.write_text(json.dumps(data))
        self.expect_error("storage", lambda: self.controller.set_enabled(state, True))
        self.assertEqual(len(self.bus.mutations), 1)

    def test_partial_original_restrictions_are_restored_exactly(self):
        self.bus.applied["ipv6"]["never-default"] = GLib.Variant("b", True)
        self.bus.applied["ipv4"]["ignore-auto-dns"] = GLib.Variant("b", True)
        original = GLib.Variant("a{sa{sv}}", self.bus.applied).unpack()
        self.controller.set_enabled(self.disable(), True)
        self.assertEqual(GLib.Variant("a{sa{sv}}", self.bus.applied).unpack(), original)

    def test_no_usb_or_multiple_candidates_does_not_call_network_manager(self):
        for links, code in (([], "no-usb"), ([self.link, self.link], "ambiguous")):
            self.links = links
            self.expect_error(code, self.controller.inspect)
        self.assertEqual(self.bus.calls, [])

    def test_non_usb_or_unmanaged_device_is_rejected(self):
        self.bus.driver = "iwlwifi"
        self.expect_error("no-usb", self.controller.inspect)
        self.bus.driver = "rndis_host"
        self.bus.managed = False
        self.expect_error("no-usb", self.controller.inspect)

    def test_stale_ui_state_after_reconnect_cannot_change_new_connection(self):
        state = self.controller.inspect()
        self.bus.active = "/active/6"
        self.expect_error("changed", lambda: self.controller.set_enabled(state, False))
        self.assertEqual(self.bus.mutations, [])

    def test_ip_change_or_another_applied_edit_invalidates_ui_state(self):
        state = self.controller.inspect()
        self.links = [replace(self.link, local_ip="192.168.42.21")]
        self.expect_error("changed", lambda: self.controller.set_enabled(state, False))
        self.links = [self.link]
        self.bus.version += 1
        self.expect_error("changed", lambda: self.controller.set_enabled(state, False))
        self.assertEqual(self.bus.mutations, [])

    def test_cas_protects_edit_between_final_read_and_reapply(self):
        self.bus.before_reapply = lambda: setattr(self.bus, "version", self.bus.version + 1)
        self.expect_error("changed", self.disable)
        self.assertTrue(self.controller.inspect().enabled)

    def test_profile_read_race_is_caught_before_reapply(self):
        self.disable()
        (self.runtime / "panelyra-internet/restore.json").unlink()
        count = len(self.bus.mutations)
        self.bus.after_get_profile = lambda: setattr(self.bus, "active", "/active/8")
        self.expect_error("changed", lambda: self.controller.set_enabled(self.controller.inspect(), True))
        self.assertEqual(len(self.bus.mutations), count)

    def test_nm_or_bus_restart_ignores_previous_runtime_snapshot(self):
        self.disable()
        # The saved profile now differs from our old snapshot; a daemon restart
        # can reuse device/active paths but must not replay the old DNS settings.
        self.bus.profile["ipv4"]["dns"] = GLib.Variant("au", [16843009])
        self.bus.owner = ":1.100"
        self.bus.bus_id = "new-bus-id"
        self.controller.set_enabled(self.controller.inspect(), True)
        self.assertEqual(self.bus.applied["ipv4"]["dns"].unpack(), [16843009])

    def test_unrelated_user_edit_survives_restore(self):
        self.disable()
        self.bus.applied["ipv4"]["route-metric"] = GLib.Variant("x", 500)
        self.bus.version += 1
        self.controller.set_enabled(self.controller.inspect(), True)
        self.assertEqual(self.bus.applied["ipv4"]["route-metric"].unpack(), 500)

    def test_corrupt_snapshot_or_unsafe_storage_never_changes_network(self):
        self.disable()
        path = self.runtime / "panelyra-internet/restore.json"
        path.write_text("{")
        count = len(self.bus.mutations)
        self.expect_error("storage", lambda: self.controller.set_enabled(self.controller.inspect(), True))
        self.assertEqual(len(self.bus.mutations), count)
        self.runtime.chmod(0o777)
        self.expect_error("storage", lambda: self.controller.set_enabled(self.controller.inspect(), True))
        self.runtime.chmod(0o700)

    def test_symlink_restore_file_is_not_followed(self):
        self.disable()
        path = self.runtime / "panelyra-internet/restore.json"
        path.unlink()
        target = self.runtime / "unrelated"
        target.write_text("private")
        path.symlink_to(target)
        self.expect_error("storage", lambda: self.controller.set_enabled(self.controller.inspect(), True))
        self.assertEqual(target.read_text(), "private")
        self.assertEqual(len(self.bus.mutations), 1)

    def test_permission_denied_is_reported_and_existing_network_survives(self):
        self.bus.reapply_error = Gio.dbus_error_new_for_dbus_error(
            internet.NM + ".PermissionDenied", "Not authorized")
        self.expect_error("permission", self.disable)
        self.assertTrue(self.controller.inspect().enabled)

    def test_explicit_default_route_and_policy_routing_are_not_silently_ignored(self):
        for key, value in (
            ("gateway", GLib.Variant("s", "192.168.42.129")),
            ("route-data", GLib.Variant("aa{sv}", [{"dest": GLib.Variant("s", "0.0.0.0"),
                                                    "prefix": GLib.Variant("u", 0)}])),
            ("routing-rules", GLib.Variant("aa{sv}", [{"priority": GLib.Variant("u", 100)}])),
        ):
            with self.subTest(key=key):
                self.bus.applied["ipv4"][key] = value
                self.expect_error("unsupported", self.disable)
                self.bus.applied["ipv4"].pop(key)
        self.assertEqual(self.bus.mutations, [])


if __name__ == "__main__":
    unittest.main()

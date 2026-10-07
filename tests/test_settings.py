"""Preference corruption must not break startup or destroy the last good file."""

from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from usbdisplay import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="panelyra-settings-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "panelyra/settings.json"

    def test_missing_preferences_use_defaults_without_an_error(self):
        value, error = settings.load(self.path)
        self.assertEqual(value, settings.Settings())
        self.assertIsNone(error)
        self.assertFalse(self.path.exists())

    def test_valid_preferences_round_trip_as_private_atomic_file(self):
        value = settings.Settings(width=1280, height=800, fps=20, capture_fps=60,
                                  quality=20, transport="adb", auto_connect=False, language="ru",
                                  apk_path="/tmp/Планшет/app.apk", apk_port=9080,
                                  apk_duration=900, apk_interface="enx123456789abc")
        settings.save(value, self.path)
        loaded, error = settings.load(self.path)
        self.assertEqual(loaded, value)
        self.assertIsNone(error)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(list(self.path.parent.glob(".settings-*")), [])
        # The old inode stays readable until the completed replacement is installed.
        with self.path.open() as old:
            updated = replace(value, quality=14)
            settings.save(updated, self.path)
            self.assertEqual(json.load(old)["quality"], 20)
        self.assertEqual(settings.load(self.path), (updated, None))

    def test_failed_atomic_replace_preserves_last_good_preferences(self):
        original = settings.Settings(auto_connect=False)
        settings.save(original, self.path)
        before = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                settings.save(replace(original, quality=12), self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(settings.load(self.path), (original, None))
        self.assertEqual(list(self.path.parent.glob(".settings-*")), [])

    def test_bad_json_and_wrong_top_level_types_fall_back_with_notice(self):
        self.path.parent.mkdir()
        for payload in ("{", "[]", "null", '"text"', "42", "true"):
            with self.subTest(payload=payload):
                self.path.write_text(payload)
                value, error = settings.load(self.path)
                self.assertEqual(value, settings.Settings())
                self.assertTrue(error)
                self.assertEqual(self.path.read_text(), payload)

    def test_invalid_types_and_bounds_are_rejected_before_writing(self):
        original = settings.Settings()
        settings.save(original, self.path)
        before = self.path.read_bytes()
        invalid = {
            "width": [True, "1600", 1600.0, 159, 1601, 2562],
            "height": [None, False, [], 0, 999, 3000],
            "fps": [True, "30", 4, 61],
            "capture_fps": [False, 60.0, 29, 61],
            "quality": [False, "18", 9, 36],
            "transport": ["wifi", 1, None],
            "auto_connect": ["false", 0, 1, None],
            "language": ["de", False, None],
            "apk_path": [None, 123, "bad\0.apk", "x" * 4097],
            "apk_port": [True, "8765", 1023, 65536],
            "apk_duration": [False, "1800", 0, 86401],
            "apk_interface": [None, 123, "name with spaces", "path/name", "x\0"],
        }
        for name, values in invalid.items():
            for value in values:
                with self.subTest(field=name, value=value):
                    with self.assertRaises(ValueError):
                        settings.save(replace(original, **{name: value}), self.path)
                    self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_loaded_values_warn_and_do_not_partially_apply(self):
        self.path.parent.mkdir()
        for wrong in ("30", True, 0, 61):
            with self.subTest(fps=wrong):
                self.path.write_text(json.dumps({"width": 1280, "fps": wrong}))
                loaded, error = settings.load(self.path)
                self.assertEqual(loaded, settings.Settings())
                self.assertTrue(error)

    def test_unknown_future_fields_do_not_break_known_preferences(self):
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"width": 1280, "height": 800, "fps": 20,
                                         "capture_fps": 60, "quality": 19,
                                         "future_codec": {"name": "something-new"},
                                         "schema_version": 99}))
        loaded, error = settings.load(self.path)
        self.assertIsNone(error)
        self.assertEqual((loaded.width, loaded.height, loaded.fps, loaded.quality),
                         (1280, 800, 20, 19))

    def test_boundary_modes_and_presets_make_valid_cli_arguments(self):
        modes = [settings.Settings(width=160, height=160, fps=5, capture_fps=5, quality=10),
                 settings.Settings(width=2560, height=2560, fps=60, capture_fps=60, quality=35)]
        modes.extend(settings.Settings(**values) for values in settings.PRESETS.values())
        from usbdisplay.__main__ import build_parser
        for value in modes:
            with self.subTest(mode=value):
                value.validate()
                args = build_parser().parse_args(value.command_args())
                self.assertEqual((args.width, args.height, args.fps, args.capture_fps, args.quality),
                                 (value.width, value.height, value.fps, value.capture_fps, value.quality))
                self.assertEqual(args.transport, value.transport)

    def test_config_uses_explicit_xdg_location(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temporary.name}):
            self.assertEqual(settings.config_path(), self.path)


if __name__ == "__main__":
    unittest.main()

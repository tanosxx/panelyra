"""Live language changes use the existing GTK window without touching a stream.

These tests are skipped on CLI-only systems without GTK or a display. All
preferences and device/backend entry points are mocked; no tablet is needed.
"""

from dataclasses import replace
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from usbdisplay.devices import Device
from usbdisplay.settings import Settings

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from usbdisplay import launcher
    from gtk_support import get_test_application
except (ImportError, ValueError):
    Gtk = None


def descendants(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from descendants(child)


@unittest.skipIf(Gtk is None, "GTK 3 is not installed")
class LauncherLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = get_test_application()

    def setUp(self):
        self.saved = self.patch("save")
        self.loaded = self.patch("load", return_value=(Settings(language="en"), None))
        self.notifications = self.patch("NotificationCenter")
        self.notifications.return_value.button = Gtk.MenuButton()
        self.patch("discover_devices", side_effect=AssertionError("Unexpected device discovery"))
        self.patch("subprocess.Popen", side_effect=AssertionError("Unexpected backend start"))

    def patch(self, name, **kwargs):
        mock = patch("usbdisplay.launcher." + name, **kwargs)
        value = mock.start()
        self.addCleanup(mock.stop)
        return value

    def make_app(self, settings=None):
        if settings is not None:
            self.loaded.return_value = (settings, None)
        app = launcher.Launcher(auto_connect=False)
        window_type = Gtk.ApplicationWindow
        with patch.object(Gtk, "ApplicationWindow", side_effect=lambda **kwargs:
                          window_type(**{**kwargs, "application": self.host})):
            app.build_window()
        self.addCleanup(app.window.destroy)
        return app

    def make_process(self):
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, 'rb', buffering=0)
        writer = os.fdopen(write_fd, 'wb', buffering=0)
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        return SimpleNamespace(stdin=writer), reader

    @staticmethod
    def labels(widget):
        return [item.get_text() for item in descendants(widget) if isinstance(item, Gtk.Label)]

    @staticmethod
    def combo_text(combo, item_id):
        return next(row[0] for row in combo.get_model() if row[1] == item_id)

    def test_language_changes_all_visible_sections_and_returns_to_system_language(self):
        app = self.make_app()
        app.on_devices([], None)
        with patch.dict(os.environ, {"LANGUAGE": "ru_RU:en"}):
            for index, language in enumerate(("ru", "en", "auto"), 1):
                with self.subTest(language=language):
                    app.language.set_active_id(language)
                    russian = language != "en"
                    self.assertEqual(app.russian, russian)
                    self.assertEqual(app.start_button.get_label(),
                                     "Подключить планшет" if russian else "Connect tablet")
                    self.assertEqual(app.window.get_titlebar().get_subtitle(),
                                     "Вторая жизнь. Второй экран." if russian
                                     else "A second life. A second screen.")
                    labels = self.labels(app.window)
                    self.assertIn("Место для новой идеи." if russian else "Room for one more idea.", labels)
                    self.assertIn("Разрешение" if russian else "Resolution", labels)
                    self.assertIn("На ПК язык меняется сразу.\nНа планшете — при подключении." if russian else
                                  "Changes immediately on this PC.\nThe tablet follows when connected.", labels)
                    self.assertEqual(app.details.get_label(),
                                     "Журнал подключения" if russian else "Connection log")
                    self.assertEqual(app.autoconnect.get_label(),
                                     "Подключать при запуске" if russian else "Connect when opened")
                    self.assertIn("Меньше QP" if russian else "Lower QP", app.quality.get_tooltip_text())
                    self.assertEqual(self.combo_text(app.transport, "auto"),
                                     "Автоматически" if russian else "Automatic")
                    self.assertIn("эксперимент" if russian else "experimental",
                                  self.combo_text(app.fps, "60"))
                    self.assertEqual(self.saved.call_count, index)
                    self.assertEqual(self.saved.call_args.args[0].language, language)
                    self.assertFalse(app.updating)

    def test_translation_preserves_custom_values_and_combo_ids_without_recursive_saves(self):
        original = Settings(width=1440, height=900, fps=24, capture_fps=48,
                            quality=19, transport="auto", auto_connect=False, language="en")
        app = self.make_app(original)
        for language in ("ru", "en", "ru"):
            with self.subTest(language=language):
                before = self.saved.call_count
                app.language.set_active_id(language)
                self.assertEqual(self.saved.call_count, before + 1)
                self.assertEqual(app.values(), replace(original, language=language))
                self.assertEqual(app.profile.get_active_id(), "custom")
                self.assertEqual(app.transport.get_active_id(), "auto")
                self.assertEqual(app.fps.get_active_id(), "24")
                self.assertEqual(app.fps.get_active_text(), "24 fps")
                self.assertEqual(len(app.fps.get_model()), 5)

    def test_window_expanders_log_and_existing_controls_survive_a_language_change(self):
        app = self.make_app()
        window, width, log = app.window, app.width, app.log
        expanders = [widget for widget in descendants(window) if isinstance(widget, Gtk.Expander)]
        for expander in expanders:
            expander.set_expanded(True)
        app.append_log("backend diagnostic: retain this exact text")
        app.language.set_active_id("ru")
        self.assertIs(app.window, window)
        self.assertIs(app.width, width)
        self.assertIs(app.log, log)
        self.assertTrue(all(expander.get_expanded() for expander in expanders))
        self.assertEqual(log.get_text(log.get_start_iter(), log.get_end_iter(), False),
                         "backend diagnostic: retain this exact text")

    def test_active_stream_status_and_control_state_are_preserved(self):
        app = self.make_app()
        process, reader = self.make_process()
        app.process = process
        app.set_busy(True)
        app.on_output(process, '@panelyra {"event": "ready"}')
        app.language.set_active_id("ru")
        self.assertIs(app.process, process)
        self.assertEqual(app.status_kind, "connected")
        self.assertEqual(app.status.get_text(), "Подключено. Перетащите окно на новый экран.")
        self.assertEqual(app.badge.get_text(), "ПОДКЛЮЧЕНО")
        self.assertFalse(app.start_button.get_sensitive())
        self.assertTrue(app.stop_button.get_sensitive())
        self.assertFalse(app.width.get_sensitive())
        self.assertTrue(app.language.get_sensitive())
        self.assertFalse(app.stopped)
        self.assertEqual(json.loads(reader.read()), {"language": "ru"})

    def test_start_passes_resolved_pc_language_and_opens_live_control_pipe(self):
        app = self.make_app(Settings(language="auto"))
        process, _ = self.make_process()
        with patch.dict(os.environ, {"LANGUAGE": "ru_RU:en"}), \
                patch.object(launcher, "running_stream", return_value=None), \
                patch.object(launcher.subprocess, "Popen", return_value=process) as popen, \
                patch.object(launcher.threading, "Thread"):
            app.start()
        arguments = popen.call_args.args[0]
        self.assertEqual(arguments[arguments.index('--language') + 1], 'ru')
        self.assertIn('--control-stdin', arguments)
        self.assertEqual(popen.call_args.kwargs['stdin'], launcher.subprocess.PIPE)
        self.assertFalse(os.get_blocking(process.stdin.fileno()))

    def test_busy_control_pipe_retries_only_latest_language_without_blocking_ui(self):
        app = self.make_app()
        app.process, reader = self.make_process()
        while True:
            try:
                os.write(app.process.stdin.fileno(), b'x' * 4096)
            except BlockingIOError:
                break
        with patch.object(launcher.GLib, "timeout_add", return_value=123) as timer:
            app.language.set_active_id('ru')
            app.language.set_active_id('en')
            timer.assert_called_once()
        self.assertEqual(app.pending_language, 'en')
        reader.read()
        self.assertFalse(app.send_pending_language())
        self.assertEqual(json.loads(reader.read()), {'language': 'en'})
        self.assertIsNone(app.pending_language)
        self.assertIsNone(app.language_timer)

    def test_failed_language_control_keeps_video_connected_and_closes_on_finish(self):
        app = self.make_app()
        process, _ = self.make_process()
        app.process = process
        app.on_output(process, '@panelyra {"event": "ready"}')
        process.stdin.close()
        app.language.set_active_id('ru')
        self.assertEqual(app.status_kind, 'connected')
        self.assertIs(app.process, process)
        self.assertTrue(app.russian)
        self.assertIn('Передача языка недоступна', app.lines[-1])
        app.on_output(process, '@panelyra {"event": "language", "language": "ru", "synced": false}')
        self.assertEqual(app.status_kind, 'connected')
        self.assertIsNone(app.last_error)
        self.assertFalse(app.on_finished(process, 0))
        self.assertIsNone(app.process)

    def test_external_stream_remains_connected_without_claiming_settings_ready(self):
        app = self.make_app()
        with patch.object(launcher, "running_stream", return_value=321), \
                patch.object(launcher.GLib, "timeout_add_seconds", return_value=123):
            app.start()
        app.language.set_active_id("ru")
        self.assertIsNone(app.process)
        self.assertEqual(app.external_timer, 123)
        self.assertEqual(app.status_kind, "connected")
        self.assertIn("Передача уже работает", app.status.get_text())
        self.assertEqual(app.badge.get_text(), "ПОДКЛЮЧЕНО")
        self.assertFalse(app.start_button.get_sensitive())
        self.assertFalse(app.stop_button.get_sensitive())
        self.assertTrue(app.language.get_sensitive())

    def test_cached_device_translates_without_starting_another_scan_or_growing_bindings(self):
        app = self.make_app()
        device = Device("Lenovo TB-X606X", "usb", "usb-ready", "enx-test", "192.168.42.129")
        app.on_devices([device], None)
        bindings = len(app.translations)
        app.device_scan_running = True
        with patch.object(launcher.threading, "Thread") as thread:
            for language in ("ru", "en") * 5:
                app.language.set_active_id(language)
                labels = self.labels(app.device_rows)
                self.assertIn("Lenovo TB-X606X", labels)
                self.assertIn("192.168.42.129", labels)
                self.assertIn("USB-модем включён" if language == "ru" else "USB tethering enabled", labels)
                self.assertTrue(app.device_scan_running)
                self.assertEqual(len(app.translations), bindings)
                app.refresh_devices()
            thread.assert_not_called()
        app.on_devices([], None)
        self.assertFalse(app.device_scan_running)
        self.assertIn("No tablet connected", self.labels(app.device_rows))
        app.language.set_active_id("ru")
        self.assertIn("Планшет не подключён", self.labels(app.device_rows))
        app.on_devices([], "test-only diagnostic")
        app.language.set_active_id("en")
        self.assertIn("Device check unavailable", self.labels(app.device_rows))
        self.assertEqual(app.device_rows.get_tooltip_text(), "test-only diagnostic")
        self.assertEqual(len(app.translations), bindings)

    def test_failed_save_reports_error_without_claiming_language_was_saved(self):
        app = self.make_app()
        self.saved.side_effect = OSError("test preferences are read-only")
        app.language.set_active_id("ru")
        self.assertEqual(self.saved.call_count, 1)
        self.assertEqual(app.settings.language, "en")
        self.assertEqual(app.language.get_active_id(), "en")
        self.assertFalse(app.russian)
        self.assertEqual(app.status_kind, "error")
        self.assertIn("test preferences are read-only", app.status.get_text())
        self.assertNotIn("Your settings are saved", app.status.get_text())

    def test_invalid_video_draft_does_not_block_language_or_overwrite_saved_mode(self):
        app = self.make_app()
        # The user can be midway through changing capture/output frame rates.
        app.capture.set_value(15)
        self.assertEqual(app.status_kind, "error")
        self.saved.reset_mock()
        app.language.set_active_id("ru")
        self.saved.assert_called_once_with(Settings(language="ru"))
        self.assertTrue(app.russian)
        self.assertEqual(app.capture.get_value_as_int(), 15)
        self.assertEqual(app.settings.capture_fps, 60)
        self.assertEqual(app.status_kind, "error")
        self.assertTrue(app.status.get_text().startswith("Проверьте настройки:"))
        self.assertIn("Проверяем USB-устройства…", self.labels(app.device_rows))

    def test_apk_settings_and_video_settings_preserve_each_other(self):
        app = self.make_app()
        app.save_apk_settings(apk_path='/tmp/new.apk', apk_port=9080, apk_duration=900,
                              apk_interface='usb0')
        self.assertEqual(app.settings.width, 1600)
        app.width.set_value(1280)
        app.language.set_active_id('ru')
        self.assertEqual(app.settings.apk_path, '/tmp/new.apk')
        self.assertEqual(app.settings.apk_port, 9080)
        self.assertEqual(app.settings.apk_duration, 900)
        self.assertEqual(app.settings.apk_interface, 'usb0')
        self.assertEqual(app.settings.width, 1280)
        self.assertEqual(app.settings.language, 'ru')
        app.capture.set_value(15)  # An incomplete video draft must not block APK setup.
        app.save_apk_settings(apk_port=9081)
        self.assertEqual(app.settings.capture_fps, 60)
        self.assertEqual(app.settings.apk_port, 9081)

    def test_long_content_and_expanded_sections_fit_both_compact_pages(self):
        app = self.make_app()
        app.pages.set_transition_duration(0)
        # These widgets are measured without presenting a real desktop window.
        body = app.window.get_child()
        body.show_all()
        app.on_devices([Device("Tablet-" + "X" * 240, "usb", "usb-ready",
                               "usb-" + "x" * 120, "192.0.2.2")], None)
        app.internet.reason = "restore-conflict"
        for widget in descendants(body):
            if isinstance(widget, Gtk.Expander):
                widget.set_expanded(True)
        for language in ("ru", "en"):
            app.language.set_active_id(language)
            app.internet.render()
            for page in ("connection", "settings"):
                with self.subTest(language=language, page=page):
                    app.pages.set_visible_child_name(page)
                    minimum_width, _ = body.get_preferred_width()
                    self.assertGreater(minimum_width, 0)
                    self.assertLessEqual(minimum_width, 780)
                    # Even expanded sections must leave room for the page's
                    # own vertical scrolling instead of growing the window.
                    minimum_height, _ = body.get_preferred_height_for_width(780)
                    self.assertLessEqual(minimum_height, 760)

    def test_stream_controls_stay_outside_pages_and_work_on_settings(self):
        app = self.make_app()
        body = app.window.get_child()
        body.show_all()
        process, _ = self.make_process()
        app.process = process
        app.set_busy(True)
        app.on_output(process, '@panelyra {"event": "ready"}')
        app.internet.state = SimpleNamespace(enabled=False, active_path="/test/active")
        app.internet.render()
        for page in ("settings", "connection", "settings"):
            with self.subTest(page=page):
                app.pages.set_visible_child_name(page)
                for control in (app.status, app.start_button, app.stop_button):
                    self.assertTrue(control.get_visible())
                    self.assertTrue(control.is_ancestor(body))
                    self.assertFalse(control.is_ancestor(app.pages))
                self.assertIs(app.process, process)
                self.assertEqual(app.status_kind, "connected")
                self.assertFalse(app.start_button.get_sensitive())
                self.assertTrue(app.stop_button.get_sensitive())
                self.assertFalse(app.width.get_sensitive())
                self.assertTrue(app.language.get_sensitive())
                self.assertTrue(app.internet.button.get_sensitive())

    def test_tab_titles_translate_without_resetting_active_page_or_preferences(self):
        app = self.make_app()
        app.window.get_child().show_all()
        app.pages.set_visible_child_name("settings")
        original = app.values()
        for language, titles in (("ru", ("Подключение", "Настройки")),
                                 ("en", ("Connection", "Settings"))):
            with self.subTest(language=language):
                app.language.set_active_id(language)
                self.assertEqual(app.pages.get_visible_child_name(), "settings")
                for page, title in zip(("connection", "settings"), titles):
                    child = app.pages.get_child_by_name(page)
                    self.assertEqual(app.pages.child_get_property(child, "title"), title)
                self.assertEqual(app.values(), replace(original, language=language))


if __name__ == "__main__":
    unittest.main()

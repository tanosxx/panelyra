"""Appearance previews preserve the running launcher and its unsaved form state.

GTK widgets are measured without presenting a window. Preferences, discovery,
NetworkManager and backend creation are isolated from the user's session.
"""

from dataclasses import replace
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from usbdisplay.devices import Device
from usbdisplay.settings import Settings
from usbdisplay.themes import THEME_IDS, THEMES

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
class LauncherAppearanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = get_test_application()

    def setUp(self):
        self.saved = self.patch("usbdisplay.launcher.save")
        self.loaded = self.patch("usbdisplay.launcher.load", return_value=(Settings(language="en"), None))
        self.notifications = self.patch("usbdisplay.launcher.NotificationCenter")
        self.notifications.side_effect = lambda _app: Mock(button=Gtk.MenuButton(), popover=Gtk.Popover())
        self.discovery = self.patch("usbdisplay.launcher.discover_devices",
                                    side_effect=AssertionError("Unexpected device discovery"))
        self.patch("usbdisplay.launcher.subprocess.Popen",
                   side_effect=AssertionError("Unexpected backend creation"))
        self.network = self.patch("usbdisplay.internet.InternetController")
        self.network.return_value.inspect.side_effect = AssertionError("Unexpected network inspection")
        self.network.return_value.set_enabled.side_effect = AssertionError("Unexpected network change")

    def patch(self, name, **kwargs):
        mock = patch(name, **kwargs)
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
        app.window.get_child().show_all()
        app.pages.set_transition_duration(0)
        return app

    def make_process(self):
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb", buffering=0)
        writer = os.fdopen(write_fd, "wb", buffering=0)
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        return SimpleNamespace(stdin=writer), reader

    def test_repeated_previews_keep_active_stream_widgets_log_drafts_and_page(self):
        app = self.make_app()
        app.capture.set_value(15)  # An unfinished output/capture FPS edit.
        app.append_log("existing stream diagnostic")
        app.details.set_expanded(True)
        app.pages.set_visible_child_name("appearance")
        process, reader = self.make_process()
        app.process = process
        app.set_busy(True)
        app.on_output(process, '@panelyra {"event": "ready"}')
        writer = process.stdin
        original = app.settings
        identities = {name: getattr(app, name) for name in (
            "window", "pages", "width", "capture", "log", "device_rows", "details",
            "start_button", "stop_button", "internet", "downloads", "appearance")}
        self.saved.reset_mock()

        for theme_id in THEME_IDS[1:] + THEME_IDS + THEME_IDS[1:]:
            with self.subTest(theme=theme_id):
                app.appearance.select(theme_id)
                self.assertEqual(app.visual_theme, theme_id)
                self.assertIs(app.process, process)
                self.assertIs(process.stdin, writer)
                self.assertFalse(writer.closed)
                self.assertIsNone(reader.read())
                self.assertEqual(app.settings, original)
                self.assertEqual(app.pages.get_visible_child_name(), "appearance")
                self.assertEqual(app.capture.get_value_as_int(), 15)
                self.assertEqual(app.status_kind, "connected")
                self.assertTrue(app.details.get_expanded())
                self.assertFalse(app.width.get_sensitive())
                self.assertFalse(app.start_button.get_sensitive())
                self.assertTrue(app.stop_button.get_sensitive())
                self.assertTrue(app.language.get_sensitive())
                for tile in app.appearance.tiles.values():
                    self.assertTrue(tile.button.get_sensitive())
                self.assertEqual(app.appearance.apply_button.get_sensitive(), theme_id != original.theme)
                for name, widget in identities.items():
                    self.assertIs(getattr(app, name), widget)
                self.assertEqual(app.log.get_text(app.log.get_start_iter(), app.log.get_end_iter(), False),
                                 "existing stream diagnostic")
        self.saved.assert_not_called()
        self.network.return_value.inspect.assert_not_called()
        self.network.return_value.set_enabled.assert_not_called()

    def test_shared_main_editors_survive_page_changes_without_saving_or_duplicate_callbacks(self):
        app = self.make_app()
        # An invalid capture/output combination is intentionally an unsaved draft.
        app.capture.set_value(15)
        app.width.set_value(1410)
        app.quality.set_value(26)
        app.picture_extra.set_expanded(True)
        app.set_busy(True)
        controls = {name: getattr(app, name) for name in (
            'picture_card', 'profile', 'width', 'height', 'fps', 'quality', 'capture', 'picture_extra')}
        internet_widget = app.internet.widget
        saved_values = app.settings
        self.saved.reset_mock()

        for theme in THEME_IDS:
            with self.subTest(theme=theme):
                # Seed the persisted choice without writing preferences; navigation
                # away from Appearance correctly returns to this selected design.
                app.settings = replace(saved_values, theme=theme)
                app.preview_theme(theme)
                for _ in range(3):
                    for page in ('settings', 'connection', 'appearance', 'connection'):
                        app.pages.set_visible_child_name(page)
                        if page == 'appearance':
                            app.appearance.select('aurora' if theme != 'aurora' else 'midnight')
                            app.appearance.cancel()
                        if page == 'settings' or theme == 'classic':
                            self.assertIs(app.picture_card.get_parent(), app.settings_body)
                            self.assertIs(internet_widget.get_parent(), app.settings_connection)
                        else:
                            main_widgets = list(descendants(app.connection_grid))
                            self.assertEqual(main_widgets.count(app.picture_card), 1)
                            self.assertEqual(main_widgets.count(internet_widget), 1)
                        for name, widget in controls.items():
                            self.assertIs(getattr(app, name), widget)
                        self.assertIs(app.internet.widget, internet_widget)
                        self.assertEqual(app.capture.get_value_as_int(), 15)
                        self.assertEqual(app.width.get_value_as_int(), 1410)
                        self.assertEqual(app.quality.get_value(), 26)
                        self.assertTrue(app.picture_extra.get_expanded())
                        for name in ('profile', 'width', 'height', 'fps', 'quality', 'capture'):
                            self.assertFalse(controls[name].get_sensitive())
                        self.assertEqual(app.settings, replace(saved_values, theme=theme))
        self.saved.assert_not_called()
        self.discovery.assert_not_called()
        self.network.return_value.inspect.assert_not_called()
        self.network.return_value.set_enabled.assert_not_called()

        # Repairing the draft and making one edit still invoke one save each,
        # regardless of how many times the actual GTK controls were reparented.
        app.set_busy(False)
        app.capture.set_value(30)
        self.saved.assert_called_once()
        self.saved.reset_mock()
        app.quality.set_value(27)
        self.saved.assert_called_once()
        self.assertEqual(self.saved.call_args.args[0].quality, 27)
        self.assertEqual(self.saved.call_args.args[0].width, 1410)

    def test_applying_design_keeps_stream_and_control_pipe_running(self):
        app = self.make_app()
        process, reader = self.make_process()
        app.process = process
        app.set_busy(True)
        app.on_output(process, '@panelyra {"event": "ready"}')
        app.pages.set_visible_child_name("appearance")
        app.appearance.select("midnight")
        app.appearance.apply_button.clicked()
        self.saved.assert_called_once_with(Settings(language="en", theme="midnight"))
        self.assertIs(app.process, process)
        self.assertFalse(process.stdin.closed)
        self.assertEqual(json.loads(reader.read()), {"theme": "midnight"})
        self.assertFalse(app.stopped)
        self.assertEqual(app.status_kind, "connected")
        self.assertFalse(app.start_button.get_sensitive())
        self.assertFalse(app.capture.get_sensitive())
        self.assertTrue(app.stop_button.get_sensitive())
        app.pages.set_visible_child_name("connection")
        self.assertEqual(app.visual_theme, "midnight")

    def test_busy_pipe_retries_only_latest_applied_theme_not_a_preview(self):
        app = self.make_app()
        app.process, reader = self.make_process()
        app.pages.set_visible_child_name("appearance")
        while True:
            try:
                os.write(app.process.stdin.fileno(), b'x' * 4096)
            except BlockingIOError:
                break
        with patch.object(launcher.GLib, "timeout_add", return_value=123) as timer:
            app.appearance.select("midnight")
            app.appearance.apply()
            app.appearance.select("aurora")
            app.appearance.apply()
            app.appearance.select("editorial")  # Unsaved preview stays local.
            timer.assert_called_once()
        self.assertEqual(app.pending_theme, "aurora")
        reader.read()
        self.assertFalse(app.send_pending_theme())
        self.assertEqual(json.loads(reader.read()), {"theme": "aurora"})
        self.assertIsNone(app.pending_theme)
        self.assertIsNone(app.theme_timer)
        app.appearance.cancel()
        self.assertIsNone(reader.read())

    def test_closed_theme_pipe_keeps_video_and_saved_style(self):
        app = self.make_app()
        process, _ = self.make_process()
        app.process = process
        app.on_output(process, '@panelyra {"event": "ready"}')
        process.stdin.close()
        app.appearance.select("graphite")
        app.appearance.apply()
        self.assertEqual(app.settings.theme, "graphite")
        self.assertEqual(app.appearance.message, "saved")
        self.assertIs(app.process, process)
        self.assertFalse(app.stopped)
        self.assertEqual(app.status_kind, "connected")
        self.assertIn("Reconnect", app.lines[-1])
        self.assertIsNone(app.pending_theme)

    def test_finishing_stream_discards_pending_theme_and_retry(self):
        app = self.make_app()
        process, _ = self.make_process()
        app.process = process
        app.pending_theme = "aurora"
        app.theme_timer = 123
        with patch.object(launcher.GLib, "source_remove") as remove:
            app.on_finished(process, 0)
        remove.assert_called_once_with(123)
        self.assertIsNone(app.theme_timer)
        self.assertIsNone(app.pending_theme)
        self.assertIsNone(app.process)

    def test_apply_saves_only_theme_when_video_draft_is_invalid_and_survives_relaunch(self):
        original = Settings(language="ru", transport="adb", auto_connect=False,
                            apk_path="/tmp/keep.apk", apk_port=9080, apk_duration=900)
        app = self.make_app(original)
        app.capture.set_value(15)
        self.assertEqual(app.status_kind, "error")
        self.saved.reset_mock()
        app.pages.set_visible_child_name("appearance")
        app.appearance.select("aurora")
        app.appearance.apply_button.clicked()

        updated = replace(original, theme="aurora")
        self.saved.assert_called_once_with(updated)
        self.assertEqual(app.settings, updated)
        self.assertEqual(app.capture.get_value_as_int(), 15)
        self.assertEqual(app.status_kind, "error")
        self.assertFalse(app.appearance.apply_button.get_sensitive())
        app.pages.set_visible_child_name("connection")
        self.assertEqual(app.visual_theme, "aurora")

        restarted = self.make_app(updated)
        self.assertEqual(restarted.settings, updated)
        self.assertEqual(restarted.visual_theme, "aurora")
        self.assertEqual(restarted.appearance.selected_theme, "aurora")
        self.assertTrue(restarted.window.get_style_context().has_class("theme-aurora"))

    def test_failed_apply_retains_saved_settings_and_cancel_restores_design(self):
        original = Settings(language="en", theme="light")
        app = self.make_app(original)
        app.process, reader = self.make_process()
        app.pages.set_visible_child_name("appearance")
        app.appearance.select("midnight")
        self.saved.side_effect = OSError("test settings directory is read-only")
        app.appearance.apply_button.clicked()
        self.assertEqual(app.settings, original)
        self.assertEqual(app.visual_theme, "midnight")
        self.assertEqual(app.appearance.message, "error")
        self.assertIn("Could not save", app.appearance.status.get_text())
        self.assertTrue(app.appearance.cancel_button.get_sensitive())
        app.appearance.cancel_button.clicked()
        self.assertEqual(app.settings, original)
        self.assertEqual(app.visual_theme, "light")
        self.assertEqual(app.appearance.selected_theme, "light")
        self.assertIsNone(app.appearance.message)
        self.saved.assert_called_once_with(replace(original, theme="midnight"))
        self.assertIsNone(reader.read())

    def test_leaving_appearance_cancels_preview_without_saving_or_changing_destination(self):
        app = self.make_app(Settings(language="en", theme="editorial"))
        for destination in ("settings", "connection"):
            with self.subTest(destination=destination):
                app.pages.set_visible_child_name("appearance")
                app.appearance.select("graphite")
                self.assertEqual(app.visual_theme, "graphite")
                app.pages.set_visible_child_name(destination)
                self.assertEqual(app.pages.get_visible_child_name(), destination)
                self.assertEqual(app.visual_theme, "editorial")
                self.assertEqual(app.appearance.selected_theme, "editorial")
                self.assertEqual(app.settings.theme, "editorial")
        self.saved.assert_not_called()

    def test_all_designs_and_languages_fit_narrow_window_with_long_content(self):
        app = self.make_app()
        body = app.window.get_child()
        app.on_devices([Device("Tablet-" + "X" * 240, "usb", "usb-ready",
                               "usb-" + "x" * 120, "192.0.2.2")], None)
        app.internet.reason = "restore-conflict"
        for widget in descendants(body):
            if isinstance(widget, Gtk.Expander):
                widget.set_expanded(True)
        for language in ("ru", "en"):
            app.language.set_active_id(language)
            app.internet.render()
            for theme_id in THEME_IDS:
                # Exercise the compact layout regardless of the host monitor.
                with patch.object(app.window, "get_allocated_width", return_value=700):
                    app.preview_theme(theme_id)
                self.assertFalse(app.sidebar.get_visible())
                for page in ("connection", "settings", "appearance"):
                    with self.subTest(language=language, theme=theme_id, page=page):
                        app.pages.set_visible_child_name(page)
                        minimum_width, _ = body.get_preferred_width()
                        self.assertGreater(minimum_width, 0)
                        self.assertLessEqual(minimum_width, 780)
                        minimum_height, _ = body.get_preferred_height_for_width(780)
                        self.assertLessEqual(minimum_height, 760)

    def test_language_changes_translate_themes_and_pages_without_resetting_preview(self):
        app = self.make_app()
        app.pages.set_visible_child_name("appearance")
        app.appearance.select("graphite")
        original_width = app.width
        for language, titles in (("ru", ("Подключение", "Настройки", "Оформление")),
                                 ("en", ("Connection", "Settings", "Appearance"))):
            with self.subTest(language=language):
                app.language.set_active_id(language)
                self.assertEqual(app.pages.get_visible_child_name(), "appearance")
                self.assertEqual(app.visual_theme, "graphite")
                self.assertEqual(app.appearance.selected_theme, "graphite")
                self.assertEqual(app.settings.theme, "classic")
                self.assertIs(app.width, original_width)
                for page, title in zip(("connection", "settings", "appearance"), titles):
                    child = app.pages.get_child_by_name(page)
                    self.assertEqual(app.pages.child_get_property(child, "title"), title)
                for theme_id, tile in app.appearance.tiles.items():
                    self.assertEqual(tile.name.get_text(), THEMES[theme_id].name[language == "ru"])
                self.assertEqual(app.appearance.interface_preview.language, language)
                self.assertEqual(app.appearance.lock_preview.language, language)
                self.assertEqual(app.appearance.apply_button.get_label(),
                                 "Применить" if language == "ru" else "Apply")
                self.assertEqual(app.hero_title.get_text(),
                                 "Panelyra · USB-экран" if language == "ru" else "Panelyra · USB display")
        self.assertTrue(all(call.args[0].theme == "classic" for call in self.saved.call_args_list))


if __name__ == "__main__":
    unittest.main()

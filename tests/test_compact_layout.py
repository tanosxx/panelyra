"""Measure allocated GTK content, including the real header and action footer.

The offscreen host never activates Panelyra or touches devices/preferences.
Unlike minimum-size checks, scroll adjustments detect a clipped main page.
"""

from dataclasses import replace
import time
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import Mock, patch

from usbdisplay.devices import Device
from usbdisplay.settings import Settings
from usbdisplay.themes import THEME_IDS

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    from usbdisplay import launcher
except (ImportError, ValueError):
    Gtk = None


if Gtk is not None:
    class OffscreenWindow(Gtk.OffscreenWindow):
        def set_titlebar(self, header):
            self.header = header


def settle():
    deadline = time.monotonic() + .055
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        while time.monotonic() < deadline:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(.002)


@unittest.skipIf(Gtk is None, 'GTK 3 is not installed')
class CompactLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check(None)[0]:
            raise unittest.SkipTest('No GTK display is available')

    def setUp(self):
        self.patches = []
        def isolated(name, **kwargs):
            guard = patch(name, **kwargs)
            self.patches.append(guard)
            return guard.start()
        forbidden = AssertionError('Layout verification must not access live services')
        self.saved = isolated('usbdisplay.launcher.save', side_effect=forbidden)
        isolated('usbdisplay.launcher.load', return_value=(Settings(language='en'), None))
        isolated('usbdisplay.launcher.WindowGeometry')
        isolated('usbdisplay.launcher.NotificationCenter', side_effect=lambda _app:
                 Mock(button=Gtk.MenuButton(), popover=Gtk.Popover()))
        isolated('usbdisplay.launcher.discover_devices', side_effect=forbidden)
        isolated('usbdisplay.launcher.subprocess.Popen', side_effect=forbidden)
        isolated('usbdisplay.internet.InternetController.inspect', side_effect=forbidden)
        isolated('usbdisplay.internet.InternetController.set_enabled', side_effect=forbidden)
        isolated('usbdisplay.download.DownloadServer.start', side_effect=forbidden)
        with patch.object(Gtk, 'ApplicationWindow', side_effect=lambda **_: OffscreenWindow()):
            self.app = launcher.Launcher(auto_connect=False)
            self.app.build_window()
        self.app.pages.set_transition_duration(0)
        window = self.app.window
        body = window.get_child()
        window.remove(body)
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        frame.pack_start(window.header, False, False, 0)
        frame.pack_start(body, True, True, 0)
        window.add(frame)
        self.app.internet.state = SimpleNamespace(enabled=False)
        self.app.internet.render()

    def tearDown(self):
        self.app.appearance.close()
        self.app.downloads.close()
        self.app.internet.close()
        self.app.window.destroy()
        for guard in reversed(self.patches):
            guard.stop()

    def size(self, width, height):
        window = self.app.window
        window.set_default_size(width, height)
        window.set_size_request(width, height)
        window.resize(width, height)
        window.show_all()
        settle()
        self.assertEqual((window.get_allocated_width(), window.get_allocated_height()), (width, height))

    def assert_fits(self):
        app = self.app
        scroll = app.pages.get_child_by_name('connection')
        adjustment = scroll.get_vadjustment()
        self.assertGreater(adjustment.get_page_size(), 100)
        self.assertLessEqual(adjustment.get_upper(), adjustment.get_page_size() + 1,
                             f'Content {adjustment.get_upper()} exceeds viewport {adjustment.get_page_size()}')
        for widget in (app.start_button, app.stop_button, app.details):
            self.assertTrue(widget.get_mapped())
            x, y = widget.translate_coordinates(app.window, 0, 0)
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + widget.get_allocated_width(), app.window.get_allocated_width())
            self.assertLessEqual(y + widget.get_allocated_height(), app.window.get_allocated_height())

    def bounds(self, widget):
        self.assertTrue(widget.get_mapped())
        x, y = widget.translate_coordinates(self.app.window, 0, 0)
        return x, y, x + widget.get_allocated_width(), y + widget.get_allocated_height()

    def assert_below(self, lower, upper):
        self.assertGreaterEqual(self.bounds(lower)[1], self.bounds(upper)[3])

    def assert_beside(self, left, right):
        first, second = self.bounds(left), self.bounds(right)
        self.assertLessEqual(first[2], second[0])
        self.assertLess(max(first[1], second[1]), min(first[3], second[3]))

    def test_main_page_fits_every_theme_language_and_normal_window_size(self):
        app = self.app
        for width, height in ((780, 760), (780, 680), (700, 700), (1100, 860)):
            self.size(width, height)
            for language in ('en', 'ru'):
                app.settings = replace(app.settings, language=language)
                app.update_language()
                for theme in THEME_IDS:
                    app.preview_theme(theme)
                    for count in (0, 1):
                        with self.subTest(size=(width, height), language=language, theme=theme, devices=count):
                            app.on_devices([Device('Android tablet', 'usb', 'usb-ready', 'usb0', '192.0.2.2')]
                                           if count else [], None)
                            app.set_status(('Connected. Move a window onto your new screen.',
                                            'Подключено. Перетащите окно на новый экран.')
                                           if count else ('Your next screen is one click away.',
                                                          'Ещё один экран — в одном нажатии.'),
                                           'connected' if count else 'ready')
                            settle()
                            self.assertEqual((app.window.get_allocated_width(), app.window.get_allocated_height()),
                                             (width, height), 'Changing the theme must not enlarge the window')
                            self.assert_fits()
                            self.assertEqual(app.sidebar.get_visible(), theme == 'midnight' and width >= 760)
        self.saved.assert_not_called()

    def test_default_window_preserves_each_design_composition(self):
        """A narrow window must retain the concepts, not collapse into recolored cards."""
        app = self.app
        self.size(780, 760)
        app.on_devices([Device('Android tablet', 'usb', 'usb-ready', 'usb0', '192.0.2.2')], None)
        for theme in THEME_IDS:
            with self.subTest(theme=theme):
                app.preview_theme(theme)
                settle()
                self.assertEqual((app.window.get_allocated_width(), app.window.get_allocated_height()), (780, 760))
                self.assert_fits()
                if theme == 'classic':
                    self.assert_below(app.device_card, app.hero)
                    self.assertFalse(app.picture_card.get_mapped())
                    continue
                for widget in (app.picture_card, app.internet.widget, app.profile, app.width, app.fps, app.quality):
                    self.assertTrue(widget.get_mapped(), 'The design must expose the real editable controls')
                if theme == 'light':
                    self.assertEqual(app.hero.get_halign(), Gtk.Align.CENTER)
                    self.assert_below(app.device_card, app.hero)
                    self.assert_below(app.picture_card, app.device_card)
                    self.assert_below(app.network_card, app.picture_card)
                elif theme == 'midnight':
                    self.assertTrue(app.sidebar.get_mapped())
                    self.assert_beside(app.sidebar, app.device_card)
                    self.assert_beside(app.device_card, app.picture_card)
                    self.assert_below(app.network_card, app.picture_card)
                    self.assertTrue(app.device_art.get_mapped())
                    self.assertEqual(app.device_art.variant, 'pair')
                elif theme == 'aurora':
                    self.assertTrue(app.connection_art.get_mapped())
                    self.assert_below(app.device_card, app.hero)
                    self.assert_below(app.picture_card, app.hero)
                    self.assert_beside(app.device_card, app.picture_card)
                    self.assert_below(app.network_card, app.device_card)
                elif theme == 'editorial':
                    self.assert_beside(app.hero, app.device_card)
                    self.assert_below(app.picture_card, app.hero)
                    self.assert_below(app.network_card, app.device_card)
                    self.assert_beside(app.picture_card, app.network_card)
                elif theme == 'graphite':
                    self.assertIsNone(app.hero.get_parent())
                    self.assert_beside(app.device_card, app.picture_card)
                    self.assert_below(app.network_card, app.picture_card)
                    self.assert_below(app.device_info, app.device_art)
                    self.assertEqual(app.device_art.variant, 'tablet')
        self.saved.assert_not_called()

    def test_appearance_choices_and_both_previews_fit_default_window(self):
        app = self.app
        for width, height in ((780, 760), (780, 680)):
            self.size(width, height)
            app.pages.set_visible_child_name('appearance')
            for language in ('en', 'ru'):
                app.settings = replace(app.settings, language=language)
                app.update_language()
                for theme in THEME_IDS:
                    app.appearance.select(theme)
                    for mode in ('interface', 'lock'):
                        with self.subTest(size=(width, height), language=language, theme=theme, mode=mode):
                            app.appearance.preview_stack.set_visible_child_name(mode)
                            settle()
                            self.assertEqual((app.window.get_allocated_width(), app.window.get_allocated_height()),
                                             (width, height), 'Appearance previews must not enlarge the window')
                            scroll = next(child for child in app.appearance_frame.get_children()
                                          if isinstance(child, Gtk.ScrolledWindow))
                            adjustment = scroll.get_vadjustment()
                            self.assertLessEqual(adjustment.get_upper(), adjustment.get_page_size() + 1)
                            for tile in app.appearance.tiles.values():
                                self.assertTrue(tile.button.get_mapped())
                            for button in (app.appearance.apply_button, app.appearance.cancel_button):
                                x, y = button.translate_coordinates(app.window, 0, 0)
                                self.assertLessEqual(x + button.get_allocated_width(), width)
                                self.assertLessEqual(y + button.get_allocated_height(), height)
        self.saved.assert_not_called()

    def test_expanded_help_and_log_can_scroll_without_losing_stream_actions(self):
        app = self.app
        self.size(780, 680)
        app.preview_theme('aurora')
        app.details.set_expanded(True)
        app.on_devices([Device('Android tablet', 'usb', 'usb-ready', 'usb0', '192.0.2.2')], None)
        app.set_status(('Reconnecting after unlock…', 'Переподключение после разблокировки…'), 'busy')
        settle()
        scroll = app.pages.get_child_by_name('connection')
        adjustment = scroll.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        adjustment.set_value(adjustment.get_upper())
        settle()
        for widget in (app.start_button, app.stop_button):
            self.assertTrue(widget.get_mapped())
            _, y = widget.translate_coordinates(app.window, 0, 0)
            self.assertLessEqual(y + widget.get_allocated_height(), 680)
        app.details.set_expanded(False)
        settle()
        self.assert_fits()


if __name__ == '__main__':
    unittest.main()

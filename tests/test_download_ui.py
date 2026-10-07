"""Download panel lifecycle uses fake USB/server workers, never a real tablet."""

from dataclasses import replace
import errno
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from usbdisplay.settings import Settings
from usbdisplay.network import UsbLink

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    from usbdisplay import download_ui
    from gtk_support import get_test_application
except (ImportError, ValueError):
    Gtk = None


class FakeServer:
    def __init__(self, apk, link, **options):
        self.apk, self.link, self.options = apk, link, options
        self.url = f'http://{link.local_ip}:{options["port"]}/'
        self.running = False
        self.stop_count = 0

    def start(self):
        self.running = True
        return self

    def stop(self):
        self.running = False
        self.stop_count += 1

    def event(self, name, **details):
        self.options['on_event'](name, **details)


@unittest.skipIf(Gtk is None, 'GTK 3 is not installed')
class DownloadPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = get_test_application()

    def setUp(self):
        self.tasks = []
        self.app = SimpleNamespace(settings=Settings(language='en'), russian=False)
        self.app.window = Gtk.ApplicationWindow(application=self.host)
        self.app.tr = lambda en, ru: ru if self.app.russian else en

        def save(**fields):
            self.app.settings = replace(self.app.settings, **fields)
        self.app.save_apk_settings = Mock(side_effect=save)
        self.patch('threading.Thread', side_effect=lambda target, **_: SimpleNamespace(
            start=lambda: self.tasks.append(target)))
        self.patch('GLib.idle_add', side_effect=lambda callback, *args: callback(*args))
        self.patch('download.default_apk', return_value=Path('/tmp/panelyra.apk'), create=True)
        self.servers = []

        def make_server(*args, **kwargs):
            server = FakeServer(*args, **kwargs)
            self.servers.append(server)
            return server
        self.factory = self.patch('download.DownloadServer', side_effect=make_server, create=True)
        self.link = UsbLink('usb0', '192.168.42.8', 24, '192.168.42.129')
        self.select = self.patch('network.select_link', return_value=self.link)
        self.discovery = self.patch('network.discover_links', return_value=[self.link])
        self.panel = download_ui.DownloadPanel(self.app)
        self.addCleanup(self.cleanup)

    def patch(self, name, **kwargs):
        patcher = patch('usbdisplay.download_ui.' + name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def run_tasks(self):
        while self.tasks:
            self.tasks.pop(0)()

    def cleanup(self):
        self.panel.close()
        self.run_tasks()
        self.app.window.destroy()

    def show(self):
        self.panel.present()
        self.run_tasks()

    def test_open_discovers_connections_but_does_not_start_server(self):
        self.discovery.assert_not_called()
        self.show()
        self.discovery.assert_called_once()
        self.factory.assert_not_called()
        self.assertEqual(self.panel.interfaces.get_active_id(), '')
        self.assertIn('usb0 · 192.168.42.8', [row[0] for row in self.panel.interfaces.get_model()])
        self.assertTrue(self.panel.start_button.get_sensitive())
        self.assertFalse(self.panel.stop_button.get_sensitive())

    def test_preferences_persist_without_changing_video_settings(self):
        original = replace(self.app.settings, width=1440, height=900, quality=21, fps=24)
        self.app.settings = original
        self.show()
        self.panel.port.set_value(9876)
        self.panel.duration.set_active_id('900')
        self.panel.interfaces.set_active_id('usb0')
        self.panel.set_apk_path('/tmp/custom.apk')
        self.assertEqual(self.app.settings, replace(original, apk_port=9876, apk_duration=900,
                                                   apk_interface='usb0', apk_path='/tmp/custom.apk'))
        self.panel.use_default_apk()
        self.assertEqual(self.app.settings.apk_path, '')

    def test_start_stop_and_download_count_are_distinct_from_installation(self):
        self.show()
        self.panel.start()
        self.assertEqual(self.panel.state, 'starting')
        self.assertFalse(self.panel.port.get_sensitive())
        self.assertTrue(self.panel.stop_button.get_sensitive())
        self.run_tasks()
        server = self.servers[0]
        self.assertEqual(self.panel.state, 'running')
        self.assertEqual(self.panel.url_entry.get_text(), server.url)
        self.assertTrue(self.panel.copy_button.get_sensitive())
        server.event('download', filename='panelyra.apk', size=10)
        self.assertEqual(self.panel.count, 1)
        self.assertIn('Completed APK downloads: 1', self.panel.detail.get_text())
        self.assertIn('install it', self.panel.detail.get_text())
        self.panel.stop()
        self.run_tasks()
        self.assertFalse(server.running)
        self.assertEqual(self.panel.state, 'idle')
        self.assertFalse(self.panel.copy_button.get_sensitive())
        self.assertTrue(self.panel.port.get_sensitive())

    def test_localization_does_not_restart_server_or_reset_selection(self):
        self.show()
        self.panel.interfaces.set_active_id('usb0')
        self.panel.duration.set_active_id('300')
        self.panel.start()
        self.run_tasks()
        server = self.servers[0]
        self.app.russian = True
        self.panel.render()
        self.assertEqual(self.panel.header.get_title(), 'Приложение Android')
        self.assertEqual(self.panel.start_button.get_label(), 'Запустить сервер')
        self.assertEqual(self.panel.status.get_text(), 'Можно скачивать на планшете')
        self.assertEqual(self.panel.interfaces.get_active_id(), 'usb0')
        self.assertEqual(self.panel.duration.get_active_id(), '300')
        self.assertEqual(self.panel.url_entry.get_text(), server.url)
        self.assertEqual(server.stop_count, 0)
        self.factory.assert_called_once()

    def test_busy_port_gives_actionable_error_without_touching_other_process(self):
        self.show()
        self.factory.side_effect = OSError(errno.EADDRINUSE, 'Address already in use')
        self.panel.start()
        self.run_tasks()
        self.assertEqual(self.panel.state, 'idle')
        self.assertIn('Choose another port', self.panel.detail.get_text())
        self.assertEqual(self.servers, [])

    def test_cancel_before_discovery_returns_cannot_start_late_server(self):
        self.show()
        self.panel.start()
        self.panel.stop()
        self.run_tasks()
        self.factory.assert_not_called()
        self.assertEqual(self.panel.state, 'idle')

    def test_close_during_server_start_stops_late_server_and_ignores_events(self):
        self.show()

        def factory(*args, **kwargs):
            server = FakeServer(*args, **kwargs)
            self.servers.append(server)

            def start():
                server.running = True
                self.panel.close()
                return server
            server.start = start
            return server
        self.factory.side_effect = factory
        self.panel.start()
        self.run_tasks()
        server = self.servers[0]
        self.assertFalse(server.running)
        self.assertGreaterEqual(server.stop_count, 1)
        server.event('download', filename='old.apk', size=10)
        self.assertEqual(self.panel.count, 0)

    def test_hiding_dialog_keeps_server_owned_until_main_app_closes(self):
        self.show()
        self.panel.start()
        self.run_tasks()
        server = self.servers[0]
        self.assertTrue(self.panel.hide())
        self.assertFalse(self.panel.window.get_visible())
        self.assertTrue(server.running)
        self.panel.close()
        self.run_tasks()
        self.assertFalse(server.running)

    def test_automatic_stop_displays_reason_and_enables_settings(self):
        self.show()
        self.panel.start()
        self.run_tasks()
        server = self.servers[0]
        server.running = False
        server.event('stopped', reason='disconnected')
        self.assertEqual(self.panel.state, 'idle')
        self.assertIn('USB connection changed', self.panel.detail.get_text())
        self.assertTrue(self.panel.port.get_sensitive())

    def test_runtime_failure_explains_reconnection_instead_of_startup_failure(self):
        self.show()
        self.panel.start()
        self.run_tasks()
        server = self.servers[0]
        server.running = False
        server.event('error', message='USB disconnected')
        server.event('stopped', reason='disconnected')
        self.assertIn('Check the USB connection and start it again', self.panel.detail.get_text())
        self.assertNotIn('Could not start', self.panel.detail.get_text())
        self.assertEqual(self.panel.state, 'idle')

    def test_settings_write_failure_does_not_start_and_can_be_retried(self):
        self.show()
        original = self.app.save_apk_settings.side_effect
        self.app.save_apk_settings.side_effect = OSError('disk is read-only')
        self.panel.start()
        self.run_tasks()
        self.factory.assert_not_called()
        self.assertIn('Could not save', self.panel.detail.get_text())
        self.app.save_apk_settings.side_effect = original
        self.panel.start()
        self.run_tasks()
        self.assertEqual(self.panel.state, 'running')


if __name__ == '__main__':
    unittest.main()

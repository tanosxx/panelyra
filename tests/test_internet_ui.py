"""The Internet button uses queued fake workers and never contacts the network."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
    from usbdisplay import internet_ui
    from gtk_support import get_test_application
except (ImportError, ValueError):
    Gtk = None


def state(enabled, connection='/active/1'):
    return SimpleNamespace(enabled=enabled, active_path=connection, interface='usb-test')


def failure(code):
    error = RuntimeError('test-only diagnostic')
    error.code = code
    return error


@unittest.skipIf(Gtk is None, 'GTK 3 is not installed')
class InternetPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = get_test_application()

    def setUp(self):
        self.tasks, self.callbacks = [], []
        self.app = SimpleNamespace(russian=False, append_log=Mock())
        self.app.tr = lambda en, ru: ru if self.app.russian else en
        self.controller = Mock()
        self.patch('internet.InternetController', return_value=self.controller)
        self.patch('threading.Thread', side_effect=lambda target, **_: SimpleNamespace(
            start=lambda: self.tasks.append(target)))
        self.patch('GLib.idle_add', side_effect=lambda callback, *args: self.callbacks.append(
            lambda: callback(*args)))
        self.panel = internet_ui.InternetPanel(self.app)
        self.addCleanup(self.panel.widget.destroy)
        self.addCleanup(self.panel.close)

    def patch(self, name, **kwargs):
        patcher = patch('usbdisplay.internet_ui.' + name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def complete(self):
        while self.tasks:
            self.tasks.pop(0)()
        while self.callbacks:
            self.callbacks.pop(0)()

    def ready(self, enabled):
        snapshot = state(enabled)
        self.controller.inspect.return_value = snapshot
        self.panel.refresh()
        self.complete()
        return snapshot

    def test_build_and_translation_never_inspect_or_modify_network(self):
        self.assertEqual(self.panel.title.get_text(), 'Internet via tablet')
        self.assertFalse(self.panel.button.get_sensitive())
        self.app.russian = True
        self.panel.render()
        self.assertEqual(self.panel.title.get_text(), 'Интернет через планшет')
        self.controller.inspect.assert_not_called()
        self.controller.set_enabled.assert_not_called()
        self.assertEqual(self.tasks, [])

    def test_inspection_runs_in_worker_and_applies_only_in_main_callback(self):
        self.controller.inspect.return_value = state(False)
        self.panel.refresh()
        self.panel.refresh()
        self.assertEqual(len(self.tasks), 1)
        self.controller.inspect.assert_not_called()
        self.tasks.pop(0)()
        self.controller.inspect.assert_called_once_with()
        self.assertIsNone(self.panel.state)
        self.assertFalse(self.panel.button.get_sensitive())
        self.callbacks.pop(0)()
        self.assertEqual(self.panel.status.get_text(), 'Disabled')
        self.assertEqual(self.panel.button.get_label(), 'Turn on')
        self.assertTrue(self.panel.button.get_sensitive())

    def test_toggle_uses_latest_snapshot_and_prevents_duplicate_requests(self):
        original = self.ready(True)
        updated = state(False)
        self.controller.set_enabled.return_value = updated
        self.panel.toggle()
        self.panel.toggle()
        self.panel.refresh()
        self.assertEqual(len(self.tasks), 1)
        self.assertFalse(self.panel.button.get_sensitive())
        self.assertEqual(self.panel.button.get_label(), 'Applying…')
        self.controller.set_enabled.assert_not_called()
        self.complete()
        self.controller.set_enabled.assert_called_once_with(original, False)
        self.assertIs(self.panel.state, updated)
        self.assertEqual(self.panel.button.get_label(), 'Turn on')
        self.assertTrue(self.panel.button.get_sensitive())
        self.app.append_log.assert_called_once_with('Internet via tablet disabled.')

    def test_failed_change_reinspects_actual_state_and_shows_localized_error(self):
        self.ready(False)
        self.controller.set_enabled.side_effect = failure('permission')
        # Another network manager can change the state while our request fails.
        updated = state(True)
        self.controller.inspect.return_value = updated
        self.panel.toggle()
        self.complete()
        self.assertIs(self.panel.state, updated)
        self.assertEqual(self.panel.status.get_text(), 'Allowed')
        self.assertEqual(self.panel.button.get_label(), 'Turn off')
        self.app.russian = True
        self.panel.render()
        self.assertEqual(self.panel.status.get_text(), 'Разрешён')
        self.assertEqual(self.panel.button.get_label(), 'Отключить')
        self.assertIn('NetworkManager не разрешил', self.panel.detail.get_text())
        self.assertEqual(self.panel.detail.get_tooltip_text(), 'test-only diagnostic')

    def test_failed_change_and_failed_reinspection_disable_stale_action(self):
        self.ready(True)
        self.controller.set_enabled.side_effect = failure('changed')
        self.controller.inspect.side_effect = failure('no-usb')
        self.panel.toggle()
        self.complete()
        self.assertIsNone(self.panel.state)
        self.assertFalse(self.panel.button.get_sensitive())
        self.assertEqual(self.panel.status.get_text(), 'Unavailable')

    def test_periodic_read_reflects_external_change_without_writes(self):
        self.ready(True)
        self.controller.inspect.return_value = state(False)
        self.panel.refresh()
        self.complete()
        self.assertEqual(self.panel.status.get_text(), 'Disabled')
        self.controller.set_enabled.assert_not_called()

    def test_no_usb_and_multiple_usb_cannot_be_toggled(self):
        for code, text in [('no-usb', 'Enable USB tethering'),
                           ('ambiguous', 'Keep only the tablet')]:
            with self.subTest(code=code):
                self.controller.inspect.side_effect = failure(code)
                self.panel.refresh()
                self.complete()
                self.panel.toggle()
                self.assertIsNone(self.panel.state)
                self.assertFalse(self.panel.button.get_sensitive())
                self.assertIn(text, self.panel.detail.get_text())
        self.controller.set_enabled.assert_not_called()

    def test_reconnect_clears_error_from_previous_connection(self):
        self.ready(False)
        self.controller.set_enabled.side_effect = failure('permission')
        self.panel.toggle()
        self.complete()
        self.assertEqual(self.panel.error, 'permission')
        self.controller.inspect.return_value = state(True, '/active/2')
        self.panel.refresh()
        self.complete()
        self.assertIsNone(self.panel.error)
        self.assertIsNone(self.panel.diagnostic)

    def test_disconnection_replaces_old_operation_error_with_current_usb_hint(self):
        self.ready(False)
        self.controller.set_enabled.side_effect = failure('permission')
        self.panel.toggle()
        self.complete()
        self.controller.inspect.side_effect = failure('no-usb')
        self.panel.refresh()
        self.complete()
        self.assertIsNone(self.panel.error)
        self.assertIn('Enable USB tethering', self.panel.detail.get_text())

    def test_close_ignores_inflight_callbacks_without_reverting_network(self):
        original = self.ready(False)
        self.controller.set_enabled.return_value = state(True)
        self.panel.toggle()
        self.tasks.pop(0)()
        self.panel.close()
        self.panel.status.set_text('closed sentinel')
        self.complete()
        self.assertEqual(self.panel.status.get_text(), 'closed sentinel')
        self.controller.set_enabled.assert_called_once_with(original, True)
        self.app.append_log.assert_not_called()


if __name__ == '__main__':
    unittest.main()

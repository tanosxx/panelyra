"""The optional Shell companion must not break streaming or move stale layouts."""

import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from usbdisplay.window_restore import BUS, INTERFACE, PATH, WindowRestorer


class DbusError(Exception):
    pass


class WindowRestorerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = {"Get": (1,), "Register": (True,), "Restore": (3,), "Unregister": ()}
        self.connection = Mock()
        self.connection.call_sync.side_effect = self.call
        gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
        glib = SimpleNamespace(Error=DbusError, Variant=lambda signature, values: (signature, values))
        self.restorer = WindowRestorer(gio, glib, self.connection)
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def call(self, bus, path, interface, method, parameters, reply, flags, timeout, cancellable):
        self.assertEqual((bus, path), (BUS, PATH))
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 4000)
        self.calls.append((method, parameters, timeout, interface))
        response = self.responses[method]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(unpack=lambda: response)

    def methods(self):
        return [call[0] for call in self.calls]

    def test_registers_without_restoring_on_initial_connection(self):
        self.restorer.ready("Meta-1")
        self.assertEqual(self.methods(), ["Get", "Register"])
        self.assertEqual(self.calls[0][1], ("(ss)", (INTERFACE, "Version")))
        self.assertEqual(self.calls[0][3], "org.freedesktop.DBus.Properties")
        self.assertTrue(self.restorer.registered)

    def test_reconnect_registers_new_connector_before_restore(self):
        self.restorer.ready("Meta-1")
        self.restorer.ready("Meta-2")
        self.assertEqual(self.methods(), ["Get", "Register", "Register", "Restore"])
        self.assertEqual(self.calls[-2][1], ("(s)", ("Meta-2",)))
        self.assertEqual(self.calls[-1][1], ("(s)", ("Meta-2",)))
        self.assertEqual(self.calls[-1][2], 4000)
        self.assertIn("3", self.output.getvalue())

    def test_changed_topology_discards_snapshot_before_registering(self):
        self.restorer.ready("Meta-1")
        self.restorer.ready("Meta-2", layout_restored=False)
        self.assertEqual(self.methods(), ["Get", "Register", "Unregister", "Register"])
        self.assertTrue(self.restorer.registered)

    def test_failed_discard_does_not_register_or_restore_stale_snapshot(self):
        self.restorer.ready("Meta-1")
        self.responses["Unregister"] = DbusError("timeout")
        self.restorer.ready("Meta-2", layout_restored=False)
        self.assertEqual(self.methods(), ["Get", "Register", "Unregister"])
        self.assertTrue(self.restorer.discard_pending)
        self.restorer.ready("Meta-3", layout_restored=True)
        self.assertEqual(self.methods(), ["Get", "Register", "Unregister", "Unregister"])
        self.responses["Unregister"] = ()
        self.restorer.ready("Meta-4", layout_restored=True)
        self.assertEqual(self.methods()[-2:], ["Unregister", "Register"])
        self.assertNotIn("Restore", self.methods())
        self.assertFalse(self.restorer.discard_pending)

    def test_missing_extension_is_nonfatal_and_warns_once(self):
        self.responses["Get"] = DbusError("UnknownMethod")
        self.restorer.ready("Meta-1")
        self.restorer.ready("Meta-2")
        self.restorer.close()
        self.assertEqual(self.methods(), ["Get"])
        self.assertEqual(self.output.getvalue().count("Возврат окон:"), 1)

    def test_unknown_protocol_does_not_register(self):
        self.responses["Get"] = (2,)
        self.restorer.ready("Meta-1")
        self.assertEqual(self.methods(), ["Get"])
        self.assertFalse(self.restorer.registered)

    def test_denied_registration_does_not_restore(self):
        self.responses["Register"] = (False,)
        self.restorer.ready("Meta-1")
        self.restorer.ready("Meta-2")
        self.assertNotIn("Restore", self.methods())
        self.assertFalse(self.restorer.registered)

    def test_restore_error_leaves_helper_usable_and_video_unaffected(self):
        self.restorer.ready("Meta-1")
        self.responses["Restore"] = DbusError("screen still locked")
        self.restorer.ready("Meta-2")
        self.assertTrue(self.restorer.registered)
        self.responses["Restore"] = (2,)
        self.restorer.ready("Meta-3")
        self.assertEqual(self.methods().count("Restore"), 2)

    def test_ignores_missing_and_physical_connectors(self):
        for connector in (None, "", "HDMI-1", "eDP-1"):
            self.restorer.ready(connector)
        self.assertEqual(self.methods(), [])

    def test_close_unregisters_once_even_if_extension_disappeared(self):
        self.restorer.ready("Meta-1")
        self.responses["Unregister"] = DbusError("gone")
        self.restorer.close()
        self.restorer.close()
        self.assertEqual(self.methods(), ["Get", "Register", "Unregister"])
        self.assertFalse(self.restorer.registered)


if __name__ == "__main__":
    unittest.main()

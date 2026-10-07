import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from usbdisplay.cursor import CursorWorkaround
from usbdisplay.host import Stream


class Variant:
    def __init__(self, _signature, value):
        self.value = value

    def unpack(self):
        return self.value


class DbusError(Exception):
    pass


class CursorSelectionTests(unittest.TestCase):
    @staticmethod
    def monitor(connector, width=1280, height=800, current=True):
        return ((connector, "vendor", "product", "serial"), [
            ("mode", width, height, 20.0, 1.0, [1.0], {"is-current": current})
        ], {})

    def stream(self, monitors):
        stream = Stream.__new__(Stream)
        stream.stopping = threading.Event()
        stream.width, stream.height = 1280, 800
        # Capture cannot produce encoder frames until the monitor node is delegated.
        stream.frames = 0
        stream.keeper_frames = 1
        stream.monitors_before = {"Meta-0", "DP-1"}
        stream.monitor_state = Mock(return_value=monitors)
        stream.monitor_wait_started = time.monotonic()
        stream.cursor_workaround = None
        stream.Gio = stream.GLib = stream.Gst = object()
        stream.fail = Mock(return_value=False)
        return stream

    def test_only_new_current_matching_virtual_monitor_is_selected(self):
        stream = self.stream([
            self.monitor("Meta-0"), self.monitor("DP-1"), self.monitor("HDMI-2"),
            self.monitor("Meta-1", current=False), self.monitor("Meta-2", width=1024),
            self.monitor("Meta-3"),
        ])
        with patch("usbdisplay.host.CursorWorkaround") as sidecar:
            self.assertFalse(stream.setup_cursor())
            self.assertEqual(sidecar.call_args.args[0], "Meta-3")
            self.assertEqual(sidecar.call_args.kwargs["on_node"], stream.start_pipeline)
            sidecar.return_value.start.assert_called_once_with()
            stream.fail.assert_not_called()

    def test_existing_virtual_and_new_physical_monitor_are_never_used(self):
        stream = self.stream([self.monitor("Meta-0"), self.monitor("HDMI-2")])
        with patch("usbdisplay.host.CursorWorkaround") as sidecar:
            self.assertTrue(stream.setup_cursor())
            sidecar.assert_not_called()

    def test_two_new_matching_virtual_monitors_fail_without_recording_either(self):
        stream = self.stream([self.monitor("Meta-1"), self.monitor("Meta-2")])
        with patch("usbdisplay.host.CursorWorkaround") as sidecar:
            self.assertFalse(stream.setup_cursor())
            sidecar.assert_not_called()
            self.assertIn("однозначно", stream.fail.call_args.args[0])

    def test_waits_for_first_keeper_frame_before_selecting_another_clients_new_monitor(self):
        stream = self.stream([self.monitor("Meta-1")])
        stream.keeper_frames = 0
        with patch("usbdisplay.host.CursorWorkaround") as sidecar:
            self.assertTrue(stream.setup_cursor())
            sidecar.assert_not_called()

    def test_sidecar_start_failure_stops_host_and_retains_cleanup_reference(self):
        stream = self.stream([self.monitor("Meta-1")])
        with patch("usbdisplay.host.CursorWorkaround") as sidecar:
            sidecar.return_value.start.side_effect = RuntimeError("capture denied")
            self.assertFalse(stream.setup_cursor())
            self.assertIs(stream.cursor_workaround, sidecar.return_value)
            self.assertIn("capture denied", stream.fail.call_args.args[0])


class CursorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.dbus = Mock()
        self.dbus.signal_subscribe.side_effect = [71, 72]
        self.GLib = SimpleNamespace(Variant=Variant, Error=DbusError,
                                    timeout_add_seconds=Mock(return_value=91), source_remove=Mock())
        self.Gio = SimpleNamespace(
            DBusCallFlags=SimpleNamespace(NONE=0),
            DBusSignalFlags=SimpleNamespace(NONE=0),
            BusType=SimpleNamespace(SESSION=0),
            bus_get_sync=Mock(return_value=self.dbus),
        )
        self.bus = Mock()
        self.bus.connect.return_value = 81
        self.pipeline = Mock()
        self.pipeline.get_bus.return_value = self.bus
        self.pipeline.set_state.return_value = 1
        self.Gst = SimpleNamespace(
            State=SimpleNamespace(PLAYING="PLAYING", NULL="NULL"),
            StateChangeReturn=SimpleNamespace(FAILURE=0),
            MessageType=SimpleNamespace(ERROR="ERROR", EOS="EOS"),
            parse_launch=Mock(return_value=self.pipeline),
        )
        self.failure = Mock()
        self.workaround = CursorWorkaround("Meta-3", self.Gio, self.GLib, self.Gst, self.failure)
        self.dbus.call_sync.side_effect = self.reply

    @staticmethod
    def reply(_destination, _path, _interface, method, *_args):
        if method == "CreateSession":
            return Variant("", ("/owned-cursor-session",))
        if method == "RecordMonitor":
            return Variant("", ("/owned-cursor-stream",))
        return Variant("", ())

    def stopped_sessions(self):
        return [entry.args[1] for entry in self.dbus.call_sync.call_args_list if entry.args[3] == "Stop"]

    def test_partial_create_failure_releases_its_session(self):
        def reject_record(*args):
            if args[3] == "RecordMonitor":
                raise DbusError("monitor disappeared")
            return self.reply(*args)
        self.dbus.call_sync.side_effect = reject_record
        with self.assertRaisesRegex(RuntimeError, "monitor disappeared"):
            self.workaround.start()
        self.assertEqual(self.stopped_sessions(), ["/owned-cursor-session"])
        self.dbus.signal_unsubscribe.assert_called_once_with(71)
        self.Gst.parse_launch.assert_not_called()
        self.workaround.close()
        self.assertEqual(self.stopped_sessions(), ["/owned-cursor-session"])

    def test_close_stops_only_owned_capture_and_is_idempotent(self):
        self.workaround.start()
        record = next(entry for entry in self.dbus.call_sync.call_args_list if entry.args[3] == "RecordMonitor")
        self.assertEqual(record.args[4].unpack()[0], "Meta-3")
        self.workaround._on_node(None, None, None, None, None, Variant("", (42,)))
        self.workaround.close()
        self.workaround.close()
        self.assertEqual(self.stopped_sessions(), ["/owned-cursor-session"])
        self.dbus.signal_unsubscribe.assert_has_calls([call(71), call(72)])
        self.assertEqual(self.dbus.signal_unsubscribe.call_count, 2)
        self.bus.disconnect.assert_called_once_with(81)
        self.bus.remove_signal_watch.assert_called_once_with()
        self.assertEqual(self.pipeline.set_state.call_args_list, [call("PLAYING"), call("NULL")])
        self.failure.assert_not_called()

    def test_async_pipeline_error_reports_once_and_late_callbacks_do_nothing(self):
        self.workaround.start()
        self.workaround._on_node(None, None, None, None, None, Variant("", (42,)))
        error = SimpleNamespace(message="negotiation failed")
        message = SimpleNamespace(type="ERROR", parse_error=lambda: (error, None))
        self.workaround._on_message(self.bus, message)
        self.workaround._on_message(self.bus, message)
        self.workaround._on_closed()
        self.workaround._on_node(None, None, None, None, None, Variant("", (43,)))
        self.failure.assert_called_once()
        self.assertIn("negotiation failed", self.failure.call_args.args[0])
        self.assertEqual(self.stopped_sessions(), ["/owned-cursor-session"])
        self.Gst.parse_launch.assert_called_once()

    def test_already_removed_dbus_session_does_not_break_cleanup(self):
        self.workaround.start()
        def already_closed(*args):
            if args[3] == "Stop":
                raise DbusError("session already removed")
            return self.reply(*args)
        self.dbus.call_sync.side_effect = already_closed
        self.workaround.close()
        self.workaround.close()
        self.assertIsNone(self.workaround.session)
        self.assertEqual(self.stopped_sessions(), ["/owned-cursor-session"])

    def test_ready_timeout_without_a_frame_releases_capture_and_reports_error(self):
        self.workaround.start()
        self.workaround._on_node(None, None, None, None, None, Variant("", (42,)))
        self.assertFalse(self.workaround._check_ready())
        self.failure.assert_called_once()
        self.assertIn("10 секунд", self.failure.call_args.args[0])
        self.assertEqual(self.stopped_sessions(), ["/owned-cursor-session"])
        self.assertEqual(self.pipeline.set_state.call_args_list, [call("PLAYING"), call("NULL")])
        self.bus.remove_signal_watch.assert_called_once_with()
        self.assertEqual(self.dbus.signal_unsubscribe.call_count, 2)
        self.assertIsNone(self.workaround.readiness_timer)
        # The callback already owns/removes its timer; cleanup must not remove it twice.
        self.GLib.source_remove.assert_not_called()
        self.workaround._on_handoff(None, None, None)
        self.workaround._check_ready()
        self.assertEqual(self.workaround.frames, 0)
        self.failure.assert_called_once()

    def test_node_is_delegated_once_without_creating_an_extra_pipeline(self):
        delegate = Mock()
        self.workaround = CursorWorkaround("Meta-3", self.Gio, self.GLib, self.Gst,
                                           self.failure, on_node=delegate)
        self.workaround.start()
        self.workaround._on_node(None, None, None, None, None, Variant("", (42,)))
        self.workaround._on_node(None, None, None, None, None, Variant("", (43,)))
        delegate.assert_called_once_with(42)
        self.Gst.parse_launch.assert_not_called()
        self.assertIsNone(self.workaround.pipeline)
        self.failure.assert_not_called()
        self.workaround.close()
        self.workaround._on_node(None, None, None, None, None, Variant("", (44,)))
        delegate.assert_called_once_with(42)
        self.assertEqual(self.stopped_sessions(), ["/owned-cursor-session"])

    def test_delegate_error_releases_recordmonitor_and_reports_failure_once(self):
        delegate = Mock(side_effect=RuntimeError("encoder initialization failed"))
        self.workaround = CursorWorkaround("Meta-3", self.Gio, self.GLib, self.Gst,
                                           self.failure, on_node=delegate)
        self.workaround.start()
        self.workaround._on_node(None, None, None, None, None, Variant("", (42,)))
        self.workaround._on_node(None, None, None, None, None, Variant("", (43,)))
        self.workaround._on_closed()
        delegate.assert_called_once_with(42)
        self.Gst.parse_launch.assert_not_called()
        self.failure.assert_called_once()
        self.assertIn("encoder initialization failed", self.failure.call_args.args[0])
        self.assertTrue(self.workaround.closed)
        self.assertIsNone(self.workaround.session)
        self.assertEqual(self.stopped_sessions(), ["/owned-cursor-session"])
        self.assertEqual(self.dbus.signal_unsubscribe.call_count, 2)
        self.GLib.source_remove.assert_called_once_with(91)


if __name__ == "__main__":
    unittest.main()

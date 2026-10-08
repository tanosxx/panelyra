"""Lock/reconnect lifecycle regressions without a desktop, capture or USB device."""

import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from usbdisplay.resume import LockResumingStream


class DbusError(Exception):
    @property
    def message(self):
        return str(self)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.locked = False
        self.dbus = Mock()
        self.loop = Mock()
        self.Gio = SimpleNamespace(
            DBusCallFlags=SimpleNamespace(NONE=0),
            DBusSignalFlags=SimpleNamespace(NONE=0),
            BusType=SimpleNamespace(SESSION=0),
            bus_get_sync=Mock(return_value=self.dbus))
        self.GLib = SimpleNamespace(
            Error=DbusError,
            MainLoop=Mock(return_value=self.loop),
            MainContext=SimpleNamespace(default=lambda: SimpleNamespace(
                find_source_by_id=lambda _source: False)),
            timeout_add_seconds=Mock(return_value=91),
            source_remove=Mock())
        self.created_pipelines = []
        self.Gst = SimpleNamespace(
            State=SimpleNamespace(PLAYING="PLAYING", NULL="NULL"),
            StateChangeReturn=SimpleNamespace(FAILURE=0),
            MessageType=SimpleNamespace(ERROR="ERROR", EOS="EOS"),
            parse_launch=Mock(side_effect=self.make_pipeline))
        self.addCleanup(patch.stopall)
        patch("usbdisplay.host.load_gi", return_value=(self.Gio, self.GLib, self.Gst)).start()
        self.prepare_scene = patch("usbdisplay.host.prepare_lock_scene", return_value=None).start()
        patch("usbdisplay.resume.time.monotonic", side_effect=lambda: self.now).start()
        patch("usbdisplay.host.select.select", return_value=([], [], [])).start()
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.connection = Mock()
        self.status = Mock()
        self.stream = LockResumingStream(
            self.connection, 1280, 800, 30, 2500, on_status=self.status)
        self.stream.phase = "desktop"
        self.stream.dbus = self.dbus
        self.stream.screen_lock = Mock()
        self.stream.screen_lock.refresh.side_effect = lambda: self.locked
        self.stream.frames = 10
        self.stream.cursor_ready_reported = True
        self.stream.saved_layout = object()
        self.stream.pipeline = self.make_pipeline("desktop")
        self.stream.bus = self.stream.pipeline.get_bus()
        self.stream.keeper_pipeline = self.make_pipeline("keeper")
        self.stream.keeper_bus = self.stream.keeper_pipeline.get_bus()
        self.stream.cursor_workaround = Mock(connector="Meta-1", frames=10)
        self.stream.session = "/owned-session"
        self.stream.connector = "Meta-1"
        self.stream.remember_layout = Mock()
        self.stream.restore_layout = Mock()

    def make_pipeline(self, _description):
        pipeline = Mock()
        pipeline.set_state.return_value = 1
        self.created_pipelines.append(pipeline)
        return pipeline

    @staticmethod
    def error_message(text="capture revoked"):
        return SimpleNamespace(type="ERROR", parse_error=lambda: (SimpleNamespace(message=text), None))

    def lock(self):
        self.locked = True
        self.stream.on_lock_changed(True)

    def unlock(self):
        self.locked = False
        self.stream.on_lock_changed(False)

    def begin_desktop(self):
        """A new capture exists, but its first decoded frame has not arrived."""
        self.stream.start_pipeline(42)
        self.stream.cursor_workaround = Mock(connector="Meta-2", frames=0)

    def test_closed_before_active_changed_uses_grace_then_waits_locked_indefinitely(self):
        desktop, keeper = self.stream.pipeline, self.stream.keeper_pipeline
        old_cursor = self.stream.cursor_workaround
        self.assertFalse(self.stream.capture_failed("GNOME closed capture"))
        self.assertEqual(self.stream.phase, "grace")
        desktop.set_state.assert_called_once_with("NULL")
        keeper.set_state.assert_called_once_with("NULL")
        old_cursor.close.assert_called_once()
        self.now += 1
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "grace")
        self.lock()
        self.assertEqual(self.stream.phase, "locked")
        self.assertIsNone(self.stream.pending_capture_error)
        self.assertTrue(self.stream.restore_layout_pending)
        self.now += 300
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "locked")
        self.assertIsNone(self.stream.error)
        self.connection.shutdown.assert_not_called()
        self.status.assert_called_once_with("locked")

    def test_duplicate_lock_and_capture_failures_do_not_restart_placeholder(self):
        self.lock()
        placeholder = self.stream.pipeline
        self.stream.capture_failed("late Closed")
        self.stream.on_lock_changed(True)
        self.now += 30
        self.assertTrue(self.stream.tick())
        self.assertIs(self.stream.pipeline, placeholder)
        self.Gst.parse_launch.assert_called_once()
        self.status.assert_called_once_with("locked")
        self.assertFalse(self.stream.stopping.is_set())

    def test_theme_and_language_updates_only_affect_next_lock_scene(self):
        desktop = self.stream.pipeline
        self.stream.set_theme('aurora')
        self.stream.set_language('en')
        self.assertIs(self.stream.pipeline, desktop)
        self.Gst.parse_launch.assert_not_called()
        self.connection.sendall.assert_not_called()
        self.connection.shutdown.assert_not_called()
        self.lock()
        self.prepare_scene.assert_called_once_with(1280, 800, self.Gst, 'en', theme='aurora')
        placeholder = self.stream.pipeline
        self.stream.set_theme('editorial')
        self.stream.set_language('ru')
        self.assertIs(self.stream.pipeline, placeholder)
        self.assertEqual(self.Gst.parse_launch.call_count, 1)
        self.assertEqual(self.prepare_scene.call_count, 1)
        self.unlock()
        self.lock()
        self.prepare_scene.assert_called_with(1280, 800, self.Gst, 'ru', theme='editorial')
        self.assertFalse(self.stream.stopping.is_set())

    def test_invalid_theme_does_not_change_capture_or_saved_theme(self):
        desktop = self.stream.pipeline
        self.assertEqual(self.stream.theme, 'classic')
        for theme in ('unknown', None, [], {'theme': 'aurora'}):
            with self.subTest(theme=theme), self.assertRaises(ValueError):
                self.stream.set_theme(theme)
        self.assertEqual(self.stream.theme, 'classic')
        self.assertIs(self.stream.pipeline, desktop)
        self.Gst.parse_launch.assert_not_called()
        self.connection.sendall.assert_not_called()

    def test_initial_selected_theme_is_used_when_starting_while_locked(self):
        stream = LockResumingStream(self.connection, 1280, 800, 30, 2500,
                                    theme='midnight', language='ru')
        watcher = Mock()
        watcher.start.return_value = True
        with patch('usbdisplay.resume.ScreenLock', return_value=watcher):
            stream.create_monitor()
        self.prepare_scene.assert_called_once_with(1280, 800, self.Gst, 'ru', theme='midnight')
        self.assertEqual(stream.phase, 'locked')

    def test_polling_recovers_missed_lock_and_unlock_notifications(self):
        self.stream.capture_failed("Closed")
        self.locked = True
        self.now += 1
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "locked")
        self.locked = False
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.now += 1
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "resuming")
        self.stream.begin_desktop.assert_called_once()
        self.assertEqual(self.status.call_args_list, [call("locked"), call("resuming")])

    def test_unlock_announces_ready_only_after_new_desktop_frame_and_restores_once(self):
        self.lock()
        self.stream.frames += 4  # Frames from the local placeholder.
        self.unlock()
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "resuming")
        self.stream.restore_layout.assert_not_called()
        self.status.assert_has_calls([call("locked"), call("resuming")])
        self.assertNotIn(call("ready"), self.status.call_args_list)
        self.stream.frames += 1
        self.stream.cursor_workaround.frames = 1
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "desktop")
        self.assertEqual(self.stream.connector, "Meta-2")
        self.assertEqual(self.stream.resume_deadline, 0)
        self.stream.restore_layout.assert_called_once()
        self.status.assert_called_with("ready")
        self.assertTrue(self.stream.tick())
        self.stream.restore_layout.assert_called_once()
        self.assertEqual(self.status.call_args_list.count(call("ready")), 1)

    def test_window_restore_waits_for_frames_and_monitor_layout(self):
        restorer = self.stream.window_restorer = Mock()
        self.stream.restore_layout.return_value = True
        self.stream.restore_layout.side_effect = lambda: (
            restorer.ready.assert_not_called() or True)
        self.lock()
        self.unlock()
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.stream.tick()
        restorer.ready.assert_not_called()
        self.stream.frames += 1
        self.stream.cursor_workaround.frames = 1
        self.stream.tick()
        restorer.ready.assert_called_once_with("Meta-2", layout_restored=True)
        self.stream.tick()
        restorer.ready.assert_called_once()

    def test_failed_monitor_layout_prevents_stale_window_restore(self):
        restorer = self.stream.window_restorer = Mock()
        self.stream.restore_layout.return_value = False
        self.stream.phase = "resuming"
        self.stream.desktop_first_frame = self.stream.frames - 1
        self.stream.tick()
        restorer.ready.assert_called_once_with("Meta-1", layout_restored=False)
        self.assertIsNone(self.stream.error)

    def test_unlock_retry_keeps_original_deadline_and_does_not_retry_early(self):
        self.lock()
        self.unlock()
        deadline = self.stream.resume_deadline
        self.stream.begin_desktop = Mock(side_effect=DbusError("Session creation inhibited"))
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "waiting")
        self.assertEqual(self.stream.resume_deadline, deadline)
        self.now += 0.5
        self.assertTrue(self.stream.tick())
        self.stream.begin_desktop.assert_called_once()
        self.now += 0.5
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.begin_desktop.call_count, 2)
        self.assertEqual(self.stream.resume_deadline, deadline)
        self.now = deadline
        self.assertFalse(self.stream.tick())
        self.assertTrue(self.stream.stopping.is_set())
        self.assertIn("восстановить экран", self.stream.error)
        self.assertIn("Session creation inhibited", self.stream.error)
        self.assertEqual(self.stream.begin_desktop.call_count, 2)

    def test_placeholder_keeps_streaming_during_capture_node_negotiation(self):
        self.lock()
        placeholder = self.stream.pipeline
        self.unlock()
        self.stream.begin_desktop = Mock()  # No node has been supplied yet.
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "resuming")
        self.assertIs(self.stream.placeholder_pipeline, placeholder)
        self.assertIsNone(self.stream.pipeline)
        placeholder.set_state.assert_called_once_with("PLAYING")
        self.stream.frames += 3  # Only local placeholder frames arrived.
        self.now += 1
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "resuming")
        self.stream.restore_layout.assert_not_called()
        self.assertNotIn(call("ready"), self.status.call_args_list)
        self.begin_desktop()
        placeholder.set_state.assert_has_calls([call("PLAYING"), call("NULL")])
        self.assertIsNone(self.stream.placeholder_pipeline)

    def test_negotiation_placeholder_failure_is_terminal_even_while_resuming(self):
        self.lock()
        placeholder_bus = self.stream.bus
        self.unlock()
        self.stream.begin_desktop = Mock()
        self.stream.tick()
        self.stream.on_message(placeholder_bus, self.error_message("placeholder stalled"))
        self.assertTrue(self.stream.stopping.is_set())
        self.assertIn("placeholder stalled", self.stream.error)

    def test_no_frame_attempt_returns_to_placeholder_before_receiver_timeout(self):
        self.lock()
        self.unlock()
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.assertTrue(self.stream.tick())
        stalled_desktop = self.stream.pipeline
        original_deadline = self.stream.resume_deadline
        self.assertEqual(self.stream.capture_attempt_until - self.now, 5)
        # Android closes a connection after ten seconds without incoming bytes.
        # A successfully created source that produces no frames must be retired
        # sooner, even though the overall reconnect deadline is longer.
        self.now = self.stream.capture_attempt_until
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "waiting")
        self.assertEqual(self.stream.resume_deadline, original_deadline)
        self.assertIsNot(self.stream.pipeline, stalled_desktop)
        stalled_desktop.set_state.assert_has_calls([call("PLAYING"), call("NULL")])
        self.assertIn("pattern=black", self.Gst.parse_launch.call_args.args[0])
        self.connection.shutdown.assert_not_called()
        self.now = self.stream.retry_at
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.begin_desktop.call_count, 2)

    def test_capture_error_during_resume_retries_within_original_deadline(self):
        self.lock()
        self.unlock()
        deadline = self.stream.resume_deadline
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.stream.tick()
        self.now += 1
        self.stream.capture_failed("new capture closed")
        self.assertEqual(self.stream.phase, "grace")
        self.assertEqual(self.stream.resume_deadline, deadline)
        self.now += self.stream.LOCK_GRACE
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "resuming")
        self.assertEqual(self.stream.begin_desktop.call_count, 2)
        self.assertEqual(self.stream.resume_deadline, deadline)

    def test_resume_without_new_video_frame_expires_even_if_capture_creation_succeeds(self):
        self.lock()
        self.unlock()
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.stream.tick()
        self.now = self.stream.resume_deadline
        self.assertFalse(self.stream.tick())
        self.assertIn("не передал видеокадры", self.stream.error)
        self.stream.restore_layout.assert_not_called()

    def test_relocking_during_resume_cancels_old_deadline(self):
        self.lock()
        self.unlock()
        deadline = self.stream.resume_deadline
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.stream.tick()
        self.lock()
        self.now = deadline + 100
        self.assertTrue(self.stream.tick())
        self.assertEqual(self.stream.phase, "locked")
        self.assertEqual(self.stream.resume_deadline, 0)
        self.assertFalse(self.stream.stopping.is_set())

    def test_non_lock_capture_failure_remains_terminal_after_short_grace(self):
        self.stream.on_message(self.stream.bus, self.error_message("real encoder failure"))
        self.assertEqual(self.stream.phase, "grace")
        self.now += self.stream.LOCK_GRACE
        self.assertFalse(self.stream.tick())
        self.assertIn("real encoder failure", self.stream.error)
        self.assertTrue(self.stream.stopping.is_set())
        self.loop.quit.assert_called_once()

    def test_test_pattern_failure_does_not_wait_for_lock(self):
        self.stream.test_pattern = True
        self.assertFalse(self.stream.capture_failed("synthetic pipeline failed"))
        self.assertEqual(self.stream.error, "synthetic pipeline failed")
        self.assertTrue(self.stream.stopping.is_set())
        self.Gst.parse_launch.assert_not_called()

    def test_placeholder_error_is_immediately_terminal(self):
        self.lock()
        self.stream.on_message(self.stream.bus, self.error_message("placeholder encoder failed"))
        self.assertTrue(self.stream.stopping.is_set())
        self.assertIn("placeholder encoder failed", self.stream.error)

    def test_placeholder_eos_is_immediately_terminal(self):
        self.lock()
        self.stream.on_message(self.stream.bus, SimpleNamespace(type="EOS"))
        self.assertTrue(self.stream.stopping.is_set())
        self.assertIn("экрана блокировки", self.stream.error)

    def test_placeholder_start_failure_is_terminal(self):
        self.Gst.parse_launch.side_effect = RuntimeError("missing textoverlay")
        self.lock()
        self.assertTrue(self.stream.stopping.is_set())
        self.assertIn("missing textoverlay", self.stream.error)

    def test_retired_bus_errors_do_not_affect_placeholder_or_resumed_capture(self):
        old_bus, old_keeper_bus = self.stream.bus, self.stream.keeper_bus
        self.lock()
        placeholder_bus = self.stream.bus
        for bus in (old_bus, old_keeper_bus):
            self.stream.on_message(bus, self.error_message())
        self.assertEqual(self.stream.phase, "locked")
        self.assertIsNone(self.stream.error)
        self.unlock()
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.stream.tick()
        for bus in (old_bus, old_keeper_bus, placeholder_bus):
            self.stream.on_message(bus, self.error_message())
            self.stream.on_message(bus, SimpleNamespace(type="EOS"))
        self.assertEqual(self.stream.phase, "resuming")
        self.assertIsNone(self.stream.error)

    def test_stop_while_locked_blocks_reconnect_and_run_releases_owned_resources(self):
        self.stream.window_restorer = Mock()
        self.lock()
        placeholder = self.stream.pipeline
        self.stream.begin_desktop = Mock(side_effect=self.begin_desktop)
        self.stream.create_monitor = Mock()
        self.loop.run.side_effect = self.stream.stop
        with patch("usbdisplay.host.signal.signal", return_value=object()):
            self.stream.run()
        self.assertTrue(self.stream.stopping.is_set())
        self.assertIsNone(self.stream.pipeline)
        self.assertIsNone(self.stream.session)
        placeholder.set_state.assert_has_calls([call("PLAYING"), call("NULL")])
        self.connection.shutdown.assert_called_once()
        self.stream.screen_lock.close.assert_called_once()
        self.stream.window_restorer.close.assert_called_once()
        self.unlock()
        self.assertFalse(self.stream.tick())
        self.stream.begin_desktop.assert_not_called()

    def test_unavailable_lock_state_stops_instead_of_assuming_unlocked(self):
        self.lock()
        self.stream.screen_lock.refresh.side_effect = RuntimeError("GetActive unavailable")
        self.assertFalse(self.stream.tick())
        self.assertEqual(self.stream.error, "GetActive unavailable")
        self.assertTrue(self.stream.stopping.is_set())

    def test_starting_while_locked_never_starts_desktop_capture(self):
        watcher = Mock()
        watcher.start.return_value = True
        self.stream.begin_desktop = Mock()
        with patch("usbdisplay.resume.ScreenLock", return_value=watcher):
            self.stream.create_monitor()
        self.assertEqual(self.stream.phase, "locked")
        self.stream.begin_desktop.assert_not_called()
        self.assertIs(self.stream.dbus, watcher.connection)

    def test_lock_race_between_create_session_and_record_virtual_enters_grace(self):
        watcher = Mock()
        watcher.start.return_value = False
        self.stream.begin_desktop = Mock(side_effect=DbusError(
            "org.freedesktop.DBus.Error.UnknownObject: session disappeared during RecordVirtual"))
        with patch("usbdisplay.resume.ScreenLock", return_value=watcher):
            self.stream.create_monitor()
        self.assertEqual(self.stream.phase, "grace")
        self.assertIn("UnknownObject", self.stream.pending_capture_error)
        self.assertIsNone(self.stream.error)
        self.assertFalse(self.stream.stopping.is_set())
        self.lock()
        self.assertEqual(self.stream.phase, "locked")
        self.assertIsNone(self.stream.pending_capture_error)

    def test_run_closes_lock_watcher_even_when_capture_start_raises(self):
        self.stream.create_monitor = Mock(side_effect=RuntimeError("setup failed"))
        with patch("usbdisplay.host.signal.signal", return_value=object()):
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                self.stream.run()
        self.stream.screen_lock.close.assert_called_once()
        self.connection.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()

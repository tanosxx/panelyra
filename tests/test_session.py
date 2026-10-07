from copy import deepcopy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from usbdisplay.session import ScreenLock, capture_layout, restore_parameters


class Variant:
    def __init__(self, *args):
        self.value = args[-1]

    def unpack(self):
        return self.value


class ScreenLockTests(unittest.TestCase):
    def setUp(self):
        self.dbus = Mock()
        self.dbus.signal_subscribe.return_value = 41
        self.dbus.call_sync.return_value = Variant((False,))
        self.Gio = SimpleNamespace(
            DBusCallFlags=SimpleNamespace(NONE=0),
            DBusSignalFlags=SimpleNamespace(NONE=0),
            BusType=SimpleNamespace(SESSION=0),
            bus_get_sync=Mock(return_value=self.dbus))
        self.callback = Mock()
        self.watcher = ScreenLock(self.Gio, self.callback)
        # No system bus on this fixture: exercise the ScreenSaver-only fallback
        # without importing GI or contacting any real desktop service.
        self.watcher.GLib = SimpleNamespace(Variant=Variant)

    def emit(self, active):
        self.watcher._on_active_changed(None, None, None, None, None, Variant((active,)))

    def test_subscribes_before_initial_read_without_invoking_callback(self):
        self.assertFalse(self.watcher.start())
        self.assertEqual([entry[0] for entry in self.dbus.mock_calls],
                         ["signal_subscribe", "call_sync"])
        subscription = self.dbus.signal_subscribe.call_args.args
        self.assertEqual(subscription[:5], (
            "org.gnome.ScreenSaver", "org.gnome.ScreenSaver", "ActiveChanged",
            "/org/gnome/ScreenSaver", None))
        self.assertEqual(self.dbus.call_sync.call_args.args[:4], (
            "org.gnome.ScreenSaver", "/org/gnome/ScreenSaver",
            "org.gnome.ScreenSaver", "GetActive"))
        self.callback.assert_not_called()

    def test_repeated_signals_deliver_only_lock_transitions(self):
        self.watcher.start()
        for active in (False, True, True, False, False, True):
            self.emit(active)
        self.assertEqual(self.callback.call_args_list, [call(True), call(False), call(True)])
        self.assertTrue(self.watcher.active)
        self.assertTrue(self.watcher.start())
        self.dbus.signal_subscribe.assert_called_once()

    def test_initially_locked_and_refresh_do_not_invoke_callback(self):
        self.dbus.call_sync.return_value = Variant((True,))
        self.assertTrue(self.watcher.start())
        self.dbus.call_sync.return_value = Variant((False,))
        self.assertFalse(self.watcher.refresh())
        self.assertFalse(self.watcher.active)
        self.callback.assert_not_called()

    def test_failed_initial_query_releases_subscription_and_reports_clear_error(self):
        self.dbus.call_sync.side_effect = OSError("ServiceUnknown")
        with self.assertRaisesRegex(RuntimeError, "блокировку GNOME.*ServiceUnknown"):
            self.watcher.start()
        self.dbus.signal_unsubscribe.assert_called_once_with(41)
        self.assertIsNone(self.watcher.subscription)
        self.callback.assert_not_called()
        self.watcher.close()
        self.dbus.signal_unsubscribe.assert_called_once()

    def test_failed_refresh_does_not_assume_desktop_unlocked(self):
        self.dbus.call_sync.return_value = Variant((True,))
        self.watcher.start()
        self.dbus.call_sync.side_effect = OSError("Disconnected")
        with self.assertRaisesRegex(RuntimeError, "Disconnected"):
            self.watcher.refresh()
        self.assertTrue(self.watcher.active)
        self.callback.assert_not_called()

    def test_invalid_initial_state_is_rejected(self):
        self.dbus.call_sync.return_value = Variant(("false",))
        with self.assertRaisesRegex(RuntimeError, "неверное состояние"):
            self.watcher.start()
        self.dbus.signal_unsubscribe.assert_called_once_with(41)

    def test_close_is_idempotent_and_ignores_late_signals(self):
        self.watcher.start()
        self.watcher.close()
        self.watcher.close()
        self.emit(True)
        self.dbus.signal_unsubscribe.assert_called_once_with(41)
        self.callback.assert_not_called()

    def test_supplied_connection_does_not_open_another_bus(self):
        watcher = ScreenLock(self.Gio, self.callback, self.dbus,
                             GLib=SimpleNamespace(Variant=Variant))
        watcher.start()
        self.Gio.bus_get_sync.assert_not_called()


class LogindLockTests(unittest.TestCase):
    def setUp(self):
        self.saver = Mock()
        self.saver.signal_subscribe.return_value = 41
        self.saver.call_sync.return_value = Variant((False,))
        self.system = Mock()
        self.system.signal_subscribe.return_value = 42
        self.system.call_sync.side_effect = self.reply
        self.locked = False
        self.no_pid_session = False
        self.display_properties = {"User": (os.getuid(), "/user/current"),
                                   "Type": "wayland", "Class": "user"}
        self.Gio = SimpleNamespace(
            DBusCallFlags=SimpleNamespace(NONE=0),
            DBusSignalFlags=SimpleNamespace(NONE=0),
            BusType=SimpleNamespace(SESSION=0, SYSTEM=1),
            bus_get_sync=Mock(side_effect=lambda bus, _: self.system if bus else self.saver))
        self.callback = Mock()
        self.watcher = ScreenLock(self.Gio, self.callback, GLib=SimpleNamespace(Variant=Variant))

    def reply(self, _destination, _path, _interface, method, parameters, *_args):
        if method == "GetSessionByPID":
            if self.no_pid_session:
                raise OSError("NoSessionForPID")
            return Variant(("/org/freedesktop/login1/session/current",))
        if method == "GetUser":
            return Variant(("/org/freedesktop/login1/user/current",))
        if method == "GetAll":
            return Variant((self.display_properties,))
        if method == "Get" and parameters.unpack()[1] == "Display":
            return Variant((("display", "/org/freedesktop/login1/session/display"),))
        if method == "Get" and parameters.unpack()[1] == "LockedHint":
            return Variant((self.locked,))
        raise AssertionError(f"Unexpected logind method: {method}")

    def emit_locked(self, locked):
        self.locked = locked
        self.watcher._on_lock_changed(None, None, None, None, None, Variant((
            "org.freedesktop.login1.Session", {"LockedHint": locked}, [])))

    def emit_active(self, active):
        self.saver.call_sync.return_value = Variant((active,))
        self.watcher._on_active_changed(None, None, None, None, None, Variant((active,)))

    def test_subscribes_before_query_and_selects_session_for_current_process(self):
        self.assertFalse(self.watcher.start())
        self.assertEqual([entry[0] for entry in self.system.mock_calls],
                         ["call_sync", "signal_subscribe", "call_sync"])
        lookup, read = self.system.call_sync.call_args_list
        self.assertEqual(lookup.args[3], "GetSessionByPID")
        self.assertEqual(lookup.args[4].unpack(), (os.getpid(),))
        self.assertEqual(read.args[1], "/org/freedesktop/login1/session/current")
        self.assertEqual(read.args[4].unpack(), ("org.freedesktop.login1.Session", "LockedHint"))
        self.assertEqual(self.system.signal_subscribe.call_args.args[3:5],
                         ("/org/freedesktop/login1/session/current", "org.freedesktop.login1.Session"))
        self.callback.assert_not_called()

    def test_fast_lock_unlock_is_detected_without_screensaver_animation(self):
        self.watcher.start()
        self.emit_locked(True)
        self.emit_locked(False)
        self.assertEqual(self.callback.call_args_list, [call(True), call(False)])
        self.assertFalse(self.watcher.active)

    def test_remains_locked_until_both_shield_and_session_are_unlocked(self):
        self.watcher.start()
        self.emit_locked(True)
        self.emit_active(True)
        self.emit_locked(False)
        self.assertTrue(self.watcher.active)
        self.callback.assert_called_once_with(True)
        self.emit_active(False)
        self.assertEqual(self.callback.call_args_list, [call(True), call(False)])

    def test_logind_can_supply_initial_state_when_screensaver_unavailable(self):
        self.saver.call_sync.side_effect = OSError("ServiceUnknown")
        self.locked = True
        self.assertTrue(self.watcher.start())
        self.callback.assert_not_called()
        self.emit_locked(False)
        self.callback.assert_called_once_with(False)

    def test_both_sources_unavailable_fails_and_unsubscribes_both(self):
        self.saver.call_sync.side_effect = OSError("ScreenSaver disconnected")
        def denied_property(*args):
            if args[3] == "Get":
                raise OSError("logind disconnected")
            return self.reply(*args)
        self.system.call_sync.side_effect = denied_property
        with self.assertRaisesRegex(RuntimeError, "ScreenSaver disconnected.*logind disconnected"):
            self.watcher.start()
        self.saver.signal_unsubscribe.assert_called_once_with(41)
        self.system.signal_unsubscribe.assert_called_once_with(42)

    def test_failed_locked_source_query_does_not_clear_known_lock(self):
        self.watcher.start()
        self.emit_locked(True)
        self.system.call_sync.side_effect = OSError("temporarily unavailable")
        self.assertTrue(self.watcher.refresh())
        self.emit_active(False)
        self.callback.assert_called_once_with(True)

    def test_invalidated_property_is_reread_before_notifying(self):
        self.watcher.start()
        self.locked = True
        self.watcher._on_lock_changed(None, None, None, None, None, Variant((
            "org.freedesktop.login1.Session", {}, ["LockedHint"])))
        self.callback.assert_called_once_with(True)

    def test_close_unsubscribes_both_and_ignores_late_signals(self):
        self.watcher.start()
        self.watcher.close()
        self.watcher.close()
        self.emit_locked(True)
        self.emit_active(True)
        self.saver.signal_unsubscribe.assert_called_once_with(41)
        self.system.signal_unsubscribe.assert_called_once_with(42)
        self.callback.assert_not_called()

    def test_systemd_service_uses_current_users_designated_display(self):
        self.no_pid_session = True
        self.assertFalse(self.watcher.start())
        methods = [entry.args[3] for entry in self.system.call_sync.call_args_list]
        self.assertEqual(methods, ["GetSessionByPID", "GetUser", "Get", "GetAll", "Get"])
        get_user = self.system.call_sync.call_args_list[1]
        self.assertEqual(get_user.args[4].unpack(), (os.getuid(),))
        self.assertEqual(self.watcher.session_path, "/org/freedesktop/login1/session/display")
        self.assertEqual(self.system.signal_subscribe.call_args.args[3], self.watcher.session_path)
        self.assertNotIn("ListSessions", methods)

    def test_wrong_user_or_non_user_non_wayland_display_is_not_watched(self):
        self.no_pid_session = True
        for changed in ({"User": (os.getuid() + 1, "/other")}, {"Type": "tty"},
                        {"Class": "greeter"}):
            with self.subTest(changed=changed):
                watcher = ScreenLock(self.Gio, self.callback, GLib=SimpleNamespace(Variant=Variant))
                properties = {**self.display_properties, **changed}
                with patch.object(self, "display_properties", properties):
                    self.assertFalse(watcher.start())  # ScreenSaver fallback.
                self.assertIsNone(watcher.lock_subscription)
                watcher.close()
        self.system.signal_subscribe.assert_not_called()


def spec(connector):
    return (connector, "vendor", "product", "serial")


def mode(name, width, height, rate=60.0, current=True, scales=(1.0, 1.25, 2.0), **properties):
    return (name, width, height, rate, 1.0, list(scales),
            {"is-current": current, **properties})


def state(connector="Meta-1", suffix="old", serial=5):
    physical = (spec("DP-1"), [mode("physical-" + suffix, 3840, 2160)],
                {"color-mode": 1, "is-underscanning": False})
    virtual = (spec(connector), [mode("virtual-" + suffix, 1280, 800, rate=30.0)], {})
    inactive = (spec("HDMI-1"), [mode("inactive-" + suffix, 1920, 1080, current=False)], {})
    return (serial, [physical, virtual, inactive], [
        (0, 0, 2.0, 0, True, [physical[0]], {}),
        (1920, 0, 1.25, 1, False, [virtual[0]], {}),
    ], {"layout-mode": 1, "supports-changing-layout-mode": True})


class MonitorLayoutTests(unittest.TestCase):
    def test_reconnect_preserves_geometry_and_modes_while_remapping_owned_connector(self):
        saved = capture_layout(state(), "Meta-1")
        current = state("Meta-8", "new", serial=27)
        # GNOME may temporarily choose a different primary and auto-position.
        current[2][:] = [
            (0, 0, 1.0, 0, False, [spec("DP-1")], {}),
            (3840, 0, 1.0, 0, True, [spec("Meta-8")], {}),
        ]
        result = restore_parameters(current, saved, "Meta-8")
        self.assertEqual(result, (27, 1, [
            (0, 0, 2.0, 0, True,
             [("DP-1", "physical-new", {"color-mode": 1, "underscanning": False})]),
            (1920, 0, 1.25, 1, False, [("Meta-8", "virtual-new", {})]),
        ], {"layout-mode": 1}))
        self.assertNotIn("HDMI-1", [output[0] for logical in result[2] for output in logical[5]])

    def test_snapshot_is_independent_of_later_changes_to_dbus_reply(self):
        original = state()
        saved = capture_layout(original, "Meta-1")
        original[1][0][2]["color-mode"] = 0
        original[2].clear()
        original[3]["layout-mode"] = 2
        restored = restore_parameters(state("Meta-2"), saved, "Meta-2")
        self.assertEqual(restored[2][0][5][0][2]["color-mode"], 1)
        self.assertEqual(restored[3], {"layout-mode": 1})
        self.assertEqual(len(restored[2]), 2)

    def test_changed_mode_ids_match_dimensions_and_refresh_not_list_position(self):
        saved = capture_layout(state(), "Meta-1")
        current = state("Meta-2", "new")
        current[1][1][1][:] = [
            mode("wrong-size", 1920, 1080, rate=30.0),
            mode("wrong-rate", 1280, 800, rate=60.0),
            mode("matching", 1280, 800, rate=30.0, current=False),
        ]
        result = restore_parameters(current, saved, "Meta-2")
        self.assertEqual(result[2][1][5], [("Meta-2", "matching", {})])

    def test_added_removed_or_replaced_other_monitor_refuses_old_layout(self):
        saved = capture_layout(state(), "Meta-1")
        for kind in ("added", "removed", "replaced", "other-virtual"):
            with self.subTest(kind=kind):
                current = state("Meta-2")
                if kind == "added":
                    current[1].append((spec("DP-2"), [], {}))
                elif kind == "removed":
                    current[1].pop()  # Even an inactive monitor matters.
                elif kind == "other-virtual":
                    current[1].append((spec("Meta-9"), [], {}))
                else:
                    current[1][0] = (("DP-1", "another", "display", "serial"),
                                     current[1][0][1], {})
                before = deepcopy(current)
                with self.assertRaisesRegex(RuntimeError, "Состав остальных мониторов изменился"):
                    restore_parameters(current, saved, "Meta-2")
                self.assertEqual(current, before)

    def test_missing_saved_resolution_refresh_scale_or_scan_mode_refuses_layout(self):
        saved = capture_layout(state(), "Meta-1")
        for replacement in (
                mode("different-size", 1280, 720, rate=30),
                mode("different-rate", 1280, 800, rate=60),
                mode("different-scale", 1280, 800, rate=30, scales=(1.0,)),
                mode("interlaced", 1280, 800, rate=30, **{"is-interlaced": True}),
                mode("vrr", 1280, 800, rate=30, **{"refresh-rate-mode": "variable"})):
            with self.subTest(mode=replacement[0]):
                current = state("Meta-2")
                current[1][1][1][:] = [replacement]
                with self.assertRaisesRegex(RuntimeError, "режим или масштаб.*Meta-2"):
                    restore_parameters(current, saved, "Meta-2")

    def test_missing_or_disabled_owned_monitor_cannot_be_captured(self):
        with self.assertRaisesRegex(RuntimeError, "отсутствует"):
            capture_layout(state(), "Meta-99")
        current = state()
        current[2].pop()
        with self.assertRaisesRegex(RuntimeError, "выключен"):
            capture_layout(current, "Meta-1")

    def test_missing_new_connector_does_not_reconfigure_physical_monitors(self):
        saved = capture_layout(state(), "Meta-1")
        with self.assertRaisesRegex(RuntimeError, "ещё не появился"):
            restore_parameters(state(), saved, "Meta-2")

    def test_saved_coordinate_system_is_preserved_or_rejected_if_unsupported(self):
        saved = capture_layout(state(), "Meta-1")
        current = state("Meta-2")
        current[3]["layout-mode"] = 2
        self.assertEqual(restore_parameters(current, saved, "Meta-2")[3], {"layout-mode": 1})
        current[3]["supports-changing-layout-mode"] = False
        with self.assertRaisesRegex(RuntimeError, "режим расположения"):
            restore_parameters(current, saved, "Meta-2")

    def test_mirrored_logical_monitor_keeps_both_outputs(self):
        before = state()
        before[1][1][1][:] = [mode("mirror", 3840, 2160)]
        before[2][:] = [(0, 0, 2.0, 0, True, [spec("DP-1"), spec("Meta-1")], {})]
        saved = capture_layout(before, "Meta-1")
        current = state("Meta-2", "new")
        current[1][1][1][:] = [mode("new-mirror", 3840, 2160)]
        result = restore_parameters(current, saved, "Meta-2")
        self.assertEqual(len(result[2]), 1)
        self.assertEqual([output[:2] for output in result[2][0][5]],
                         [("DP-1", "physical-new"), ("Meta-2", "new-mirror")])


if __name__ == "__main__":
    unittest.main()

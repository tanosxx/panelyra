"""GNOME lock notifications and temporary restoration of an owned output's layout."""

from dataclasses import dataclass
import math
import os


class ScreenLock:
    """Watch GNOME's lock state on the caller's GLib main context.

    Combine the animated ScreenSaver shield and logind's immediate LockedHint.
    Subscribe before the initial reads so a transition during setup is not lost.
    ``start`` and ``refresh`` return the state without invoking the callback;
    subsequent state changes invoke ``callback(active)``. No lock, unlock or
    screen-saver inhibition method is called.
    """

    BUS_NAME = INTERFACE = "org.gnome.ScreenSaver"
    PATH = "/org/gnome/ScreenSaver"
    LOGIN_NAME = "org.freedesktop.login1"
    LOGIN_INTERFACE = "org.freedesktop.login1.Session"
    PROPERTIES = "org.freedesktop.DBus.Properties"

    def __init__(self, Gio, callback, connection=None, GLib=None):
        self.Gio = Gio
        self.GLib = GLib
        self.callback = callback
        self.connection = connection
        self.subscription = None
        self.system_connection = None
        self.lock_subscription = None
        self.session_path = None
        self.saver_active = None
        self.session_locked = None
        self.active = None
        self.started = False

    def start(self):
        if self.started:
            return self.active
        try:
            try:
                if self.connection is None:
                    self.connection = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None)
                self.subscription = self.connection.signal_subscribe(
                    self.BUS_NAME, self.INTERFACE, "ActiveChanged", self.PATH, None,
                    self.Gio.DBusSignalFlags.NONE, self._on_active_changed)
            except Exception:
                pass  # logind can still supply the current session's lock state.
            try:
                if self.GLib is None:
                    from gi.repository import GLib
                    self.GLib = GLib
                self.system_connection = self.Gio.bus_get_sync(self.Gio.BusType.SYSTEM, None)
                self.session_path = self._find_session()
                self.lock_subscription = self.system_connection.signal_subscribe(
                    self.LOGIN_NAME, self.PROPERTIES, "PropertiesChanged", self.session_path,
                    self.LOGIN_INTERFACE, self.Gio.DBusSignalFlags.NONE,
                    self._on_lock_changed)
            except Exception:
                self.session_path = None  # Older/remote sessions may lack logind.
            active = self.refresh()
            self.started = True
            return active
        except Exception as error:
            self.close()
            if isinstance(error, RuntimeError):
                raise
            raise RuntimeError(f"Не удалось наблюдать за блокировкой GNOME: {error}") from error

    def _system_call(self, path, interface, method, parameters):
        return self.system_connection.call_sync(
            self.LOGIN_NAME, path, interface, method, parameters,
            None, self.Gio.DBusCallFlags.NONE, 3000, None).unpack()

    def _find_session(self):
        try:
            return self._system_call(
                "/org/freedesktop/login1", self.LOGIN_NAME + ".Manager", "GetSessionByPID",
                self.GLib.Variant("(u)", (os.getpid(),)))[0]
        except Exception:
            # Processes launched as systemd user services have no session PID
            # mapping. Ask logind for this user's designated graphical display;
            # never pick an arbitrary entry from ListSessions.
            user_path = self._system_call(
                "/org/freedesktop/login1", self.LOGIN_NAME + ".Manager", "GetUser",
                self.GLib.Variant("(u)", (os.getuid(),)))[0]
            _session_id, session_path = self._system_call(
                user_path, self.PROPERTIES, "Get",
                self.GLib.Variant("(ss)", (self.LOGIN_NAME + ".User", "Display")))[0]
            if not session_path or session_path == "/":
                raise RuntimeError("У пользователя нет графического сеанса logind")
            properties = self._system_call(
                session_path, self.PROPERTIES, "GetAll",
                self.GLib.Variant("(s)", (self.LOGIN_INTERFACE,)))[0]
            if (properties.get("User", (None,))[0] != os.getuid()
                    or properties.get("Type") != "wayland"
                    or properties.get("Class") != "user"):
                raise RuntimeError("Графический сеанс logind не принадлежит текущему пользователю Wayland")
            return session_path

    def refresh(self):
        successes, errors = 0, []
        if self.subscription is not None:
            try:
                result = self.connection.call_sync(
                    self.BUS_NAME, self.PATH, self.INTERFACE, "GetActive", None,
                    None, self.Gio.DBusCallFlags.NONE, 3000, None).unpack()
                self.saver_active = self._boolean(result, "GetActive")
                successes += 1
            except Exception as error:
                errors.append(str(error))
        if self.lock_subscription is not None:
            try:
                self.session_locked = self._read_session_locked()
                successes += 1
            except Exception as error:
                errors.append(str(error))
        if not successes:
            detail = "; ".join(errors) or "службы состояния блокировки недоступны"
            raise RuntimeError(f"Не удалось проверить блокировку GNOME: {detail}")
        # Keep a previously locked source locked if its query has failed. Only
        # a successful read/signal can clear it; an error never means unlocked.
        self.active = bool(self.saver_active or self.session_locked)
        return self.active

    @staticmethod
    def _boolean(result, source):
        if len(result) != 1 or not isinstance(result[0], bool):
            raise ValueError(f"{source} вернул неверное состояние")
        return result[0]

    def _read_session_locked(self):
        result = self.system_connection.call_sync(
            self.LOGIN_NAME, self.session_path, self.PROPERTIES, "Get",
            self.GLib.Variant("(ss)", (self.LOGIN_INTERFACE, "LockedHint")),
            None, self.Gio.DBusCallFlags.NONE, 3000, None).unpack()
        return self._boolean(result, "LockedHint")

    def _notify(self):
        active = bool(self.saver_active or self.session_locked)
        if active != self.active:
            self.active = active
            if self.started:
                self.callback(active)

    def _on_active_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        if self.subscription is None:
            return
        values = parameters.unpack()
        if len(values) != 1 or not isinstance(values[0], bool):
            return
        self.saver_active = values[0]
        self._notify()

    def _on_lock_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        if self.lock_subscription is None:
            return
        interface, changed, invalidated = parameters.unpack()
        if interface != self.LOGIN_INTERFACE:
            return
        if isinstance(changed.get("LockedHint"), bool):
            self.session_locked = changed["LockedHint"]
        elif "LockedHint" in invalidated:
            try:
                self.session_locked = self._read_session_locked()
            except Exception:
                return  # Retain last known state until a refresh can resolve it.
        else:
            return
        self._notify()

    def close(self):
        self.started = False
        subscription, self.subscription = self.subscription, None
        lock_subscription, self.lock_subscription = self.lock_subscription, None
        if subscription is not None:
            self.connection.signal_unsubscribe(subscription)
        if lock_subscription is not None:
            self.system_connection.signal_unsubscribe(lock_subscription)


@dataclass(frozen=True)
class _MonitorMode:
    connector: str
    width: int
    height: int
    refresh: float
    interlaced: bool
    refresh_mode: str
    properties: tuple


@dataclass(frozen=True)
class _LogicalMonitor:
    x: int
    y: int
    scale: float
    transform: int
    primary: bool
    monitors: tuple


@dataclass(frozen=True)
class LayoutSnapshot:
    owned_connector: str
    other_monitors: frozenset
    logical_monitors: tuple
    layout_mode: int | None


def _monitor_map(state):
    monitors = {monitor[0][0]: monitor for monitor in state[1]}
    if len(monitors) != len(state[1]):
        raise RuntimeError("GNOME вернул повторяющиеся разъёмы мониторов")
    return monitors


def _mode_properties(properties):
    # Unspecified color-mode resets to the default in ApplyMonitorsConfig.
    # Preserve the settings that this API accepts instead of passing read-only
    # GetCurrentState properties back to Mutter.
    result = []
    if "is-underscanning" in properties:
        result.append(("underscanning", properties["is-underscanning"]))
    if "color-mode" in properties:
        result.append(("color-mode", properties["color-mode"]))
    return tuple(result)


def capture_layout(state, owned_connector):
    """Copy an unpacked DisplayConfig.GetCurrentState reply for later reconnect.

    Includes inactive connectors in the topology check. A reconnect must never
    reapply an old desktop layout after another monitor was plugged or removed.
    Mode IDs are deliberately omitted: they can change when outputs reconnect.
    """
    monitors = _monitor_map(state)
    if owned_connector not in monitors:
        raise RuntimeError("Виртуальный монитор отсутствует в конфигурации GNOME")
    logicals = []
    used = set()
    for x, y, scale, transform, primary, specs, _properties in state[2]:
        modes = []
        for spec in specs:
            connector = spec[0]
            if connector in used or connector not in monitors:
                raise RuntimeError("Некорректный список активных мониторов GNOME")
            used.add(connector)
            monitor = monitors[connector]
            current = [mode for mode in monitor[1] if mode[6].get("is-current")]
            if len(current) != 1:
                raise RuntimeError(f"Не удалось определить текущий режим монитора {connector}")
            mode = current[0]
            modes.append(_MonitorMode(
                connector, mode[1], mode[2], mode[3],
                mode[6].get("is-interlaced", False),
                mode[6].get("refresh-rate-mode", "fixed"), _mode_properties(monitor[2])))
        logicals.append(_LogicalMonitor(x, y, scale, transform, primary, tuple(modes)))
    if owned_connector not in used:
        raise RuntimeError("Виртуальный монитор выключен в конфигурации GNOME")
    return LayoutSnapshot(
        owned_connector,
        frozenset(tuple(monitor[0]) for connector, monitor in monitors.items()
                  if connector != owned_connector),
        tuple(logicals), state[3].get("layout-mode"))


def restore_parameters(current_state, saved, new_connector):
    """Build unpacked ApplyMonitorsConfig arguments, without changing anything.

    The returned tuple is ``(serial, 1, logical_monitors, properties)``; method 1
    is temporary. Dictionary values are plain Python values. The DBus caller
    wraps ``layout-mode``/``color-mode`` as ``u`` and ``underscanning`` as ``b``
    GLib.Variants before packing ``(uua(iiduba(ssa{sv}))a{sv})``.
    """
    monitors = _monitor_map(current_state)
    if new_connector not in monitors:
        raise RuntimeError("Новый виртуальный монитор ещё не появился в GNOME")
    other_monitors = frozenset(tuple(monitor[0]) for connector, monitor in monitors.items()
                              if connector != new_connector)
    if other_monitors != saved.other_monitors:
        raise RuntimeError("Состав остальных мониторов изменился; прежняя раскладка не восстановлена")

    logicals = []
    for logical in saved.logical_monitors:
        outputs = []
        for saved_mode in logical.monitors:
            connector = (new_connector if saved_mode.connector == saved.owned_connector
                         else saved_mode.connector)
            matches = [mode for mode in monitors[connector][1]
                       if mode[1:3] == (saved_mode.width, saved_mode.height)
                       and math.isclose(mode[3], saved_mode.refresh, rel_tol=1e-6, abs_tol=0.01)
                       and mode[6].get("is-interlaced", False) == saved_mode.interlaced
                       and mode[6].get("refresh-rate-mode", "fixed") == saved_mode.refresh_mode
                       and any(math.isclose(scale, logical.scale, abs_tol=1e-6)
                               for scale in mode[5])]
            if not matches:
                raise RuntimeError(f"Прежний режим или масштаб монитора {connector} недоступен")
            mode = next((mode for mode in matches if mode[6].get("is-current")), matches[0])
            outputs.append((connector, mode[0], dict(saved_mode.properties)))
        logicals.append((logical.x, logical.y, logical.scale, logical.transform,
                         logical.primary, outputs))

    properties = {}
    current_mode = current_state[3].get("layout-mode")
    layout_mode = saved.layout_mode if saved.layout_mode is not None else current_mode
    if layout_mode is not None:
        if (layout_mode != current_mode
                and not current_state[3].get("supports-changing-layout-mode", False)):
            raise RuntimeError("Прежний режим расположения мониторов больше не поддерживается")
        properties["layout-mode"] = layout_mode
    return current_state[0], 1, logicals, properties

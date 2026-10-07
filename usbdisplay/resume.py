"""Keep the USB video connection alive across GNOME lock/unlock cycles."""

import time

from .host import Stream
from .session import ScreenLock, capture_layout, restore_parameters
from .window_restore import WindowRestorer


class LockResumingStream(Stream):
    # Mutter closes capture before Shell emits ActiveChanged (after animation).
    LOCK_GRACE = 2.0
    RESUME_TIMEOUT = 20.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen_lock = None
        self.window_restorer = None
        self.phase = "starting"
        self.saved_layout = None
        self.restore_layout_pending = False
        self.desktop_first_frame = 0
        self.pending_capture_error = None
        self.grace_until = 0
        self.resume_deadline = 0
        self.retry_at = 0
        self.capture_attempt_until = 0
        self.connector = None
        self.placeholder_pipeline = self.placeholder_bus = None

    def create_monitor(self):
        self.screen_lock = ScreenLock(self.Gio, self.on_lock_changed, GLib=self.GLib)
        active = self.screen_lock.start()
        self.dbus = self.screen_lock.connection
        self.window_restorer = WindowRestorer(self.Gio, self.GLib, self.dbus)
        if active:
            self.on_lock_changed(True)
        else:
            try:
                self.begin_desktop()
            except (self.GLib.Error, RuntimeError) as error:
                self.capture_failed(str(error))

    def begin_desktop(self):
        self.desktop_first_frame = self.frames
        self.cursor_ready_reported = True  # Only desktop frames announce ready.
        self.monitors_before = {monitor[0][0] for monitor in self.monitor_state()}
        super().create_monitor()

    def show_placeholder(self, phase):
        self.phase = phase
        self.cursor_ready_reported = True
        self.close_capture()
        try:
            self.start_pipeline(lock_screen=True)
        except Exception as error:
            self.fail(f"Не удалось показать экран блокировки: {error}")

    def close_placeholder(self):
        pipeline, bus = self.placeholder_pipeline, self.placeholder_bus
        self.placeholder_pipeline = self.placeholder_bus = None
        if pipeline is not None:
            pipeline.set_state(self.Gst.State.NULL)
        if bus is not None:
            bus.remove_signal_watch()

    def close_capture(self):
        self.close_placeholder()
        super().close_capture()

    def start_pipeline(self, node=None, *, lock_screen=False):
        # Keep sending the placeholder throughout monitor/node negotiation.
        # Stop its streaming thread before a new encoder can write to TCP.
        self.close_placeholder()
        super().start_pipeline(node, lock_screen=lock_screen)
        if not lock_screen:
            self.desktop_first_frame = self.frames

    def on_lock_changed(self, active):
        if self.stopping.is_set():
            return
        if active:
            if self.phase == "locked":
                return
            self.restore_layout_pending = self.saved_layout is not None
            self.pending_capture_error = None
            self.resume_deadline = 0
            self.show_placeholder("locked")
            print("Компьютер заблокирован. Передача продолжится после разблокировки.", flush=True)
            if self.on_status is not None:
                self.on_status("locked")
        elif self.phase in ("locked", "grace"):
            self.phase = "waiting"
            self.resume_deadline = time.monotonic() + self.RESUME_TIMEOUT
            self.retry_at = time.monotonic()
            if self.on_status is not None:
                self.on_status("resuming")

    def capture_failed(self, message):
        if self.test_pattern or self.screen_lock is None:
            return self.fail(message)
        if self.stopping.is_set() or self.phase in ("grace", "locked", "waiting"):
            return False
        self.pending_capture_error = message
        self.grace_until = time.monotonic() + self.LOCK_GRACE
        self.show_placeholder("grace")
        return False

    def on_message(self, bus, message):
        if (bus is self.placeholder_bus or
                (bus is self.bus and self.phase in ("grace", "locked", "waiting"))):
            # The local placeholder has no dependency on Mutter. Its errors
            # are real encoder failures, never an expected lock interruption.
            if message.type == self.Gst.MessageType.ERROR:
                error, _debug = message.parse_error()
                self.fail(f"Экран блокировки: {error.message}")
            elif message.type == self.Gst.MessageType.EOS:
                self.fail("Передача экрана блокировки завершилась")
            return
        super().on_message(bus, message)

    def display_state(self):
        return self.dbus.call_sync(
            "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None,
            None, self.Gio.DBusCallFlags.NONE, 3000, None).unpack()

    def find_connector(self):
        if self.cursor_workaround:
            return self.cursor_workaround.connector
        candidates = [monitor[0][0] for monitor in self.monitor_state()
                      if monitor[0][0] not in self.monitors_before
                      and monitor[0][0].startswith("Meta-")
                      and any(mode[1:3] == (self.width, self.height)
                              and mode[6].get("is-current") for mode in monitor[1])]
        return candidates[0] if len(candidates) == 1 else None

    def remember_layout(self):
        if self.connector is None:
            return
        try:
            self.saved_layout = capture_layout(self.display_state(), self.connector)
        except (self.GLib.Error, RuntimeError):
            # Never replace the last valid snapshot after Mutter has already
            # removed our monitor, before ActiveChanged reaches this process.
            pass

    def restore_layout(self):
        if not self.restore_layout_pending:
            return True
        if self.connector is None:
            return False
        self.restore_layout_pending = False
        try:
            serial, method, logicals, props = restore_parameters(
                self.display_state(), self.saved_layout, self.connector)
            for logical in logicals:
                for _connector, _mode, properties in logical[5]:
                    for key, value in properties.items():
                        properties[key] = self.GLib.Variant(
                            "b" if key == "underscanning" else "u", value)
            props = {key: self.GLib.Variant("u", value) for key, value in props.items()}
            self.dbus.call_sync(
                "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                "org.gnome.Mutter.DisplayConfig", "ApplyMonitorsConfig",
                self.GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})",
                                  (serial, method, logicals, props)),
                None, self.Gio.DBusCallFlags.NONE, 3000, None)
        except (self.GLib.Error, RuntimeError) as error:
            print(f"Экран подключён; прежнее расположение не восстановлено: {error}", flush=True)
            return False
        return True

    def resume_desktop(self):
        self.placeholder_pipeline, self.pipeline = self.pipeline, None
        self.placeholder_bus, self.bus = self.bus, None
        super().close_capture()
        self.phase = "resuming"
        self.capture_attempt_until = time.monotonic() + 5
        try:
            self.begin_desktop()
        except (self.GLib.Error, RuntimeError) as error:
            self.pending_capture_error = str(error)
            self.show_placeholder("waiting")
            self.retry_at = time.monotonic() + 1

    def tick(self):
        if self.stopping.is_set():
            return False
        if self.test_pattern:
            return super().tick()
        now = time.monotonic()
        try:
            if self.phase in ("locked", "grace", "waiting"):
                active = self.screen_lock.refresh()
                if active:
                    self.on_lock_changed(True)
                elif self.phase == "locked":
                    self.on_lock_changed(False)
                elif self.phase == "grace" and now >= self.grace_until:
                    if not self.resume_deadline:
                        return self.fail(self.pending_capture_error or "Захват экрана завершился")
                    self.phase = "waiting"
                    self.retry_at = now
            if self.resume_deadline and now >= self.resume_deadline:
                return self.fail("Не удалось восстановить экран после разблокировки: "
                                 + (self.pending_capture_error or "GNOME не передал видеокадры"))
            if self.phase == "waiting" and now >= self.retry_at:
                self.resume_desktop()
            if (self.phase in ("starting", "resuming")
                    and self.frames > self.desktop_first_frame and self.pipeline is not None
                    and (not self.cursor_workaround_enabled
                         or (self.cursor_workaround and self.cursor_workaround.frames))):
                self.connector = self.find_connector()
                layout_restored = self.restore_layout()
                if self.window_restorer is not None:
                    self.window_restorer.ready(self.connector, layout_restored=layout_restored)
                self.phase = "desktop"
                self.resume_deadline = 0
                self.pending_capture_error = None
                self.cursor_ready_reported = False
            if self.phase == "resuming" and now >= self.capture_attempt_until:
                self.pending_capture_error = "Захват ещё не передал первый кадр"
                self.show_placeholder("waiting")
                self.retry_at = now + 1
            if self.phase == "desktop":
                self.remember_layout()
        except (self.GLib.Error, RuntimeError) as error:
            return self.fail(str(error))
        return super().tick()

    def run(self):
        try:
            return super().run()
        finally:
            if self.window_restorer is not None:
                self.window_restorer.close()
            if self.screen_lock is not None:
                self.screen_lock.close()

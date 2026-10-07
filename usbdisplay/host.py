"""GNOME/Mutter screen capture and a bounded low-latency encoder."""

import select
import signal
import socket
import threading
import time

from .protocol import frame_header, hello
from .cursor import CursorWorkaround


def load_gi():
    try:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gio, GLib, Gst
    except (ImportError, ValueError) as error:
        raise RuntimeError("Нужны python3-gi и gir1.2-gstreamer-1.0; запускайте /usr/bin/python3") from error
    Gst.init(None)
    return Gio, GLib, Gst


def pipeline_description(width, height, fps, bitrate, node=None, quality=None, capture_fps=None,
                         lock_screen=False, animated_lock=True):
    if lock_screen:
        # Keep the existing Android connection alive with an opaque, local image.
        # Never repeat the last desktop frame while GNOME has revoked capture.
        source = "videotestsrc is-live=true pattern=black"
        if not animated_lock:
            source += (' ! textoverlay text="Panelyra\nComputer locked\nЭкран заблокирован" '
                       f'font-desc="Sans {max(8, width // 48)}" '
                       'auto-resize=false halignment=center valignment=center')
        source_caps = f"video/x-raw,format=BGRx,width={width},height={height},framerate={fps}/1"
    elif node is None:
        source = "videotestsrc is-live=true pattern=ball"
        source_caps = (f"video/x-raw,width={width},height={height},"
                       f"framerate={capture_fps or fps}/1")
    else:
        # path takes the PipeWire node ID emitted by Mutter. target-object
        # instead takes an object serial/name, which is a different identifier.
        # Keep the encoder queue independent of the compositor's small buffer
        # pool. The source copies pixels before returning the PipeWire buffer.
        source = (f"pipewiresrc path={int(node)} do-timestamp=true "
                  "use-bufferpool=false keepalive-time=1000")
        # Mutter offers variable-rate (0/1) frames, with a maximum rate.
        source_caps = (f"video/x-raw,width={width},height={height},framerate=0/1,"
                       f"max-framerate={capture_fps or fps}/1")
    if quality is None:
        rate_control = f"pass=cbr bitrate={bitrate} vbv-buf-capacity=100"
    else:
        if not 10 <= quality <= 35:
            raise ValueError("Качество QP должно быть от 10 до 35")
        # A 100 ms CBR buffer forces large quality changes on detailed desktops.
        # CQP has no VBV limit; equal I/P quantizers avoid periodic quality pulses.
        rate_control = f"pass=quant quantizer={quality} ip-factor=1.0 pb-factor=1.0"
    overlay = "! cairooverlay name=lock_scene " if lock_screen and animated_lock else ""
    return (
        f"{source} ! {source_caps} {overlay}"
        "! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream "
        f"! videorate name=rate drop-only=true ! video/x-raw,framerate={fps}/1 "
        "! videoconvert ! video/x-raw,format=I420 "
        f"! x264enc tune=zerolatency speed-preset=ultrafast {rate_control} "
        f"key-int-max={fps} byte-stream=true bframes=0 cabac=false dct8x8=false "
        "ref=1 threads=2 "
        "! video/x-h264,stream-format=byte-stream,alignment=au,profile=baseline "
        "! appsink name=encoded emit-signals=true sync=false max-buffers=2 drop=false"
    )


def prepare_lock_scene(width, height, Gst, language=None):
    """Optional decoration must never prevent the existing lock recovery."""
    try:
        import gi
        gi.require_foreign("cairo")
        from .lockscreen import LockScene
        if Gst.ElementFactory.find("cairooverlay") is None:
            raise RuntimeError("cairooverlay is unavailable")
        return LockScene(width, height, language)
    except Exception as error:
        # Cairo surface/context errors are not RuntimeError subclasses. A
        # failure anywhere in optional decoration must retain the static path.
        print(f"Анимация блокировки недоступна, используется простая заставка: {error}", flush=True)
        return None


class Stream:
    def __init__(self, connection, width, height, fps, bitrate, test_pattern=False, duration=0,
                 cursor_workaround=True, quality=None, stats_interval=0, capture_fps=None,
                 on_status=None, language=None):
        self.Gio, self.GLib, self.Gst = load_gi()
        self.connection = connection
        self.width, self.height, self.fps = width, height, fps
        # Capture can run faster than delivery: a 60 Hz desktop gives Mutter's
        # frame scheduling headroom while videorate keeps the decoder at 30 fps.
        self.capture_fps = fps if capture_fps is None else capture_fps
        if not fps <= self.capture_fps <= 60:
            raise ValueError("Частота захвата должна быть от FPS передачи до 60")
        self.bitrate, self.test_pattern, self.duration = bitrate, test_pattern, duration
        self.quality = quality
        self.loop = self.GLib.MainLoop()
        self.dbus = self.session = self.pipeline = self.bus = None
        self.keeper_pipeline = self.keeper_bus = None
        self.keeper_frames = 0
        self.subscriptions = []
        self.timers = []
        self.capture_timers = []
        self.capture_epoch = 0
        self.stopping = threading.Event()
        self.frames = self.bytes_sent = 0
        self.first_pts = None
        self.pts_offset_us = 0
        self.last_pts_us = -1
        self.error = None
        self.started = time.monotonic()
        self.stats_interval = stats_interval
        self.stats_last_time = self.started
        self.stats_frames = self.stats_bytes = 0
        self.stats_send_max_ms = 0.0
        self.stats_rate_previous = (0, 0, 0)
        self.stats_lock = threading.Lock()
        self.cursor_workaround_enabled = cursor_workaround
        self.cursor_workaround = None
        self.cursor_ready_reported = False
        self.on_status = on_status
        self.language = language
        self.monitors_before = set()
        self.monitor_wait_started = None

    def monitor_state(self):
        return self.dbus.call_sync(
            "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
            "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None,
            None, self.Gio.DBusCallFlags.NONE, 3000, None).unpack()[1]

    def setup_cursor(self):
        if self.stopping.is_set():
            return False
        try:
            # A first keeper frame proves our virtual output is configured.
            # Do not accidentally select another client's output appearing
            # while ours is still negotiating its PipeWire format.
            if not self.keeper_frames:
                if time.monotonic() - self.monitor_wait_started > 10:
                    raise RuntimeError("Не получен первый кадр виртуального экрана за 10 секунд")
                return True
            candidates = [monitor[0][0] for monitor in self.monitor_state()
                          if monitor[0][0] not in self.monitors_before
                          and monitor[0][0].startswith("Meta-")
                          and any(mode[1:3] == (self.width, self.height)
                                  and mode[6].get("is-current") for mode in monitor[1])]
            if len(candidates) > 1:
                raise RuntimeError("Не удалось однозначно определить новый виртуальный экран")
            if candidates:
                self.cursor_workaround = CursorWorkaround(
                    candidates[0], self.Gio, self.GLib, self.Gst, self.capture_failed,
                    on_node=self.start_pipeline)
                self.cursor_workaround.start()
                return False
            if time.monotonic() - self.monitor_wait_started > 10:
                raise RuntimeError("Виртуальный экран не появился в настройках GNOME за 10 секунд")
        except Exception as error:
            return self.capture_failed(f"Настройка курсора: {error}")
        return True

    def call(self, path, interface, method, parameters=None):
        return self.dbus.call_sync(
            "org.gnome.Mutter.ScreenCast", path, interface, method, parameters,
            None, self.Gio.DBusCallFlags.NONE, 5000, None)

    def create_monitor(self):
        self.dbus = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None)
        if self.cursor_workaround_enabled:
            self.monitors_before = {monitor[0][0] for monitor in self.monitor_state()}
        base = "org.gnome.Mutter.ScreenCast"
        self.session = self.call("/org/gnome/Mutter/ScreenCast", base, "CreateSession",
                                 self.GLib.Variant("(a{sv})", ({},))).unpack()[0]
        epoch = self.capture_epoch
        self.subscriptions.append(self.dbus.signal_subscribe(
            base, base + ".Session", "Closed", self.session, None,
            self.Gio.DBusSignalFlags.NONE,
            lambda *_: self.capture_failed("GNOME завершил сеанс виртуального экрана")
            if epoch == self.capture_epoch else None))
        stream = self.call(self.session, base + ".Session", "RecordVirtual",
                           self.GLib.Variant("(a{sv})", ({
                               "cursor-mode": self.GLib.Variant("u", 1),
                           },))).unpack()[0]
        self.subscriptions.append(self.dbus.signal_subscribe(
            base, base + ".Stream", "PipeWireStreamAdded", stream, None,
            self.Gio.DBusSignalFlags.NONE,
            lambda *args: self.on_node(*args) if epoch == self.capture_epoch else None))
        self.call(self.session, base + ".Session", "Start")
        if self.cursor_workaround_enabled:
            self.monitor_wait_started = time.monotonic()
            self.capture_timers.append(self.GLib.timeout_add(100, self.setup_cursor))

    def on_node(self, _connection, _sender, _path, _interface, _signal, parameters):
        try:
            if (not self.stopping.is_set() and self.pipeline is None
                    and self.keeper_pipeline is None):
                if self.cursor_workaround_enabled:
                    self.start_keeper(parameters.unpack()[0])
                else:
                    self.start_pipeline(parameters.unpack()[0])
        except Exception as error:
            self.capture_failed(str(error))

    def start_keeper(self, node):
        # RecordVirtual creates the monitor and fixes its refresh rate. Its
        # MemFd capture runs inside AFTER_PAINT, so discard these pixels and
        # encode RecordMonitor instead (its readback is deferred until idle).
        self.keeper_pipeline = self.Gst.parse_launch(
            f"pipewiresrc path={int(node)} use-bufferpool=false "
            f"! video/x-raw,width={self.width},height={self.height},"
            f"framerate=0/1,max-framerate={self.capture_fps}/1 "
            "! fakesink name=keeper sync=false async=false "
            "enable-last-sample=false signal-handoffs=true")
        self.keeper_pipeline.get_by_name("keeper").connect("handoff", self.on_keeper_frame)
        self.keeper_bus = self.keeper_pipeline.get_bus()
        self.keeper_bus.add_signal_watch()
        self.keeper_bus.connect("message", self.on_message)
        result = self.keeper_pipeline.set_state(self.Gst.State.PLAYING)
        if result == self.Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Не удалось активировать виртуальный монитор")

    def on_keeper_frame(self, _sink, _buffer, _pad):
        self.keeper_frames += 1

    def start_pipeline(self, node=None, *, lock_screen=False):
        scene = prepare_lock_scene(self.width, self.height, self.Gst, self.language) if lock_screen else None
        self.first_pts = None
        self.pts_offset_us = (0 if self.last_pts_us < 0 else
                              max(self.last_pts_us + 1,
                                  int((time.monotonic() - self.started) * 1_000_000)))
        self.pipeline = self.Gst.parse_launch(pipeline_description(
            self.width, self.height, self.fps, self.bitrate, node, self.quality,
            self.capture_fps, lock_screen=lock_screen, animated_lock=scene is not None))
        if scene is not None:
            self.pipeline.get_by_name("lock_scene").connect("draw", scene.on_draw)
        self.pipeline.get_by_name("encoded").connect("new-sample", self.on_sample)
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self.on_message)
        result = self.pipeline.set_state(self.Gst.State.PLAYING)
        if result == self.Gst.StateChangeReturn.FAILURE:
            message = self.bus.pop_filtered(self.Gst.MessageType.ERROR)
            if message is not None:
                error, debug = message.parse_error()
                raise RuntimeError(f"GStreamer: {error.message}\n{debug or ''}")
            raise RuntimeError("Не удалось запустить видеокодер")

    def on_sample(self, sink):
        if self.stopping.is_set():
            return self.Gst.FlowReturn.FLUSHING
        sample = sink.emit("pull-sample")
        if sample is None:
            return self.Gst.FlowReturn.EOS
        buffer = sample.get_buffer()
        success, mapped = buffer.map(self.Gst.MapFlags.READ)
        if not success:
            self.GLib.idle_add(self.fail, "Не удалось прочитать видеокадр")
            return self.Gst.FlowReturn.ERROR
        try:
            pts = buffer.pts
            if pts == self.Gst.CLOCK_TIME_NONE:
                pts_us = self.frames * 1_000_000 // self.fps
            else:
                if self.first_pts is None:
                    self.first_pts = pts
                pts_us = max(0, (pts - self.first_pts) // 1000)
            pts_us = max(self.last_pts_us + 1, self.pts_offset_us + pts_us)
            # Never drop encoded P-frames. Backpressure drops raw frames in
            # the queue BEFORE x264, preserving the H.264 reference chain.
            send_started = time.monotonic() if self.stats_interval > 0 else None
            self.connection.sendall(frame_header(len(mapped.data), pts_us))
            self.connection.sendall(mapped.data)
            self.last_pts_us = pts_us
            if send_started is not None:
                send_ms = (time.monotonic() - send_started) * 1000
                with self.stats_lock:
                    self.stats_frames += 1
                    self.stats_bytes += len(mapped.data)
                    self.stats_send_max_ms = max(self.stats_send_max_ms, send_ms)
            self.frames += 1
            self.bytes_sent += len(mapped.data)
            if (self.cursor_workaround is not None and self.pipeline is not None
                    and sink == self.pipeline.get_by_name("encoded")):
                self.cursor_workaround.frames += 1
        except (OSError, ValueError) as error:
            self.GLib.idle_add(self.fail, f"Передача остановлена: {error}")
            return self.Gst.FlowReturn.ERROR
        finally:
            buffer.unmap(mapped)
        return self.Gst.FlowReturn.OK

    def on_message(self, _bus, message):
        if _bus not in (self.bus, self.keeper_bus):
            return  # A queued message from an intentionally retired capture.
        if message.type == self.Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self.capture_failed(f"GStreamer: {error.message}\n{debug or ''}")
        elif message.type == self.Gst.MessageType.EOS:
            self.capture_failed("Видеопоток завершён")

    def capture_failed(self, message):
        return self.fail(message)

    def close_capture(self):
        """Release capture resources without closing the tablet connection."""
        self.capture_epoch += 1
        for timer in self.capture_timers:
            if self.GLib.MainContext.default().find_source_by_id(timer):
                self.GLib.source_remove(timer)
        self.capture_timers.clear()
        for subscription in self.subscriptions:
            self.dbus.signal_unsubscribe(subscription)
        self.subscriptions.clear()
        pipeline, keeper = self.pipeline, self.keeper_pipeline
        bus, keeper_bus = self.bus, self.keeper_bus
        self.pipeline = self.keeper_pipeline = self.bus = self.keeper_bus = None
        if pipeline:
            pipeline.set_state(self.Gst.State.NULL)
        # Release the monitor-scoped cursor inhibitor before its monitor.
        if self.cursor_workaround:
            self.cursor_workaround.close()
            self.cursor_workaround = None
        if keeper:
            keeper.set_state(self.Gst.State.NULL)
        for watched_bus in (bus, keeper_bus):
            if watched_bus:
                watched_bus.remove_signal_watch()
        if self.session:
            try:
                self.call(self.session, "org.gnome.Mutter.ScreenCast.Session", "Stop")
            except self.GLib.Error:
                pass  # GNOME may already have closed this session.
            self.session = None
        self.keeper_frames = 0

    def fail(self, message):
        if not self.stopping.is_set():
            self.error = message
            self.stop()
        return False

    def stop(self, *_):
        self.stopping.set()
        self.loop.quit()
        return False

    def report_stats(self, now):
        interval = now - self.stats_last_time
        if self.stats_interval <= 0 or interval < self.stats_interval:
            return
        with self.stats_lock:
            frames, byte_count = self.stats_frames, self.stats_bytes
            send_max_ms = self.stats_send_max_ms
            self.stats_frames = self.stats_bytes = 0
            self.stats_send_max_ms = 0.0
        rate = self.pipeline.get_by_name("rate") if self.pipeline is not None else None
        rate_text = "videorate ещё не активен"
        if rate is not None:
            current = tuple(rate.get_property(name) for name in ("in", "out", "drop"))
            changes = tuple(value - previous for value, previous
                            in zip(current, self.stats_rate_previous))
            self.stats_rate_previous = current
            rate_text = f"videorate in={changes[0]} out={changes[1]} drop={changes[2]}"
        self.stats_last_time = now
        print(f"Статистика за {interval:.1f} с: {frames / interval:.1f} fps; "
              f"{byte_count * 8 / interval / 1_000_000:.2f} Мбит/с; "
              f"{rate_text}; send max={send_max_ms:.1f} мс", flush=True)

    def tick(self):
        if self.stopping.is_set():
            return False
        if (self.cursor_workaround and self.cursor_workaround.frames
                and not self.cursor_ready_reported):
            print(f"Изображение и курсор: захват экрана {self.cursor_workaround.connector} активен.", flush=True)
            self.cursor_ready_reported = True
            if self.on_status is not None:
                self.on_status("ready")
        elif (self.test_pattern or not self.cursor_workaround_enabled) and self.frames and not self.cursor_ready_reported:
            self.cursor_ready_reported = True
            if self.on_status is not None:
                self.on_status("ready")
        now = time.monotonic()
        elapsed = now - self.started
        if not self.frames and elapsed > 15:
            return self.fail("Нет кадров за 15 секунд. Проверьте GNOME Wayland и плагины GStreamer")
        if self.duration and elapsed >= self.duration:
            if not self.frames:
                return self.fail("Проверка завершилась без видеокадров")
            self.stop()
            return False
        if self.stats_interval > 0:
            self.report_stats(now)
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if readable and not self.connection.recv(1, socket.MSG_PEEK):
                return self.fail("Планшет отключился")
        except OSError as error:
            return self.fail(str(error))
        return True

    def run(self):
        previous = {}
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, self.stop)
            self.connection.sendall(hello(self.width, self.height, self.fps))
            if self.test_pattern:
                self.start_pipeline()
            else:
                self.create_monitor()
            self.timers.append(self.GLib.timeout_add_seconds(1, self.tick))
            encoding = (f"постоянное качество QP {self.quality}" if self.quality is not None
                        else f"{self.bitrate} кбит/с")
            print(f"Panelyra: {self.width}×{self.height}, {self.fps} fps, {encoding}. Ctrl+C — остановить.", flush=True)
            if not self.test_pattern and self.capture_fps != self.fps:
                print(f"Виртуальный монитор и захват: {self.capture_fps} Гц; "
                      f"передача на планшет: до {self.fps} кадров/с.", flush=True)
            if not self.stopping.is_set():
                self.loop.run()
        except self.GLib.Error as error:
            if "Session creation inhibited" in error.message:
                raise RuntimeError("GNOME запретил захват экрана. Разблокируйте Ubuntu "
                                   "и повторите запуск передачи") from error
            raise RuntimeError(f"Ошибка GNOME/GStreamer: {error.message}") from error
        finally:
            self.stopping.set()
            # Unblock a sender before joining GStreamer's streaming thread.
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.close_capture()
            for timer in self.timers:
                if self.GLib.MainContext.default().find_source_by_id(timer):
                    self.GLib.source_remove(timer)
            for signum, handler in previous.items():
                signal.signal(signum, handler)
            print(f"Передано кадров: {self.frames}; данных: {self.bytes_sent / 1_000_000:.2f} МБ", flush=True)
        if self.error:
            raise RuntimeError(self.error)

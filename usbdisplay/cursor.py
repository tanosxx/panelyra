"""Keep the cursor visible on virtual outputs affected by Mutter's overlay bug.

Mutter's RecordMonitor embedded-cursor capture inhibits hardware cursors only
while the pointer is inside that monitor. RecordVirtual does not install that
inhibitor. A monitor capture therefore avoids the mixed physical/virtual cursor
bug fixed upstream in Mutter 50.5, without changing desktop settings. Its node
can feed the caller's encoder or a small local consumer that drops the pixels.
"""


class CursorWorkaround:
    """Own one RecordMonitor session and optionally its PipeWire consumer.

    ``connector`` must already exist in Mutter DisplayConfig. ``start()`` calls
    ``create()`` when necessary. Synchronous failures raise RuntimeError; later
    failures close the owned resources and call ``on_error(message)`` on the
    GLib main context. ``close()`` is safe to call more than once.

    If provided, ``on_node(node_id)`` is called once instead of starting the
    internal consumer. The caller owns that pipeline, increments ``frames`` on
    received frames for the readiness check, and stops it before ``close()``.
    """

    BASE = "org.gnome.Mutter.ScreenCast"

    def __init__(self, connector, Gio, GLib, Gst, on_error, on_node=None):
        if not connector:
            raise ValueError("Не указан виртуальный монитор для курсора")
        self.connector = connector
        self.Gio, self.GLib, self.Gst = Gio, GLib, Gst
        self.on_error = on_error
        self.on_node = on_node
        self.node_received = False
        self.dbus = self.session = self.stream = None
        self.pipeline = self.bus = None
        self.subscriptions = []
        self.bus_handler = None
        self.readiness_timer = None
        self.frames = 0
        self.started = False
        self.closed = False

    def _call(self, path, interface, method, parameters=None):
        return self.dbus.call_sync(
            self.BASE, path, interface, method, parameters,
            None, self.Gio.DBusCallFlags.NONE, 5000, None)

    def _subscribe(self, interface, signal, path, callback):
        self.subscriptions.append(self.dbus.signal_subscribe(
            self.BASE, interface, signal, path, None,
            self.Gio.DBusSignalFlags.NONE, callback))

    def create(self):
        if self.closed:
            raise RuntimeError("Сеанс обновления курсора уже закрыт")
        if self.session is not None:
            return
        try:
            self.dbus = self.Gio.bus_get_sync(self.Gio.BusType.SESSION, None)
            self.session = self._call(
                "/org/gnome/Mutter/ScreenCast", self.BASE, "CreateSession",
                self.GLib.Variant("(a{sv})", ({},))).unpack()[0]
            self._subscribe(self.BASE + ".Session", "Closed", self.session,
                            self._on_closed)
            self.stream = self._call(
                self.session, self.BASE + ".Session", "RecordMonitor",
                self.GLib.Variant("(sa{sv})", (
                    self.connector, {"cursor-mode": self.GLib.Variant("u", 1)},
                ))).unpack()[0]
            self._subscribe(self.BASE + ".Stream", "PipeWireStreamAdded",
                            self.stream, self._on_node)
        except Exception as error:
            self.close()
            raise RuntimeError(f"Не удалось подготовить обновление курсора: {error}") from error

    def start(self):
        self.create()
        if self.started:
            return
        try:
            self._call(self.session, self.BASE + ".Session", "Start")
            self.started = True
            self.readiness_timer = self.GLib.timeout_add_seconds(10, self._check_ready)
        except Exception as error:
            self.close()
            raise RuntimeError(f"Не удалось запустить обновление курсора: {error}") from error

    def _on_node(self, _connection, _sender, _path, _interface, _signal, parameters):
        if self.closed or self.node_received:
            return
        try:
            node = int(parameters.unpack()[0])
            self.node_received = True
            if self.on_node is not None:
                self.on_node(node)
                return
            # A linked, streaming consumer enables the monitor's scoped cursor
            # inhibitor. The pixels stay on this PC and are immediately dropped.
            # Variable-rate caps match Mutter; 1 fps limits the extra readback.
            self.pipeline = self.Gst.parse_launch(
                f"pipewiresrc path={node} do-timestamp=true use-bufferpool=false "
                "! video/x-raw,framerate=0/1,max-framerate=1/1 "
                "! fakesink name=cursor_frames sync=false async=false "
                "enable-last-sample=false signal-handoffs=true")
            self.pipeline.get_by_name("cursor_frames").connect("handoff", self._on_handoff)
            self.bus = self.pipeline.get_bus()
            self.bus.add_signal_watch()
            self.bus_handler = self.bus.connect("message", self._on_message)
            result = self.pipeline.set_state(self.Gst.State.PLAYING)
            if result == self.Gst.StateChangeReturn.FAILURE:
                message = self.bus.pop_filtered(self.Gst.MessageType.ERROR)
                if message is not None:
                    error, _debug = message.parse_error()
                    raise RuntimeError(error.message)
                raise RuntimeError("GStreamer не запустил приёмник курсора")
        except Exception as error:
            self._fail(f"Не удалось включить обновление курсора: {error}")

    def _on_handoff(self, _sink, _buffer, _pad):
        if not self.closed:
            self.frames += 1

    def _check_ready(self):
        self.readiness_timer = None
        if not self.closed and not self.frames:
            self._fail("Вспомогательный поток курсора не передал кадр за 10 секунд")
        return False

    def _on_closed(self, *_args):
        self._fail("GNOME завершил вспомогательный сеанс обновления курсора")

    def _on_message(self, _bus, message):
        if self.closed:
            return
        if message.type == self.Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            self._fail(f"GStreamer: обновление курсора остановлено: {error.message}")
        elif message.type == self.Gst.MessageType.EOS:
            self._fail("Вспомогательный поток обновления курсора завершён")

    def _fail(self, message):
        if self.closed:
            return
        self.close()
        self.on_error(message)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.readiness_timer is not None:
            self.GLib.source_remove(self.readiness_timer)
            self.readiness_timer = None
        if self.dbus is not None:
            for subscription in self.subscriptions:
                self.dbus.signal_unsubscribe(subscription)
        self.subscriptions.clear()
        if self.bus is not None:
            if self.bus_handler is not None:
                self.bus.disconnect(self.bus_handler)
                self.bus_handler = None
            self.bus.remove_signal_watch()
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)
        self.pipeline = self.bus = None
        if self.session is not None:
            try:
                self._call(self.session, self.BASE + ".Session", "Stop")
            except self.GLib.Error:
                pass  # Mutter may already have closed this session.
        self.session = self.stream = None

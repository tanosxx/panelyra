"""Optional, narrowly scoped GNOME Shell companion for window restoration.

The extension snapshots live windows inside Shell before capture is revoked.
Its absence or failure must never interrupt the USB video connection.
"""


BUS = "org.gnome.Shell"
PATH = "/io/github/tanosx/Panelyra/Windows"
INTERFACE = "io.github.tanosx.Panelyra.Windows1"


class WindowRestorer:
    def __init__(self, Gio, GLib, connection):
        self.Gio, self.GLib, self.connection = Gio, GLib, connection
        self.registered = False
        self.discard_pending = False
        self.version_checked = False
        self.available = False
        self.warned = False

    def _call(self, method, signature=None, values=(), *, timeout=1500,
              interface=INTERFACE):
        parameters = self.GLib.Variant(signature, values) if signature else None
        return self.connection.call_sync(
            BUS, PATH, interface, method, parameters, None,
            self.Gio.DBusCallFlags.NONE, timeout, None).unpack()

    def _warning(self, message):
        if not self.warned:
            print(f"Возврат окон: {message}", flush=True)
            self.warned = True

    def ready(self, connector, *, layout_restored=True):
        """Register after the first frame, then restore an earlier lock snapshot."""
        if not connector or not connector.startswith("Meta-"):
            return
        try:
            if not self.version_checked:
                self.version_checked = True
                version, = self._call(
                    "Get", "(ss)", (INTERFACE, "Version"),
                    interface="org.freedesktop.DBus.Properties")
                # GLib.Variant.unpack normally unwraps nested variants too.
                if hasattr(version, "unpack"):
                    version = version.unpack()
                self.available = version == 1
                if not self.available:
                    self._warning("неподдерживаемая версия расширения GNOME.")
            if not self.available:
                return
            had_registration = self.registered
            if had_registration and not layout_restored:
                self.discard_pending = True
            if self.discard_pending:
                # Keep invalidation pending even if D-Bus times out. A later
                # reconnect must never restore the old topology's snapshot.
                self._call("Unregister", timeout=1000)
                self.registered = False
                self.discard_pending = False
                had_registration = False
                self._warning("расположение экранов изменилось; сохранённые окна не перемещены.")
            accepted, = self._call("Register", "(s)", (connector,))
            if not accepted:
                self._warning("расширение не приняло виртуальный экран.")
                return
            self.registered = True
            if had_registration:
                count, = self._call("Restore", "(s)", (connector,), timeout=4000)
                if count:
                    print(f"Окна возвращены на второй экран: {count}.", flush=True)
        except (self.GLib.Error, RuntimeError, TypeError, ValueError) as error:
            self._warning(f"расширение GNOME недоступно ({error}). "
                          "Автоматическое переподключение экрана продолжает работать.")

    def close(self):
        if self.registered:
            try:
                self._call("Unregister", timeout=1000)
            except (self.GLib.Error, RuntimeError, TypeError, ValueError):
                pass
            self.registered = False

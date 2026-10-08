"""Temporarily allow or suppress the USB tether's Internet route and DNS.

Only NetworkManager's *applied* connection is changed. Reapply keeps the
interface up; the saved profile and the tablet's directly connected subnet are
left alone. Call the synchronous controller from a worker, not GTK's thread.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from . import network

NM = "org.freedesktop.NetworkManager"
ROOT = "/org/freedesktop/NetworkManager"
DEVICE = NM + ".Device"
ACTIVE = NM + ".Connection.Active"
PROPERTIES = "org.freedesktop.DBus.Properties"
FAMILIES = ("ipv4", "ipv6")
FLAGS = ("never-default", "ignore-auto-dns")
DNS = ("dns", "dns-data", "dns-search")
KEYS = FLAGS + DNS


class InternetError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class InternetState:
    link: network.UsbLink
    enabled: bool
    device_path: str
    active_path: str
    profile_path: str
    connection_uuid: str
    version_id: int
    nm_owner: str
    bus_id: str

    @property
    def interface(self):
        return self.link.interface

    @property
    def identity(self):
        # Object paths alone can be reused after a NetworkManager/bus restart.
        return (self.bus_id, self.nm_owner, self.device_path, self.active_path,
                self.profile_path, self.connection_uuid, self.interface)


def _dictionary(variant):
    """Unbox one dictionary level without losing each property's Variant type."""
    result = {}
    for index in range(variant.n_children()):
        entry = variant.get_child_value(index)
        value = entry.get_child_value(1)
        if value.get_type_string() == "v":
            value = value.get_variant()
        result[entry.get_child_value(0).unpack()] = value
    return result


def _settings(variant):
    return {name: _dictionary(value) for name, value in _dictionary(variant).items()}


def _value(values, key, default=None):
    return values[key].unpack() if key in values else default


def _local_only(settings):
    return all(all(_value(settings[family], key, False) for key in FLAGS)
               and not any(_value(settings[family], key, []) for key in DNS)
               for family in FAMILIES)


def _fingerprint(settings):
    # NM may normalize explicit default values away on Reapply. Treat missing
    # and empty DNS lists, and missing and false flags, as equivalent.
    return {family: {
        key: (_value(settings[family], key, False) if key in FLAGS else
              (settings[family][key].print_(True)
               if _value(settings[family], key, []) else None))
        for key in KEYS} for family in FAMILIES}


def _capture(settings):
    return {family: {key: [value.get_type_string(), value.print_(True)]
                     for key, value in settings[family].items() if key in KEYS}
            for family in FAMILIES}


class InternetController:
    def __init__(self, *, connection=None, gio=None, glib=None, runtime_dir=None,
                 discover=None):
        self._connection = connection
        self._gio = gio
        self._glib = glib
        self._runtime_dir = runtime_dir
        self._discover = discover or network.discover_links

    def _connect(self):
        try:
            if self._gio is None or self._glib is None:
                from gi.repository import Gio, GLib
                self._gio, self._glib = Gio, GLib
            if self._connection is None:
                self._connection = self._gio.bus_get_sync(self._gio.BusType.SYSTEM, None)
        except (ImportError, OSError, RuntimeError) as error:
            raise InternetError("network-manager", str(error)) from error
        except Exception as error:
            if self._glib is not None and isinstance(error, self._glib.Error):
                raise InternetError("network-manager", str(error)) from error
            raise

    def _call(self, path, interface, method, signature=None, values=(), *,
              destination=NM, mutation=False):
        self._connect()
        args = self._glib.Variant(signature, values) if signature else None
        try:
            return self._connection.call_sync(
                destination, path, interface, method, args, None,
                self._gio.DBusCallFlags.NONE, 12000 if mutation else 4000, None)
        except self._glib.Error as error:
            remote = self._gio.dbus_error_get_remote_error(error) or ""
            detail = str(error)
            if any(word in remote.lower() for word in ("permission", "notauthorized", "accessdenied")):
                code = "permission"
            elif "version" in detail.lower() or "notactive" in remote.lower():
                code = "changed"
            elif mutation:
                code = "unsupported"
            else:
                code = "network-manager"
            raise InternetError(code, detail) from error

    def _properties(self, path, interface, destination=NM):
        return self._call(path, PROPERTIES, "GetAll", "(s)", (interface,),
                          destination=destination).unpack()[0]

    def _owner(self):
        return self._call("/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "GetNameOwner", "(s)", (NM,),
                          destination="org.freedesktop.DBus").unpack()[0]

    def _read(self, interface=None):
        try:
            links = self._discover(interface=interface)
        except RuntimeError as error:
            raise InternetError("no-usb", str(error)) from error
        if not links:
            raise InternetError("no-usb", "USB-модем не подключён")
        if len(links) != 1:
            raise InternetError("ambiguous", "Подключено несколько USB-модемов")
        link = links[0]
        owner = self._owner()
        bus_id = self._call("/org/freedesktop/DBus", "org.freedesktop.DBus", "GetId",
                            destination="org.freedesktop.DBus").unpack()[0]
        device = self._call(ROOT, NM, "GetDeviceByIpIface", "(s)",
                            (link.interface,), destination=owner).unpack()[0]
        props = self._properties(device, DEVICE, owner)
        if (props.get("State") != 100 or not props.get("Managed")
                or props.get("Interface") != link.interface
                or props.get("Driver") not in network.USB_DRIVERS):
            raise InternetError("no-usb", "USB-подключение ещё не готово в NetworkManager")
        active = props.get("ActiveConnection", "/")
        if active == "/":
            raise InternetError("changed", "USB-подключение изменилось")
        active_props = self._properties(active, ACTIVE, owner)
        applied = self._call(device, DEVICE, "GetAppliedConnection", "(u)", (0,),
                             destination=owner)
        settings = _settings(applied.get_child_value(0))
        version = applied.get_child_value(1).unpack()
        profile = active_props.get("Connection", "/")
        uuid = _value(settings.get("connection", {}), "uuid", "")
        if (not uuid or uuid != active_props.get("Uuid") or profile == "/"
                or version == 0 or active_props.get("State") != 2
                or self._properties(device, DEVICE, owner).get("ActiveConnection") != active
                or self._owner() != owner):
            raise InternetError("changed", "USB-подключение изменилось; повторите попытку")
        for family in FAMILIES:
            if family not in settings or _value(settings[family], "method") not in (
                    "auto", "manual", "disabled", "ignore", "link-local"):
                raise InternetError("unsupported", "Неподдерживаемые настройки USB-подключения")
        return InternetState(link, not _local_only(settings), device, active, profile,
                             uuid, version, owner, bus_id), settings

    def inspect(self, interface=None):
        """Read only. enabled means Internet is allowed, not that this route wins."""
        return self._read(interface)[0]

    @contextmanager
    def _storage(self):
        base = Path(self._runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
                    or f"/run/user/{os.getuid()}")
        folder = base / "panelyra-internet"
        try:
            info = base.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError("Небезопасный каталог пользовательского сеанса")
            folder.mkdir(mode=0o700, exist_ok=True)
            info = folder.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise OSError("Небезопасный каталог состояния Panelyra")
            descriptor = os.open(folder / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "r+") as lock:
                info = os.fstat(lock.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise OSError("Небезопасный файл блокировки Panelyra")
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield folder / "restore.json"
        except OSError as error:
            raise InternetError("storage", str(error)) from error

    @staticmethod
    def _load(path):
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor) as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise InternetError("storage", "Небезопасный файл восстановления сети")
            if info.st_size > 131072:
                raise InternetError("storage", "Файл восстановления сети слишком велик")
            try:
                saved = json.load(source)
                if not isinstance(saved, dict) or saved.get("schema") != 1:
                    raise ValueError("Неизвестный формат состояния сети")
                return saved
            except (ValueError, TypeError) as error:
                raise InternetError("storage", str(error)) from error

    @staticmethod
    def _save(path, value):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
                temporary = Path(output.name)
                json.dump(value, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _restore(self, settings, saved):
        try:
            for family in FAMILIES:
                original = saved[family]
                if not isinstance(original, dict) or set(original) - set(KEYS):
                    raise ValueError("Недопустимые параметры восстановления сети")
                for key in KEYS:
                    settings[family].pop(key, None)
                for key, (signature, text) in original.items():
                    value = self._glib.Variant.parse(self._glib.VariantType.new(signature), text, None, None)
                    allowed = ("b",) if key in FLAGS else ("au", "aay", "as", "aa{sv}")
                    if signature not in allowed:
                        raise ValueError("Недопустимый тип параметра сети")
                    settings[family][key] = value
        except (KeyError, TypeError, ValueError, self._glib.Error) as error:
            raise InternetError("storage", str(error)) from error

    @staticmethod
    def _supported_routes(settings):
        # An explicit /0 route or policy routing could bypass never-default.
        # Do not silently claim that Internet is off for such custom profiles.
        for family in FAMILIES:
            values = settings.get(family, {})
            if _value(values, "gateway", ""):
                raise InternetError("unsupported", "USB-подключение использует статический шлюз")
            if _value(values, "routing-rules", []):
                raise InternetError("unsupported", "USB-подключение использует особые правила маршрутизации")
            routes = _value(values, "route-data", [])
            legacy = _value(values, "routes", [])
            if any(route.get("prefix") == 0 for route in routes) or any(
                    len(route) > 1 and route[1] == 0 for route in legacy):
                raise InternetError("unsupported", "USB-подключение содержит явный маршрут по умолчанию")

    def set_enabled(self, state, enabled):
        """Change one previously inspected active USB connection, using NM's CAS.

        A small private runtime snapshot restores exactly the touched settings
        even after restarting the GUI. Reconnecting USB uses its saved profile.
        """
        with self._storage() as path:
            current, settings = self._read(state.interface)
            if current != state:
                raise InternetError("changed", "USB-подключение изменилось; обновите состояние")
            if current.enabled == bool(enabled):
                return current
            self._supported_routes(settings)
            saved = self._load(path)
            ours = saved and saved.get("identity") == list(state.identity)
            original = _capture(settings)
            if enabled:
                if ours:
                    if saved.get("expected") != _fingerprint(settings):
                        raise InternetError("restore-conflict", "Параметры USB были изменены другой программой")
                    self._restore(settings, saved.get("before", {}))
                else:
                    # Also handles older `configure-usb` / nmcli live changes.
                    reply = self._call(state.profile_path, NM + ".Settings.Connection", "GetSettings",
                                       destination=state.nm_owner)
                    profile = _settings(reply.get_child_value(0))
                    self._supported_routes(profile)
                    if _value(profile.get("connection", {}), "uuid") != state.connection_uuid:
                        raise InternetError("changed", "Профиль USB-подключения изменился")
                    self._restore(settings, _capture({f: profile.get(f, {}) for f in FAMILIES}))
                    # An explicit Enable must work even when the saved profile
                    # itself forbids the default route. This is still temporary.
                    if all(_value(settings[family], "never-default", False)
                           for family in FAMILIES):
                        for family in FAMILIES:
                            for key in FLAGS:
                                settings[family][key] = self._glib.Variant("b", False)
            else:
                for family in FAMILIES:
                    for key in FLAGS:
                        settings[family][key] = self._glib.Variant("b", True)
                    for key in DNS:
                        settings[family].pop(key, None)
                self._save(path, {"schema": 1, "identity": list(state.identity),
                                  "before": original, "expected": _fingerprint(settings)})
            # Check again after reading a saved profile / writing recovery data.
            # CAS closes the remaining race with another applied-settings edit.
            if self._read(state.interface)[0] != state:
                raise InternetError("changed", "USB-подключение изменилось; повторите попытку")
            self._call(state.device_path, DEVICE, "Reapply", "(a{sa{sv}}tu)",
                       (settings, state.version_id, 1), destination=state.nm_owner, mutation=True)
            result, applied = self._read(state.interface)
            if result.identity != state.identity or _fingerprint(applied) != _fingerprint(settings):
                raise InternetError("changed", "USB-подключение изменилось во время переключения")
            if enabled and ours:
                path.unlink(missing_ok=True)
            return result

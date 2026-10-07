#!/usr/bin/python3
"""Install Panelyra's window restoration extension for the current GNOME user.

Use --check to validate sources and local dependencies without installing.
GNOME discovers a new local extension after the next logout/login; this script
never restarts Shell, logs out, changes security settings or enables other
extensions. Previous versions are saved under this checkout's backups folder,
or the user's Panelyra data directory for a read-only system installation.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


UUID = "panelyra-windows@tanosx.github.io"
REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "desktop" / "gnome-extension" / UUID
BUS_NAME = "org.gnome.Shell"
INTERFACE = "org.gnome.Shell.Extensions"
OBJECT_PATH = "/org/gnome/Shell"


@dataclass(frozen=True)
class Installation:
    destination: Path
    changed: bool
    replaced: bool = False
    backup: Path | None = None


def safe_path(path):
    """Reject symlinks in every existing component, including dangling links."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Требуется абсолютный путь без '..': {path}")
    for entry in reversed((path, *path.parents)):
        if entry.is_symlink():
            raise ValueError(f"Символическая ссылка недопустима: {entry}")
    return path


def inventory(folder):
    """Read only regular files; never follow a link in an extension tree."""
    folder = safe_path(folder)
    if not folder.is_dir():
        raise ValueError(f"Нет папки расширения: {folder}")
    result = {}
    for path in sorted(folder.rglob("*")):
        safe_path(path)
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"Допустимы только обычные файлы: {path}")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError(f"Файл изменился во время проверки: {path}")
            result[path.relative_to(folder)] = stream.read()
    return result


def validate_source(source=SOURCE):
    files = inventory(source)
    try:
        metadata = json.loads(files[Path("metadata.json")])
    except (KeyError, ValueError) as error:
        raise ValueError("Нет корректного metadata.json расширения") from error
    if (not isinstance(metadata, dict) or metadata.get("uuid") != UUID
            or not isinstance(metadata.get("shell-version"), list)
            or "50" not in metadata["shell-version"]
            or not isinstance(metadata.get("session-modes"), list)
            or not {"user", "unlock-dialog"}.issubset(metadata["session-modes"])):
        raise ValueError("metadata.json не соответствует расширению Panelyra для GNOME 50")
    if not files.get(Path("extension.js")):
        raise ValueError("Нет extension.js расширения")
    return files


def check_dependencies():
    for command in ("gnome-shell", "gnome-extensions", "gjs"):
        if shutil.which(command) is None:
            raise RuntimeError(f"Не найдена программа: {command}")
    version = subprocess.check_output(["gnome-shell", "--version"], text=True).strip()
    if not re.search(r"\bGNOME Shell 50(?:\.|\s|$)", version):
        raise RuntimeError(f"Расширение проверено для GNOME 50; установлен {version}")
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError) as error:
        raise RuntimeError("Нужны системный Python и python3-gi (Gio, GLib)") from error
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.gnome.shell", True) is None:
        raise RuntimeError("Не найдена схема настроек org.gnome.shell")
    return Gio, GLib


def default_backup_root(data_home, repo=REPO):
    """Keep checkout backups together; packaged sources are normally read-only."""
    repo = safe_path(repo)
    checkout_backups = safe_path(repo / "backups")
    existing = checkout_backups if checkout_backups.exists() else repo
    if existing.stat().st_uid == os.getuid() and os.access(existing, os.W_OK):
        return checkout_backups
    return safe_path(data_home) / "panelyra" / "backups"


def install_files(source, destination, backup_root):
    """Stage and replace only our own UUID directory; preserve the old tree."""
    files = validate_source(source)
    destination, backup_root = safe_path(destination), safe_path(backup_root)
    if destination.name != UUID:
        raise ValueError("Папка назначения должна совпадать с UUID Panelyra")
    previous = None
    if destination.exists():
        if destination.stat().st_uid != os.getuid():
            raise ValueError("Расширение должно принадлежать текущему пользователю")
        previous = inventory(destination)
        try:
            metadata = json.loads(previous[Path("metadata.json")])
        except (KeyError, ValueError) as error:
            raise ValueError("Существующая папка не содержит корректное расширение Panelyra") from error
        if not isinstance(metadata, dict) or metadata.get("uuid") != UUID:
            raise ValueError("Существующая папка принадлежит другому расширению")
        if previous == files:
            return Installation(destination, False)

    destination.parent.mkdir(parents=True, exist_ok=True)
    safe_path(destination.parent)
    if destination.parent.stat().st_uid != os.getuid():
        raise ValueError("Папка расширений должна принадлежать текущему пользователю")
    backup = None
    if previous is not None:
        backup_root.mkdir(parents=True, exist_ok=True)
        safe_path(backup_root)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f%z")
        backup = backup_root / f"gnome-extension-{stamp}" / UUID
        backup.parent.mkdir(mode=0o700)
        shutil.copytree(destination, backup, copy_function=shutil.copy2)
        if inventory(backup) != previous or inventory(destination) != previous:
            raise RuntimeError("Расширение изменилось во время резервного копирования; установка отменена")

    with tempfile.TemporaryDirectory(prefix=".panelyra-extension-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        staged = temporary / "new"
        staged.mkdir(mode=0o755)
        for relative, content in files.items():
            output = staged / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
            output.chmod(0o644)
        displaced = temporary / "previous"
        safe_path(destination)
        if previous is None:
            if destination.exists():
                raise RuntimeError("Папка назначения появилась во время установки; установка отменена")
        else:
            if inventory(destination) != previous:
                raise RuntimeError("Расширение изменилось во время установки; установка отменена")
            os.replace(destination, displaced)
        try:
            os.replace(staged, destination)
        except OSError:
            if displaced.exists():
                os.replace(displaced, destination)
            raise
    return Installation(destination, True, previous is not None, backup)


def enable_extension(Gio, GLib, *, settings=None, connection=None):
    """Use the public API, or queue discovery at the next login for a new UUID."""
    if settings is None:
        settings = Gio.Settings.new("org.gnome.shell")
    if settings.get_boolean("disable-user-extensions"):
        return "disabled", "Пользовательские расширения отключены в GNOME; общая настройка не изменена."
    if not settings.get_boolean("allow-extension-installation"):
        return "disabled", "GNOME запрещает установку пользовательских расширений."
    if connection is None:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(method):
        return connection.call_sync(
            BUS_NAME, OBJECT_PATH, INTERFACE, method,
            GLib.Variant("(s)", (UUID,)), None,
            Gio.DBusCallFlags.NONE, 3000, None).unpack()

    info = call("GetExtensionInfo")[0]
    enabled = call("EnableExtension")[0]
    if info:
        if not enabled:
            return "failed", "GNOME не принял запрос включения расширения."
        return "requested", "Запрос на включение расширения передан GNOME."
    # EnableExtension cannot discover a directory added after Shell started.
    # Change only our UUID, preserving all unrelated extension preferences.
    current = settings.get_strv("enabled-extensions")
    disabled = settings.get_strv("disabled-extensions")
    changes = {}
    if UUID not in current:
        changes["enabled-extensions"] = [*current, UUID]
    if UUID in disabled:
        changes["disabled-extensions"] = [item for item in disabled if item != UUID]
    for key in changes:
        if not settings.is_writable(key):
            return "failed", f"GNOME не разрешает изменить {key}; включите расширение после входа."
    for key, value in changes.items():
        if not settings.set_strv(key, value):
            return "failed", f"GNOME не сохранил настройку {key}."
    Gio.Settings.sync()
    return "pending", "Расширение включится после следующего выхода из сеанса и повторного входа."


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="проверить без установки и изменения настроек")
    args = parser.parse_args(argv)
    validate_source()
    Gio, GLib = check_dependencies()
    if args.check:
        print("Расширение Panelyra и зависимости GNOME 50 проверены; ничего не установлено.")
        return 0
    if os.getuid() == 0:
        raise RuntimeError("Запустите установку обычным пользователем, без sudo")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    destination = safe_path(data_home) / "gnome-shell" / "extensions" / UUID
    result = install_files(SOURCE, destination, default_backup_root(data_home))
    print(f"Расширение Panelyra: {result.destination}", flush=True)
    if result.backup:
        print(f"Резервная копия прежнего расширения: {result.backup}", flush=True)
    try:
        state, message = enable_extension(Gio, GLib)
    except GLib.Error as error:
        print(f"Файлы установлены, но GNOME пока недоступен: {error}", file=sys.stderr)
        print(f"После входа выполните: gnome-extensions enable {UUID}")
        return 1
    print(message)
    if result.replaced and state == "requested":
        print("Для загрузки изменённого кода выйдите из сеанса и войдите снова.")
    print(f"Отключить восстановление: gnome-extensions disable {UUID}")
    return 1 if state in {"failed", "disabled"} else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Не удалось установить расширение: {error}", file=sys.stderr)
        sys.exit(1)

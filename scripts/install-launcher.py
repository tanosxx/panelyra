#!/usr/bin/python3
"""Install the current checkout's launcher for the current desktop user."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


APP_ID = "io.github.tanosx.Panelyra"
REPO = Path(__file__).resolve().parents[1]


def desktop_string(value):
    return (str(value).replace("\\", "\\\\").replace("\n", "\\n")
            .replace("\r", "\\r").replace("\t", "\\t"))


def exec_argument(value):
    # Desktop string escaping is decoded before the Exec argument is parsed.
    value = str(value).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        value = value.replace(char, "\\" + char)
    return desktop_string('"' + value + '"')


def render():
    template = (REPO / "desktop" / f"{APP_ID}.desktop.in").read_text(encoding="utf-8")
    values = {
        "@EXEC@": exec_argument(REPO / "panelyra"),
        "@PATH@": desktop_string(REPO),
        "@ICON@": desktop_string(REPO / "usbdisplay" / "assets" / "panelyra.svg"),
    }
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    return template


def write_atomic(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as target:
        temporary = Path(target.name)
        try:
            target.write(content)
            target.flush()
            os.fchmod(target.fileno(), mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="validate the generated desktop file without installing")
    args = parser.parse_args()
    needed = ["desktop-file-validate"]
    if not args.check:
        needed += ["xdg-user-dir", "gio", "update-desktop-database"]
    for command in needed:
        if shutil.which(command) is None:
            parser.error(f"Не найдена программа: {command}")

    content = render()
    with tempfile.TemporaryDirectory(prefix="usb-display-launcher-") as folder:
        check_file = Path(folder) / f"{APP_ID}.desktop"
        check_file.write_text(content, encoding="utf-8")
        subprocess.run(["desktop-file-validate", str(check_file)], check=True)
    if args.check:
        print("Panelyra launcher validated; no files installed.")
        return

    launcher = REPO / "panelyra"
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        parser.error(f"Нет исполняемого файла: {launcher}")
    desktop = Path(subprocess.check_output(
        ["xdg-user-dir", "DESKTOP"], text=True).strip())
    if not desktop.is_absolute() or desktop == Path.home():
        parser.error("В XDG не настроена отдельная папка рабочего стола.")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    if not data_home.is_absolute():
        parser.error("XDG_DATA_HOME должен быть абсолютным путём.")
    app_file = data_home / "applications" / f"{APP_ID}.desktop"
    desktop_file = desktop / f"{APP_ID}.desktop"
    write_atomic(app_file, content, 0o644)
    print(f"Меню приложений: {app_file}", flush=True)
    write_atomic(desktop_file, content, 0o755)
    print(f"Рабочий стол: {desktop_file}", flush=True)
    subprocess.run(["gio", "set", "-t", "string", str(desktop_file),
                    "metadata::trusted", "true"], check=True)
    subprocess.run(["update-desktop-database", str(app_file.parent)], check=True)
    print("Panelyra installed. Open its icon to start.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"Не удалось завершить установку ярлыка: {error}", file=sys.stderr)
        sys.exit(1)

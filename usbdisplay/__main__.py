import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__, runtime
from .settings import PRESETS
from .adb import Tunnel, run_adb, usb_devices
from .host import load_gi
from .resume import LockResumingStream as Stream
from .protocol import validate_mode
from . import network
from .download import default_apk, serve_apk
from .language import LanguageSync


def choose_transport(args):
    if args.transport == "adb":
        if args.interface or args.tablet_ip:
            raise ValueError("--interface и --tablet-ip относятся к USB-модему")
        return "adb", None
    if getattr(args, "serial", None):
        if args.transport == "usb" or args.interface or args.tablet_ip:
            raise ValueError("--serial относится к ADB, а не к USB-модему")
        return "adb", None
    links = network.discover_links(args.interface, args.tablet_ip)
    if links:
        if len(links) > 1:
            raise RuntimeError("Найдено несколько USB-соединений. Укажите --interface и --tablet-ip")
        links[0].require_tablet()
        return "usb", links[0]
    if args.transport == "usb" or args.interface or args.tablet_ip:
        raise RuntimeError("USB-модем не найден. Включите его в настройках планшета")
    return "adb", None


def doctor(args):
    failed = False
    print("Сеанс:", os.environ.get("XDG_SESSION_TYPE", "неизвестно"),
          "/", os.environ.get("XDG_CURRENT_DESKTOP", "неизвестно"))
    try:
        Gio, GLib, Gst = load_gi()
        for name in ("pipewiresrc", "videorate", "videoconvert", "x264enc", "appsink",
                     "videotestsrc", "textoverlay"):
            found = bool(Gst.ElementFactory.find(name))
            print(f"{name}: {'OK' if found else 'НЕ НАЙДЕН'}")
            failed |= not found
        animation = bool(Gst.ElementFactory.find("cairooverlay"))
        try:
            import gi
            import cairo
            gi.require_foreign("cairo")
        except (ImportError, ValueError):
            animation = False
        print("Анимация блокировки:", "OK" if animation else "простая заставка (Cairo не установлен)")
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        result = connection.call_sync(
            "org.gnome.Mutter.ScreenCast", "/org/gnome/Mutter/ScreenCast",
            "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", ("org.gnome.Mutter.ScreenCast", "Version")),
            None, Gio.DBusCallFlags.NONE, 3000, None)
        print("Mutter ScreenCast API:", result.unpack()[0])
    except Exception as error:
        print("Захват экрана недоступен:", error)
        failed = True
    try:
        transport, link = choose_transport(args)
        if transport == "usb":
            network.verify_route(link)
            print(f"USB-модем: OK ({link.interface})")
            print(f"ПК: {link.local_ip}; планшет: {link.tablet_ip}")
            print("ADB не требуется. Откройте Android-приложение перед запуском передачи.")
        else:
            print("ADB:", shutil.which("adb") or "НЕ УСТАНОВЛЕН")
            devices = usb_devices()
            print("Устройства ADB:", devices or "нет; включите USB-модем либо отладку по USB")
            failed |= not any(state == "device" for _, state in devices)
    except (RuntimeError, ValueError) as error:
        print(error)
        failed = True
    return int(failed)


def build_parser():
    parser = argparse.ArgumentParser(prog='panelyra',
        description='Turn an Android tablet into a second GNOME display over USB.')
    parser.add_argument('--version', action='version', version=f'Panelyra {__version__}')
    commands = parser.add_subparsers(dest='command', required=True)

    def usb_options(command):
        command.add_argument('--interface', help='USB network interface')
        command.add_argument('--tablet-ip', help='tablet IPv4 shown in the Android app')

    gui = commands.add_parser('gui', help='open the desktop application')
    gui.add_argument('--no-connect', action='store_true', help='open without starting a stream')
    gui.add_argument('--language', choices=('auto', 'en', 'ru'), default=None)
    check = commands.add_parser('doctor', help='check dependencies, desktop session and USB')
    check.add_argument('--transport', choices=('auto', 'usb', 'adb'), default='auto')
    usb_options(check)
    install = commands.add_parser('install', help='install the Android APK through USB/ADB')
    install.add_argument('--serial')
    install.add_argument('--apk', type=Path, default=default_apk())
    download = commands.add_parser('serve-apk', help='serve one APK to the tablet over USB tethering')
    usb_options(download)
    download.add_argument('--apk', type=Path, default=default_apk())
    download.add_argument('--port', type=int, default=8765)
    download.add_argument('--duration', type=int, default=1800)
    local = commands.add_parser('configure-usb', help='keep USB local and preserve the PC Internet route')
    usb_options(local)
    state = commands.add_parser('status', help='show the current Panelyra stream')
    state.add_argument('--json', action='store_true')
    commands.add_parser('stop', help='gracefully stop your Panelyra stream')
    presets = commands.add_parser('profiles', help='list display profiles')
    presets.add_argument('--json', action='store_true')
    start = commands.add_parser('start', help='create and stream a second display')
    start.add_argument('--transport', choices=('auto', 'usb', 'adb'), default='auto',
                       help='auto: prefer USB tethering, otherwise ADB')
    usb_options(start)
    start.add_argument('--profile', choices=tuple(PRESETS), default='balanced')
    start.add_argument('--wait', type=int, default=0, help='wait for the Android receiver, in seconds')
    start.add_argument('--width', type=int)
    start.add_argument('--height', type=int)
    start.add_argument('--fps', type=int, help='transmitted frames/s, 5–60 (balanced: 30)')
    start.add_argument('--capture-fps', type=int, help='desktop capture rate, between FPS and 60')
    start.add_argument('--rate-control', choices=('quality', 'bitrate'), default='quality')
    start.add_argument('--quality', type=int, help='constant QP 10–35; lower is sharper (balanced: 18)')
    start.add_argument('--bitrate', type=int, default=5000, help='kbit/s for bitrate mode only')
    start.add_argument('--serial', help='USB device serial for ADB')
    start.add_argument('--no-launch', action='store_true', help='do not open the Android app via ADB')
    start.add_argument('--test-pattern', action='store_true', help='send a synthetic image without a monitor')
    start.add_argument('--no-cursor-workaround', action='store_true', help='diagnostic direct RecordVirtual capture')
    start.add_argument('--duration', type=int, default=0, help='stop after N seconds; 0 means unlimited')
    start.add_argument('--stats', action='store_true', help='print sender FPS and USB throughput')
    start.add_argument('--json-events', action='store_true', help='emit structured lifecycle events for frontends')
    start.add_argument('--language', choices=('auto', 'en', 'ru'), default=None,
                       help='set Android UI language; auto follows the PC system language')
    start.add_argument('--control-stdin', action='store_true', help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    # Keep CLI operations usable without importing GTK.
    if args.command == 'gui':
        from .launcher import main as gui_main
        return gui_main(auto_connect=False if args.no_connect else None, language=args.language)
    if args.command == 'doctor':
        return doctor(args)
    try:
        if args.command == 'profiles':
            if args.json:
                print(json.dumps(PRESETS))
            else:
                for name, mode in PRESETS.items():
                    print(f"{name:10} {mode['width']}×{mode['height']}  {mode['fps']} fps  QP {mode['quality']}")
            return 0
        if args.command == 'status':
            state = runtime.status()
            print(json.dumps(state) if args.json else
                  (f"Panelyra running (PID {state.get('pid', 'starting')})" if state['running'] else 'Panelyra stopped'))
            return 0
        if args.command == 'stop':
            print('Stopping Panelyra…' if runtime.stop() else 'Panelyra is already stopped.')
            return 0
        if args.command == 'serve-apk':
            if not 1024 <= args.port <= 65535 or not 1 <= args.duration <= 86400:
                raise ValueError('Port: 1024–65535; duration: 1–86400 seconds')
            serve_apk(args.apk, network.select_link(args.interface, args.tablet_ip), args.port, args.duration)
            return 0
        if args.command == 'configure-usb':
            link = network.select_link(args.interface, args.tablet_ip)
            network.configure_local_only(link)
            print(f'{link.interface}: tablet Internet route and DNS disabled until USB reconnects.')
            return 0
        if args.command == 'install':
            if not args.apk.is_file():
                raise RuntimeError('APK not found. Build it with scripts/build-android.sh or pass --apk PATH.')
            print(run_adb(*([] if args.serial else ['-d']), 'install', '-r', str(args.apk.resolve()),
                          serial=args.serial, timeout=60))
            return 0
        for key, value in PRESETS[args.profile].items():
            if getattr(args, key) is None:
                setattr(args, key, value)
        validate_mode(args.width, args.height, args.fps)
        if not args.fps <= args.capture_fps <= 60:
            raise ValueError('Capture rate must be between --fps and 60')
        if not 100 <= args.bitrate <= 20000 or args.duration < 0:
            raise ValueError('Bitrate: 100–20000 kbit/s; duration must be nonnegative')
        if not 10 <= args.quality <= 35:
            raise ValueError('Quality QP must be between 10 and 35')
        if not args.test_pattern and os.environ.get('XDG_SESSION_TYPE') != 'wayland':
            raise RuntimeError('An active GNOME Wayland session is required')
        if not 0 <= args.wait <= 3600:
            raise ValueError('Wait must be between 0 and 3600 seconds')

        def event(name, **details):
            if args.json_events:
                print('@panelyra ' + json.dumps({'event': name, **details}), flush=True)

        mode = {name: getattr(args, name) for name in ('width', 'height', 'fps', 'capture_fps', 'quality')}
        with runtime.cancel_on_term(), runtime.Instance(mode):
            transport, link = choose_transport(args)
            event('connecting', transport=transport, **mode)
            def stream(connection):
                return Stream(connection, args.width, args.height, args.fps, args.bitrate,
                              args.test_pattern, args.duration,
                              cursor_workaround=not args.no_cursor_workaround,
                              quality=args.quality if args.rate_control == 'quality' else None,
                              stats_interval=5 if args.stats else 0,
                              capture_fps=args.capture_fps,
                              language=args.language,
                              on_status=lambda state: event(state)).run()
            def language_sync(**transport_options):
                return LanguageSync(args.language, **transport_options,
                                    input_stream=sys.stdin if args.control_stdin else None,
                                    on_result=lambda language, synced:
                                    event('language', language=language, synced=synced))
            if transport == 'usb':
                with network.connect(link, args.wait) as connection:
                    with language_sync(link=link) as languages:
                        languages.start()
                        stream(connection)
            else:
                with Tunnel(args.serial) as tunnel:
                    with language_sync(serial=tunnel.serial) as languages:
                        with tunnel.connect(launch=not args.no_launch) as connection:
                            languages.start()
                            stream(connection)
            event('stopped')
    except (RuntimeError, ValueError, OSError) as error:
        if getattr(args, 'json_events', False):
            print('@panelyra ' + json.dumps({'event': 'error', 'message': str(error)}), flush=True)
        print(f'Error: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())

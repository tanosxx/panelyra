"""Serve one selected APK on the Android USB link, with a bounded lifetime."""

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import signal
import threading
import time
from urllib.parse import parse_qs, urlsplit

from . import __version__, network


def default_apk():
    """Locate the development APK or the APK bundled by a package builder."""
    root = Path(__file__).resolve().parents[1]
    candidates = [root / 'out' / f'panelyra-{__version__}-android-debug.apk',
                  root / 'out' / 'usb-tablet-display-debug.apk', root / 'out' / 'panelyra.apk']
    return next((path for path in candidates if path.is_file()), candidates[0])


def _read_apk(apk):
    apk = Path(apk)
    if apk.suffix.lower() != '.apk' or not apk.is_file():
        raise RuntimeError("APK не найден. Выберите файл приложения Android (.apk)")
    try:
        return apk.read_bytes()
    except OSError as error:
        raise RuntimeError(f"Не удалось прочитать APK: {error}") from error


def _emit(callback, event, **details):
    # An observer (including a closing GUI) must never break request handling.
    if callback is not None:
        try:
            callback(event, **details)
        except Exception:
            pass


def _page(filename, size, language):
    russian = language == 'ru'
    title = 'Panelyra для Android' if russian else 'Panelyra for Android'
    intro = ('Установите приложение на планшет или обновите существующую версию.' if russian else
             'Install the tablet app or update your existing version.')
    download = 'Скачать APK' if russian else 'Download APK'
    steps = ([
        'Скачайте APK и откройте файл из загрузок браузера.',
        'Если Android попросит, разрешите этому браузеру устанавливать приложения.',
        'Нажмите «Обновить» или «Установить». Удалять прежнюю Panelyra не нужно.',
        'Откройте Panelyra. Оставьте USB-модем включённым и подключитесь с компьютера.',
    ] if russian else [
        'Download the APK and open it from your browser downloads.',
        'If Android asks, allow this browser to install applications.',
        'Tap Update or Install. You do not need to uninstall your existing Panelyra app.',
        'Open Panelyra. Keep USB tethering enabled and connect from your computer.',
    ])
    note = ('Страница работает по USB. Скачивание файла ещё не означает, что приложение установлено.'
            if russian else 'This page works over USB. Downloading the file does not install the app.')
    bytes_label = 'байт' if russian else 'bytes'
    links = ' · '.join(f"<a href='/?lang={code}' lang='{code}'"
                       f"{' aria-current=page' if language == code else ''}>{label}</a>"
                       for code, label in [('ru', 'Русский'), ('en', 'English')])
    return (f"<!doctype html><html lang='{language}'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}</title><style>"
            "body{margin:0;background:#111526;color:#f1f3ff;font:17px/1.6 system-ui,sans-serif}"
            "main{max-width:620px;margin:0 auto;padding:28px 24px 48px}"
            "nav{text-align:right;font-size:15px}a{color:#b4bcff}"
            "a[aria-current]{color:white;font-weight:bold}h1{font-size:32px;line-height:1.2;margin-top:42px}"
            ".file{overflow-wrap:anywhere;color:#c8cee8;font-size:14px}"
            ".download{display:inline-block;background:#6978ee;color:white;text-decoration:none;"
            "padding:14px 24px;margin:12px 0 20px;border-radius:12px;font-weight:bold}"
            "li{margin-bottom:14px;padding-left:5px}ol{padding-left:24px}"
            "footer{color:#aeb7d4;font-size:14px;margin-top:30px}</style></head><body><main>"
            f"<nav aria-label='Language'>{links}</nav><h1>{title}</h1><p>{intro}</p>"
            f"<p class='file'>{escape(filename)} · {size:,} {bytes_label}</p>"
            f"<a class='download' href='/usb-display.apk' download>{download}</a><ol>"
            + ''.join(f'<li>{step}</li>' for step in steps)
            + f"</ol><footer>{note}</footer></main></body></html>").encode('utf-8')


def handler_for(apk, link, language='ru', on_event=None, *, payload=None):
    """Create a handler; only the chosen APK and its landing page exist."""
    apk = Path(apk)
    payload = _read_apk(apk) if payload is None else payload
    allowed_peers = {link.require_tablet(), link.local_ip}
    language = 'ru' if language == 'ru' else 'en'

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Panelyra'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format, *args):
            pass

        def do_HEAD(self):
            self.send_content(head_only=True)

        def do_GET(self):
            self.send_content()

        def send_content(self, head_only=False):
            if self.client_address[0] not in allowed_peers:
                self.send_error(403)
                return
            parts = urlsplit(self.path)
            if parts.path == '/':
                try:
                    requested = parse_qs(parts.query, max_num_fields=32).get('lang', [])
                except ValueError:
                    self.send_error(400)
                    return
                chosen = requested[0] if len(requested) == 1 and requested[0] in ('en', 'ru') else language
                body = _page(apk.name, len(payload), chosen)
                mime = 'text/html; charset=utf-8'
            elif parts.path == '/usb-display.apk':
                body = payload
                mime = 'application/vnd.android.package-archive'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            if parts.path.endswith('.apk'):
                self.send_header('Content-Disposition', 'attachment; filename="usb-display.apk"')
            self.end_headers()
            if not head_only:
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    return
                if parts.path == '/usb-display.apk':
                    _emit(on_event, 'download', filename=apk.name, size=len(payload))

    return Handler


class DownloadServer:
    """A single-use USB APK server; create another instance to restart it.

    start() binds synchronously, so GUI callers should use a worker. stop() also
    cancels an in-flight start. Callbacks run on workers, and may raise safely.
    Startup validation/bind failures are raised by start(); background failures
    report error(message), followed by stopped(reason='error').
    """

    CHECK_INTERVAL = 2.0
    REQUEST_POLL = 0.15

    def __init__(self, apk, link, port=8765, duration=1800, language='en', on_event=None):
        self.apk = Path(apk)
        self.link = link
        self.port = port
        self.duration = duration
        self.language = language
        self.on_event = on_event
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._server = None
        self._thread = None
        self._starting = False
        self._running = False
        self._reason = None
        self._url = ''
        self._observed_peers = None

    @property
    def url(self):
        with self._lock:
            return self._url

    @property
    def running(self):
        with self._lock:
            return self._running and not self._stop.is_set()

    def _check_link(self):
        current = network.discover_links(interface=self.link.interface)
        matches = [link for link in current
                   if (link.interface, link.local_ip, link.prefix)
                   == (self.link.interface, self.link.local_ip, self.link.prefix)]
        if not matches:
            raise RuntimeError('USB-подключение изменилось или планшет отключён')
        # --tablet-ip can supply a peer when DHCP/NetworkManager discovery is
        # unavailable, or deliberately override the discovered gateway. Keep
        # that valid override, while detecting changes in discovery metadata.
        peers = frozenset(link.tablet_ip for link in matches)
        if self._observed_peers is None:
            self._observed_peers = peers
        elif peers != self._observed_peers:
            raise RuntimeError('Адрес планшета в USB-подключении изменился')
        network.verify_route(self.link)

    def start(self):
        with self._lock:
            if self._stop.is_set() or self._running:
                return self
            if self._starting:
                raise RuntimeError('Сервер APK уже запускается')
            self._starting = True
        server = None
        try:
            if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1024 <= self.port <= 65535:
                raise ValueError('Порт должен быть от 1024 до 65535')
            if isinstance(self.duration, bool) or not isinstance(self.duration, (int, float)) or not 1 <= self.duration <= 86400:
                raise ValueError('Время работы должно быть от 1 до 86400 секунд')
            if self.language not in ('en', 'ru'):
                raise ValueError('Язык должен быть en или ru')
            payload = _read_apk(self.apk)
            self.link.require_tablet()
            self._check_link()
            with self._lock:
                if self._stop.is_set():
                    return self
                server = ThreadingHTTPServer((self.link.local_ip, self.port),
                    handler_for(self.apk, self.link, self.language, self.on_event, payload=payload))
                if self._stop.is_set():
                    server.server_close()
                    return self
                server.daemon_threads = True
                server.timeout = self.REQUEST_POLL
                self._server = server
                self._url = f'http://{self.link.local_ip}:{server.server_port}/'
                self._running = True
                self._thread = threading.Thread(target=self._serve, args=(server, len(payload)),
                                                name='panelyra-apk-server', daemon=True)
                self._thread.start()
            return self
        except Exception:
            if server is not None:
                server.server_close()
            with self._lock:
                self._running = False
            raise
        finally:
            with self._lock:
                self._starting = False

    def _serve(self, server, size):
        monitor = threading.Thread(target=self._monitor, name='panelyra-apk-usb', daemon=True)
        deadline = time.monotonic() + self.duration
        try:
            _emit(self.on_event, 'started', url=self.url, apk=str(self.apk), size=size)
            monitor.start()
            while not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._request_stop('expired')
                    break
                server.timeout = min(self.REQUEST_POLL, remaining)
                server.handle_request()
        except Exception as error:
            if not self._stop.is_set():
                _emit(self.on_event, 'error', message=str(error))
                self._request_stop('error')
        finally:
            self._stop.set()
            server.server_close()
            with self._lock:
                self._running = False
                reason = self._reason or 'error'
            _emit(self.on_event, 'stopped', reason=reason)

    def _monitor(self):
        # The serving worker owns expiry, so even a slow network diagnostic
        # cannot extend the lifetime of the listener.
        while not self._stop.wait(self.CHECK_INTERVAL):
            try:
                self._check_link()
            except (OSError, RuntimeError, ValueError) as error:
                if not self._stop.is_set():
                    _emit(self.on_event, 'error', message=str(error))
                    self._request_stop('disconnected')
                return

    def _request_stop(self, reason):
        with self._lock:
            if self._reason is None:
                self._reason = reason
            self._stop.set()

    def stop(self):
        self._request_stop('requested')
        with self._lock:
            server, thread = self._server, self._thread
        if server is not None:
            server.server_close()
        if thread is not None and thread.ident is not None and thread is not threading.current_thread():
            thread.join(timeout=1)


def serve_apk(apk, link, port=8765, duration=1800, language='ru'):
    """Blocking CLI wrapper, preserving the existing command's interface."""
    server = DownloadServer(apk, link, port, duration, language)
    old_handlers = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.signal(sig, lambda *_: server.stop())
        server.start()
        print(f'Откройте на планшете: {server.url}', flush=True)
        print('Сервер отдаёт только APK через USB. Ctrl+C — остановить.', flush=True)
        while server.running:
            server._stop.wait(0.25)
    finally:
        server.stop()
        for sig, previous in old_handlers.items():
            signal.signal(sig, previous)

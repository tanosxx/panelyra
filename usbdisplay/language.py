"""Optional language control, kept separate from the UTD1 video connection."""

import json
import locale
import os
import select
import socket
import threading
import time

from . import network
from .adb import Tunnel


PORT = 27184
MAGIC = b'PLYL1'
TIMEOUT = 1.0
MAX_UPDATE = 128


def resolve_language(language='auto'):
    if language == 'auto':
        language = (os.environ.get('LANGUAGE') or locale.getlocale()[0]
                    or os.environ.get('LANG', 'en'))
        return 'ru' if language.lower().startswith('ru') else 'en'
    if language not in ('en', 'ru'):
        raise ValueError('Language must be auto, en or ru')
    return language


def parse_update(line):
    """Accept one bounded frontend command; malformed input never changes state."""
    if not isinstance(line, bytes) or len(line) > MAX_UPDATE:
        return None
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(value, dict) or set(value) != {'language'}:
        return None
    language = value['language']
    return language if language in ('en', 'ru') else None


def exchange(connection, language, timeout=TIMEOUT):
    """Use a deadline for the entire bounded request/ack, including peer EOF."""
    request = MAGIC + resolve_language(language).encode('ascii')
    deadline = time.monotonic() + timeout

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('Language control timed out')
        connection.settimeout(value)

    remaining()
    connection.sendall(request)
    connection.shutdown(socket.SHUT_WR)
    response = bytearray()
    while len(response) <= len(request):
        remaining()
        part = connection.recv(len(request) + 1 - len(response))
        if not part:
            return bytes(response) == request
        response.extend(part)
    return False


class LanguageSync:
    """Own an optional control channel for one video session.

    ADB forwarding allocation/removal stays on the main thread, using Tunnel's
    cancellation-safe ownership. All socket I/O and stdin handling are separate
    from video capture. Failure only reports synced=False; old UTD1 clients work.
    """

    def __init__(self, initial=None, *, link=None, serial=None, input_stream=None,
                 on_result=None):
        self.initial = None if initial is None else resolve_language(initial)
        self.link = link
        self.serial = serial
        self.input_stream = input_stream
        self.on_result = on_result
        self._condition = threading.Condition()
        self._closed = False
        self._input_closed = False
        self._generation = 0
        self._pending = None
        self._active_socket = None
        self._tunnel = None
        self._worker = None
        self._reader = None

    def __enter__(self):
        if (self.initial is not None or self.input_stream is not None) and self.serial is not None:
            tunnel = Tunnel(self.serial, device_port=PORT)
            try:
                tunnel.__enter__()
            except (OSError, RuntimeError, ValueError):
                # The optional control listener/forward must not disable video.
                pass
            else:
                self._tunnel = tunnel
        return self

    def start(self):
        if self._worker is not None or (self.initial is None and self.input_stream is None):
            return
        if self.initial is not None:
            self.update(self.initial)
        self._worker = threading.Thread(target=self._run, name='panelyra-language', daemon=True)
        self._worker.start()
        if self.input_stream is not None:
            self._reader = threading.Thread(target=self._read_updates,
                                            name='panelyra-language-input', daemon=True)
            self._reader.start()

    def update(self, language):
        if language not in ('en', 'ru'):
            return
        with self._condition:
            if not self._closed:
                self._generation += 1
                self._pending = (self._generation, language)
                self._condition.notify_all()

    def _read_updates(self):
        pending = bytearray()
        discard = False
        try:
            descriptor = self.input_stream.fileno()
            while not self._closed:
                readable, _, _ = select.select([descriptor], [], [], .1)
                if not readable:
                    continue
                chunk = os.read(descriptor, 4096)
                if not chunk:
                    return
                for byte in chunk:
                    if byte == 10:
                        if not discard:
                            language = parse_update(bytes(pending))
                            if language:
                                self.update(language)
                        pending.clear()
                        discard = False
                    elif not discard:
                        if len(pending) >= MAX_UPDATE:
                            pending.clear()
                            discard = True
                        else:
                            pending.append(byte)
        except (OSError, ValueError, AttributeError):
            pass
        finally:
            with self._condition:
                self._input_closed = True
                self._condition.notify_all()

    def _connect(self):
        tunnel = self._tunnel
        if self.link is not None:
            network.verify_route(self.link)
            address = (self.link.require_tablet(), PORT)
        elif tunnel is not None:
            address = ('127.0.0.1', tunnel.port)
        else:
            raise ConnectionError('Language control unavailable')
        with self._condition:
            if self._closed:
                raise ConnectionAbortedError('Language control stopped')
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._active_socket = connection
        connection.settimeout(TIMEOUT)
        if self.link is not None:
            connection.bind((self.link.local_ip, 0))
        connection.connect(address)
        return connection

    def _send(self, language):
        try:
            return exchange(self._connect(), language)
        except (OSError, RuntimeError, ValueError):
            return False
        finally:
            with self._condition:
                connection, self._active_socket = self._active_socket, None
            if connection is not None:
                connection.close()

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    if self._input_closed:
                        return
                    self._condition.wait()
                if self._closed:
                    return
                generation, language = self._pending
                self._pending = None
            synced = False
            # A new Android listener may start just after the video listener.
            for attempt in range(3):
                synced = self._send(language)
                with self._condition:
                    if self._closed or generation != self._generation:
                        break
                    if synced or attempt == 2:
                        if self.on_result is not None:
                            self.on_result(language, synced)
                        break
                    self._condition.wait(.3 * (attempt + 1))
                    if self._closed or generation != self._generation:
                        break

    def close(self):
        with self._condition:
            self._closed = True
            connection, self._active_socket = self._active_socket, None
            self._condition.notify_all()
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        # A route query can still be finishing; it checks _closed before opening
        # a socket. It must not hold up capture shutdown or make later callbacks.
        for thread in (self._worker, self._reader):
            if thread is not None:
                thread.join(.2)
        if self._tunnel is not None:
            tunnel, self._tunnel = self._tunnel, None
            tunnel.__exit__(None, None, None)

    def __exit__(self, *_):
        self.close()

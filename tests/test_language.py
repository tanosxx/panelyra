import io
import os
import socket
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from usbdisplay.language import LanguageSync, PORT, exchange, parse_update, resolve_language
from usbdisplay.network import UsbLink


class WireSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = None
        self.half_closed = False

    def settimeout(self, timeout):
        if timeout <= 0:
            raise AssertionError('missing deadline')

    def sendall(self, data):
        self.sent = data

    def shutdown(self, direction):
        if direction != socket.SHUT_WR:
            raise AssertionError('request needs write EOF')
        self.half_closed = True

    def recv(self, count):
        if not self.half_closed:
            raise AssertionError('receiver waits for request EOF')
        part = self.chunks.pop(0) if self.chunks else b''
        if isinstance(part, Exception):
            raise part
        if len(part) > count:
            self.chunks.insert(0, part[count:])
        return part[:count]


class LanguageTests(unittest.TestCase):
    def test_auto_follows_pc_language_with_english_fallback(self):
        with patch.dict(os.environ, {'LANGUAGE': 'ru_RU:en', 'LANG': 'en_US.UTF-8'}):
            self.assertEqual(resolve_language('auto'), 'ru')
        with patch.dict(os.environ, {'LANGUAGE': '', 'LANG': 'ru_RU.UTF-8'}), \
                patch('usbdisplay.language.locale.getlocale', return_value=('de_DE', 'UTF-8')):
            self.assertEqual(resolve_language('auto'), 'en')
        with patch.dict(os.environ, {'LANGUAGE': '', 'LANG': 'ru_RU.UTF-8'}), \
                patch('usbdisplay.language.locale.getlocale', return_value=(None, None)):
            self.assertEqual(resolve_language('auto'), 'ru')
        self.assertEqual(resolve_language('en'), 'en')
        with self.assertRaises(ValueError):
            resolve_language('fr')

    def test_wire_request_half_close_and_split_ack(self):
        connection = WireSocket([b'PL', b'YL1', b'ru', b''])
        self.assertTrue(exchange(connection, 'ru'))
        self.assertEqual(connection.sent, b'PLYL1ru')
        self.assertTrue(connection.half_closed)

    def test_ack_rejects_wrong_language_truncated_or_oversized(self):
        for response in (b'', b'PLYL1', b'PLYL1en', b'PLYL1ruEXTRA', b'UTD1ru'):
            with self.subTest(response=response):
                self.assertFalse(exchange(WireSocket([response]), 'ru'))

    def test_ack_timeout_is_bounded(self):
        with self.assertRaises(TimeoutError):
            exchange(WireSocket([b'P', TimeoutError('silent receiver')]), 'en')
        # A peer trickling bytes must not restart the whole deadline per byte.
        with patch('usbdisplay.language.time.monotonic', side_effect=[0, 0, .5, 1.1]):
            with self.assertRaises(TimeoutError):
                exchange(WireSocket([b'P', b'L']), 'en', timeout=1)

    def test_frontend_commands_only_accept_supported_bounded_language(self):
        self.assertEqual(parse_update(b'{"language":"ru"}'), 'ru')
        self.assertEqual(parse_update(b'{"language":"en"}\n'), 'en')
        for line in (b'', b'no', b'[]', b'null', b'{"language":"auto"}',
                     b'{"language":[]}', b'{"language":"en","command":"stop"}',
                     b'{"language":"ru"}' + b' ' * 128, b'\xff'):
            with self.subTest(line=line):
                self.assertIsNone(parse_update(line))

    def test_no_cli_language_does_not_touch_tablet_or_adb(self):
        with patch('usbdisplay.language.Tunnel') as tunnel, \
                patch('usbdisplay.language.socket.socket') as connection:
            with LanguageSync(serial='tablet') as sync:
                sync.start()
                self.assertIsNone(sync._worker)
            tunnel.assert_not_called()
            connection.assert_not_called()

    def test_usb_control_verifies_route_and_binds_only_selected_usb_address(self):
        link = UsbLink('rndis0', '192.168.42.8', 24, '192.168.42.129')
        connection = MagicMock()
        connection.recv.side_effect = [b'PLYL1en', b'']
        with patch('usbdisplay.language.network.verify_route') as route, \
                patch('usbdisplay.language.socket.socket', return_value=connection), \
                patch('usbdisplay.language.Tunnel') as tunnel:
            with LanguageSync('en', link=link) as sync:
                self.assertTrue(sync._send('en'))
            route.assert_called_once_with(link)
            connection.bind.assert_called_once_with(('192.168.42.8', 0))
            connection.connect.assert_called_once_with(('192.168.42.129', PORT))
            connection.close.assert_called_once()
            tunnel.assert_not_called()

    def test_non_usb_route_rejection_never_opens_socket(self):
        link = UsbLink('rndis0', '192.168.42.8', 24, '192.168.42.129')
        with patch('usbdisplay.language.network.verify_route', side_effect=RuntimeError('wrong route')), \
                patch('usbdisplay.language.socket.socket') as connection:
            with LanguageSync('ru', link=link) as sync:
                self.assertFalse(sync._send('ru'))
            connection.assert_not_called()

    def test_adb_owns_second_forward_on_same_serial_and_cleans_it(self):
        tunnel = MagicMock(port=42345)
        connection = MagicMock()
        connection.recv.side_effect = [b'PLYL1ru', b'']
        with patch('usbdisplay.language.Tunnel', return_value=tunnel) as factory, \
                patch('usbdisplay.language.socket.socket', return_value=connection):
            with LanguageSync('ru', serial='selected-usb-tablet') as sync:
                self.assertTrue(sync._send('ru'))
            sync.close()
            factory.assert_called_once_with('selected-usb-tablet', device_port=27184)
            connection.connect.assert_called_once_with(('127.0.0.1', 42345))
            connection.bind.assert_not_called()
            tunnel.__exit__.assert_called_once()

    def test_adb_optional_allocation_failure_does_not_raise(self):
        tunnel = MagicMock()
        tunnel.__enter__.side_effect = RuntimeError('forward unavailable')
        with patch('usbdisplay.language.Tunnel', return_value=tunnel):
            with LanguageSync('ru', serial='tablet') as sync:
                self.assertFalse(sync._send('ru'))

    def test_worker_retries_old_receiver_nonfatally_and_reports_failure(self):
        finished = threading.Event()
        results = []

        def result(language, synced):
            results.append((language, synced))
            finished.set()

        with LanguageSync('ru', on_result=result) as sync, \
                patch.object(sync, '_send', return_value=False) as send:
            sync.start()
            self.assertTrue(finished.wait(3))
            self.assertEqual(send.call_count, 3)
        self.assertEqual(results, [('ru', False)])

    def test_new_update_supersedes_inflight_and_intermediate_values(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        sent = []
        results = []

        def send(language):
            sent.append(language)
            if len(sent) == 1:
                started.set()
                release.wait(2)
            return True

        def result(language, synced):
            results.append((language, synced))
            finished.set()

        with LanguageSync('ru', on_result=result) as sync, patch.object(sync, '_send', side_effect=send):
            sync.start()
            self.assertTrue(started.wait(1))
            sync.update('en')
            sync.update('ru')
            sync.update('en')
            release.set()
            self.assertTrue(finished.wait(2))
        self.assertEqual(sent, ['ru', 'en'])
        self.assertEqual(results, [('en', True)])

    def test_stdin_discards_oversized_and_invalid_lines_recovers_and_exits_on_eof(self):
        read_fd, write_fd = os.pipe()
        finished = threading.Event()
        results = []
        with os.fdopen(read_fd, 'rb') as reader:
            with LanguageSync(input_stream=reader,
                              on_result=lambda language, synced: (results.append((language, synced)),
                                                                   finished.set())) as sync, \
                    patch.object(sync, '_send', return_value=True):
                sync.start()
                os.write(write_fd, b'x' * 1024 + b'\n{"language":"auto"}\n{"language":"ru"}\n')
                os.close(write_fd)
                self.assertTrue(finished.wait(2))
                sync._reader.join(1)
                sync._worker.join(1)
                self.assertFalse(sync._reader.is_alive())
                self.assertFalse(sync._worker.is_alive())
        self.assertEqual(results, [('ru', True)])

    def test_close_does_not_wait_for_stdin_and_late_route_check_cannot_open_socket(self):
        read_fd, write_fd = os.pipe()
        entered = threading.Event()
        release = threading.Event()
        link = UsbLink('rndis0', '192.168.42.8', 24, '192.168.42.129')

        def route(_):
            entered.set()
            release.wait(2)

        try:
            with os.fdopen(read_fd, 'rb') as reader, \
                    patch('usbdisplay.language.network.verify_route', side_effect=route), \
                    patch('usbdisplay.language.socket.socket') as connection:
                sync = LanguageSync('en', link=link, input_stream=reader)
                sync.start()
                self.assertTrue(entered.wait(1))
                start = time.monotonic()
                sync.close()
                self.assertLess(time.monotonic() - start, .6)
                release.set()
                sync._worker.join(1)
                sync._reader.join(1)
                connection.assert_not_called()
                self.assertFalse(sync._worker.is_alive())
                self.assertFalse(sync._reader.is_alive())
        finally:
            release.set()
            os.close(write_fd)

    def test_cli_option_default_is_no_override_and_language_failure_keeps_video(self):
        from usbdisplay import __main__ as cli
        self.assertIsNone(cli.build_parser().parse_args(['start']).language)
        self.assertEqual(cli.build_parser().parse_args(['start', '--language', 'auto']).language, 'auto')
        link = UsbLink('rndis0', '192.168.42.8', 24, '192.168.42.129')
        with patch.object(cli.runtime, 'Instance'), patch.object(cli.runtime, 'cancel_on_term'), \
                patch.object(cli, 'choose_transport', return_value=('usb', link)), \
                patch.object(cli.network, 'connect'), patch.object(cli, 'Stream') as stream, \
                patch('usbdisplay.language.network.verify_route', side_effect=RuntimeError('old receiver')), \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(cli.main(['start', '--test-pattern', '--language', 'ru']), 0)
            stream.return_value.run.assert_called_once()


if __name__ == '__main__':
    unittest.main()

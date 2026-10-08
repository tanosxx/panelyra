"""The local lock theme never becomes an Android or video protocol message."""

import io
import json
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from usbdisplay import __main__ as cli
from usbdisplay.host import prepare_lock_scene
from usbdisplay.language import LanguageSync, parse_theme_update, parse_update
from usbdisplay.network import UsbLink
from usbdisplay.themes import DEFAULT_THEME, THEME_IDS


class ThemeControlTests(unittest.TestCase):
    def test_parser_accepts_only_one_known_theme_and_preserves_language_parser(self):
        for theme in THEME_IDS:
            command = json.dumps({'theme': theme}).encode('utf-8')
            self.assertEqual(parse_theme_update(command), theme)
            self.assertIsNone(parse_update(command))
        for command in (b'', b'[]', b'null', b'\xff', b'{"theme":[]}',
                        b'{"theme":true}', b'{"theme":null}', b'{"theme":"unknown"}',
                        b'{"theme":"aurora","language":"ru"}',
                        b'{"theme":"aurora","command":"stop"}',
                        b'{"theme":"aurora"}' + b' ' * 128,
                        b'{"language":"ru"}', '{"theme":"aurora"}'):
            with self.subTest(command=command):
                self.assertIsNone(parse_theme_update(command))
        self.assertEqual(parse_update(b'{"language":"ru"}'), 'ru')

    def test_theme_reader_recovers_after_bad_lines_without_any_network_request(self):
        read_fd, write_fd = os.pipe()
        received = []
        link = UsbLink('rndis0', '192.168.42.8', 24, '192.168.42.129')
        with os.fdopen(read_fd, 'rb') as reader, \
                patch('usbdisplay.language.socket.socket') as connection, \
                patch('usbdisplay.language.network.verify_route') as route, \
                LanguageSync(link=link, input_stream=reader, on_theme=received.append) as sync:
            sync.start()
            os.write(write_fd, b'{"theme":"editorial"}' + b' ' * 1024 +
                     b'\n{"theme":"unknown"}\n{"theme":"light","language":"en"}\n' +
                     b'{"theme":"aurora"}\n{"theme":"graphite"}\n')
            os.close(write_fd)
            sync._reader.join(2)
            sync._worker.join(2)
            self.assertFalse(sync._reader.is_alive())
            self.assertFalse(sync._worker.is_alive())
            connection.assert_not_called()
            route.assert_not_called()
        self.assertEqual(received, ['aurora', 'graphite'])

    def test_local_callbacks_do_not_wait_for_android_language_ack(self):
        read_fd, write_fd = os.pipe()
        network_started = threading.Event()
        release_network = threading.Event()
        theme_received = threading.Event()
        local_languages = []
        local_themes = []

        def send(_language):
            network_started.set()
            release_network.wait(3)
            return False

        def theme_changed(theme):
            local_themes.append(theme)
            theme_received.set()

        try:
            with os.fdopen(read_fd, 'rb') as reader, \
                    LanguageSync('ru', input_stream=reader,
                                 on_language=local_languages.append,
                                 on_theme=theme_changed) as sync, \
                    patch.object(sync, '_send', side_effect=send):
                sync.start()
                self.assertTrue(network_started.wait(1))
                self.assertEqual(local_languages, ['ru'])
                os.write(write_fd, b'{"language":"en"}\n{"theme":"midnight"}\n')
                self.assertTrue(theme_received.wait(1))
                self.assertEqual(local_languages, ['ru', 'en'])
                self.assertEqual(local_themes, ['midnight'])
                self.assertFalse(release_network.is_set())
                release_network.set()
        finally:
            release_network.set()
            os.close(write_fd)

    def test_theme_callback_survives_missing_adb_language_forward(self):
        read_fd, write_fd = os.pipe()
        callback = Mock()
        tunnel = MagicMock()
        tunnel.__enter__.side_effect = RuntimeError('old receiver')
        with os.fdopen(read_fd, 'rb') as reader, \
                patch('usbdisplay.language.Tunnel', return_value=tunnel), \
                patch('usbdisplay.language.socket.socket') as connection, \
                LanguageSync(serial='tablet', input_stream=reader, on_theme=callback) as sync:
            sync.start()
            os.write(write_fd, b'{"theme":"editorial"}\n')
            os.close(write_fd)
            sync._reader.join(2)
            callback.assert_called_once_with('editorial')
            connection.assert_not_called()

    def test_closed_language_control_does_not_change_local_state(self):
        local_language = Mock()
        with LanguageSync(on_language=local_language) as sync:
            sync.update('ru')
        sync.update('en')
        local_language.assert_called_once_with('ru')

    def test_cli_validates_theme_and_keeps_classic_default(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(['start']).theme, DEFAULT_THEME)
        for theme in THEME_IDS:
            self.assertEqual(parser.parse_args(['start', '--theme', theme]).theme, theme)
        with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
            parser.parse_args(['start', '--theme', 'foreign'])

    def test_cli_constructs_stream_and_callbacks_before_reader_for_both_transports(self):
        link = UsbLink('rndis0', '192.168.42.8', 24, '192.168.42.129')
        for transport in ('usb', 'adb'):
            with self.subTest(transport=transport), \
                    patch.object(cli.runtime, 'Instance'), \
                    patch.object(cli.runtime, 'cancel_on_term'), \
                    patch.object(cli, 'choose_transport', return_value=(transport, link)), \
                    patch.object(cli.network, 'connect'), patch.object(cli, 'Tunnel'), \
                    patch.object(cli, 'Stream') as stream, \
                    patch.object(cli, 'LanguageSync') as language_sync:
                sync = language_sync.return_value.__enter__.return_value

                def reader_start():
                    stream.assert_called_once()
                    self.assertEqual(stream.call_args.kwargs['theme'], 'aurora')
                    kwargs = language_sync.call_args.kwargs
                    self.assertIs(kwargs['on_theme'], stream.return_value.set_theme)
                    self.assertIs(kwargs['on_language'], stream.return_value.set_language)
                    kwargs['on_theme']('editorial')
                    kwargs['on_language']('en')

                sync.start.side_effect = reader_start
                self.assertEqual(cli.main(['start', '--test-pattern', '--theme', 'aurora',
                                           '--control-stdin']), 0)
                stream.return_value.set_theme.assert_called_once_with('editorial')
                stream.return_value.set_language.assert_called_once_with('en')
                stream.return_value.run.assert_called_once_with()
                language_sync.return_value.__exit__.assert_called_once()

    def test_scene_factory_receives_selected_theme_and_keeps_static_fallback(self):
        scene = Mock()
        gst = MagicMock()
        modules = {'gi': Mock(), 'usbdisplay.lockscreen': SimpleNamespace(LockScene=scene)}
        with patch.dict('sys.modules', modules):
            result = prepare_lock_scene(1920, 1200, gst, 'ru', theme='graphite')
            self.assertIs(result, scene.return_value)
            scene.assert_called_once_with(1920, 1200, 'ru', theme='graphite')
            gst.ElementFactory.find.return_value = None
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertIsNone(prepare_lock_scene(1920, 1200, gst, 'en', theme='aurora'))


if __name__ == '__main__':
    unittest.main()

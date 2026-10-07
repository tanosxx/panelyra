import contextlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from usbdisplay.download import DownloadServer, default_apk, handler_for, serve_apk
from usbdisplay.network import UsbLink


class ApkDownloadFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.payload = b"PK\x03\x04test Android application\x00\xff"
        self.apk = self.directory / "app.apk"
        self.apk.write_bytes(self.payload)
        (self.directory / "secret.txt").write_text("not exposed")

    @contextlib.contextmanager
    def server(self, *, allow=True, as_tablet=False, language='ru', on_event=None):
        # The handler is exercised over real HTTP, while the allowed peer set is
        # supplied independently of the test server's loopback bind address.
        link = Mock(local_ip="127.0.0.1" if allow and not as_tablet else "192.168.42.50")
        link.require_tablet.return_value = "127.0.0.1" if as_tablet else "192.168.42.129"
        with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.apk, link, language, on_event)) as server:
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                yield server.server_address
            finally:
                server.shutdown()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())

    def request(self, address, path, method="GET"):
        connection = HTTPConnection(*address, timeout=2)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


class ApkDownloadTests(ApkDownloadFixture, unittest.TestCase):
    def test_root_and_apk_are_served_with_matching_head_lengths(self):
        with self.server() as address:
            for path, mime in (("/", "text/html; charset=utf-8"),
                               ("/usb-display.apk", "application/vnd.android.package-archive")):
                with self.subTest(path=path):
                    status, headers, body = self.request(address, path)
                    head_status, head_headers, head_body = self.request(address, path, "HEAD")
                    self.assertEqual((status, head_status), (200, 200))
                    self.assertEqual(headers["Content-Type"], mime)
                    self.assertEqual(headers["Content-Length"], str(len(body)))
                    self.assertEqual(head_headers["Content-Length"], headers["Content-Length"])
                    self.assertEqual(head_headers["Content-Type"], mime)
                    self.assertEqual(head_body, b"")
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                    if path.endswith(".apk"):
                        self.assertEqual(body, self.payload)
                        self.assertIn('filename="usb-display.apk"', headers["Content-Disposition"])
                    else:
                        self.assertIn(b"href='/usb-display.apk'", body)
                        self.assertNotIn(b"secret.txt", body)

    def test_no_directory_or_arbitrary_file_is_exposed(self):
        with self.server() as address:
            for path in ("/secret.txt", "/app.apk", "/out/", "/../secret.txt",
                         "/%2e%2e/secret.txt", "/usb-display.apk/../secret.txt",
                         "/usb-display.apk/", "/android/"):
                with self.subTest(path=path):
                    status, _, body = self.request(address, path)
                    self.assertEqual(status, 404)
                    self.assertNotIn(b"not exposed", body)
                    self.assertNotIn(self.payload, body)

    def test_unauthorized_peer_cannot_read_any_endpoint(self):
        with self.server(allow=False) as address:
            for method in ("GET", "HEAD"):
                for path in ("/", "/usb-display.apk", "/secret.txt"):
                    with self.subTest(method=method, path=path):
                        status, _, body = self.request(address, path, method)
                        self.assertEqual(status, 403)
                        self.assertNotIn(self.payload, body)
                        if method == "HEAD":
                            self.assertEqual(body, b"")

    def test_tablet_peer_can_download_without_matching_the_pc_address(self):
        with self.server(as_tablet=True) as address:
            status, _, body = self.request(address, "/usb-display.apk")
            self.assertEqual(status, 200)
            self.assertEqual(body, self.payload)

    def test_missing_apk_fails_before_creating_listener(self):
        with patch("usbdisplay.download.ThreadingHTTPServer") as server:
            with self.assertRaisesRegex(RuntimeError, "APK"):
                serve_apk(self.directory / "missing.apk", Mock())
            server.assert_not_called()

    def test_page_follows_pc_language_and_can_switch_in_browser(self):
        for default in ('en', 'ru'):
            with self.subTest(default=default), self.server(language=default) as address:
                for path, expected in (('/', default), ('/?lang=ru', 'ru'), ('/?lang=en', 'en'),
                                       ('/?lang=de', default), ('/?lang=ru&lang=en', default)):
                    status, _, body = self.request(address, path)
                    page = body.decode('utf-8')
                    self.assertEqual(status, 200)
                    self.assertIn(f"<html lang='{expected}'>", page)
                    self.assertIn("href='/?lang=ru'", page)
                    self.assertIn("href='/?lang=en'", page)
                    self.assertIn('Скачать APK' if expected == 'ru' else 'Download APK', page)
                    self.assertIn(self.apk.name, page)
                    self.assertIn(str(len(self.payload)), page)

    def test_filename_is_html_escaped_and_download_header_stays_safe(self):
        self.apk = self.directory / '<img src=x>&".apk'
        self.apk.write_bytes(self.payload)
        with self.server() as address:
            _, _, body = self.request(address, '/?lang=%3Cscript%3E')
            self.assertIn(b'&lt;img src=x&gt;&amp;&quot;.apk', body)
            self.assertNotIn(b'<img src=x>', body)
            _, headers, body = self.request(address, '/usb-display.apk?lang=en')
            self.assertEqual(headers['Content-Disposition'], 'attachment; filename="usb-display.apk"')
            self.assertEqual(body, self.payload)

    def test_excessive_query_fields_are_rejected_without_affecting_server(self):
        with self.server() as address:
            self.assertEqual(self.request(address, '/?' + '&'.join(['x=1'] * 33))[0], 400)
            self.assertEqual(self.request(address, '/usb-display.apk')[0], 200)

    def test_only_completed_apk_get_reports_a_download(self):
        received = threading.Event()
        events = []

        def callback(name, **details):
            events.append((name, details))
            received.set()

        with self.server(on_event=callback) as address:
            self.request(address, '/')
            self.request(address, '/usb-display.apk', 'HEAD')
            self.assertEqual(events, [])
            self.request(address, '/usb-display.apk')
            self.assertTrue(received.wait(1))
            self.assertEqual(events, [('download', {'filename': self.apk.name, 'size': len(self.payload)})])

    def test_raising_callback_does_not_break_downloads(self):
        callback = Mock(side_effect=RuntimeError('observer has closed'))
        with self.server(on_event=callback) as address:
            for _ in range(2):
                self.assertEqual(self.request(address, '/usb-display.apk')[2], self.payload)


class ApkServerLifecycleTests(ApkDownloadFixture, unittest.TestCase):
    """Real listener lifecycle, with only USB discovery and routes simulated."""

    def setUp(self):
        super().setUp()
        self.link = Mock(interface='test-usb', local_ip='127.0.0.1', prefix=24, tablet_ip='127.0.0.2')
        self.link.require_tablet.return_value = '127.0.0.2'
        self.discovery = self.enterContext(patch('usbdisplay.download.network.discover_links',
                                                 return_value=[self.link]))
        self.route = self.enterContext(patch('usbdisplay.download.network.verify_route'))
        self.events = []
        self.stopped = threading.Event()
        self.started = threading.Event()

    def event(self, name, **details):
        self.events.append((name, details))
        if name == 'started':
            self.started.set()
        elif name == 'stopped':
            self.stopped.set()

    def create(self, **options):
        # Reserve a currently free unprivileged port, without permitting port 0
        # in the public server API (the settings UI displays the chosen port).
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        options.setdefault('port', port)
        options.setdefault('on_event', self.event)
        server = DownloadServer(self.apk, self.link, **options)
        self.addCleanup(server.stop)
        return server

    def address(self, server):
        return '127.0.0.1', int(server.url.rstrip('/').rsplit(':', 1)[1])

    def test_start_stop_and_restart_release_the_port(self):
        server = self.create()
        self.assertIs(server.start(), server)
        self.assertTrue(self.started.wait(1))
        self.assertTrue(server.running)
        self.assertEqual(self.request(self.address(server), '/usb-display.apk')[2], self.payload)
        self.discovery.assert_called_with(interface='test-usb')
        self.route.assert_called_with(self.link)
        before = time.monotonic()
        server.stop()
        server.stop()
        self.assertLess(time.monotonic() - before, 1)
        self.assertFalse(server.running)
        self.assertTrue(self.stopped.wait(1))
        self.assertEqual([detail['reason'] for name, detail in self.events if name == 'stopped'], ['requested'])
        with self.assertRaises(OSError):
            self.request(self.address(server), '/')
        replacement = self.create(port=server.port)
        replacement.start()
        self.assertTrue(replacement.running)
        self.assertEqual(self.request(self.address(replacement), '/')[0], 200)

    def test_duration_expiry_closes_listener(self):
        server = self.create(duration=1).start()
        self.assertTrue(self.stopped.wait(2))
        self.assertFalse(server.running)
        self.assertIn(('stopped', {'reason': 'expired'}), self.events)
        with self.assertRaises(OSError):
            self.request(self.address(server), '/')

    def test_disappearing_usb_stops_server(self):
        server = self.create()
        server.CHECK_INTERVAL = 0.02
        server.start()
        self.discovery.return_value = []
        self.assertTrue(self.stopped.wait(1))
        self.assertFalse(server.running)
        self.assertIn(('stopped', {'reason': 'disconnected'}), self.events)

    def test_slow_usb_diagnostics_cannot_delay_expiry(self):
        entered, release = threading.Event(), threading.Event()
        server = self.create(duration=1)
        server.CHECK_INTERVAL = 0.02
        server.start()

        def slow_route(_):
            entered.set()
            release.wait(3)

        self.route.side_effect = slow_route
        try:
            self.assertTrue(entered.wait(0.5))
            self.assertTrue(self.stopped.wait(1.5))
            self.assertFalse(server.running)
            self.assertIn(('stopped', {'reason': 'expired'}), self.events)
        finally:
            release.set()

    def test_changed_address_or_peer_stops_server(self):
        for changed in ('local_ip', 'tablet_ip'):
            with self.subTest(changed=changed):
                self.stopped.clear()
                self.discovery.return_value = [self.link]
                server = self.create()
                server.CHECK_INTERVAL = 0.02
                server.start()
                changed_link = Mock(interface='test-usb', local_ip='127.0.0.1', prefix=24,
                                    tablet_ip='127.0.0.2')
                setattr(changed_link, changed, '192.168.99.1')
                self.discovery.return_value = [changed_link]
                self.assertTrue(self.stopped.wait(1))
                self.assertFalse(server.running)

    def test_discovery_failure_stops_server_and_reports_error(self):
        server = self.create()
        server.CHECK_INTERVAL = 0.02
        server.start()
        self.discovery.side_effect = RuntimeError('discovery unavailable')
        self.assertTrue(self.stopped.wait(1))
        self.assertIn(('error', {'message': 'discovery unavailable'}), self.events)
        self.assertFalse(server.running)

    def test_stop_before_start_prevents_a_listener(self):
        server = self.create()
        server.stop()
        with patch('usbdisplay.download.ThreadingHTTPServer') as listener:
            server.start()
            listener.assert_not_called()
        self.assertFalse(server.running)

    def test_stop_during_start_prevents_a_late_listener(self):
        entered, release = threading.Event(), threading.Event()

        def slow_route(_):
            entered.set()
            release.wait(2)

        self.route.side_effect = slow_route
        server = self.create()
        with patch('usbdisplay.download.ThreadingHTTPServer') as listener:
            thread = threading.Thread(target=server.start)
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                server.stop()
            finally:
                release.set()
                thread.join(2)
            self.assertFalse(thread.is_alive())
            listener.assert_not_called()
        self.assertFalse(server.running)

    def test_busy_port_leaves_no_running_server(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen(1)
            server = self.create(port=occupied.getsockname()[1])
            with self.assertRaises(OSError):
                server.start()
            self.assertFalse(server.running)

    def test_invalid_options_fail_without_binding(self):
        for options in ({'port': 0}, {'port': 1023}, {'port': 65536}, {'port': True},
                        {'duration': 0}, {'duration': 86401}, {'language': 'de'}):
            with self.subTest(options=options), patch('usbdisplay.download.ThreadingHTTPServer') as listener:
                with self.assertRaises(ValueError):
                    self.create(**options).start()
                listener.assert_not_called()

    def test_non_apk_file_is_rejected(self):
        server = self.create()
        server.apk = self.directory / 'secret.txt'
        with self.assertRaisesRegex(RuntimeError, 'APK'):
            server.start()

    def test_callbacks_may_raise_without_leaving_a_server_running(self):
        server = self.create(on_event=Mock(side_effect=RuntimeError('observer gone'))).start()
        self.assertEqual(self.request(self.address(server), '/usb-display.apk')[2], self.payload)
        server.stop()
        self.assertFalse(server.running)

    def test_packaged_apk_fallback_is_discovered(self):
        root = self.directory / 'installation'
        (root / 'out').mkdir(parents=True)
        packaged = root / 'out' / 'panelyra.apk'
        packaged.write_bytes(self.payload)
        with patch('usbdisplay.download.__file__', str(root / 'usbdisplay' / 'download.py')):
            self.assertEqual(default_apk(), packaged)

    def test_explicit_peer_works_without_discovered_dhcp_gateway(self):
        selected = UsbLink('test-usb', '192.168.42.8', 24, '192.168.42.129')
        for discovered_peer in (None, '192.168.42.1'):
            with self.subTest(discovered_peer=discovered_peer):
                self.discovery.return_value = [UsbLink('test-usb', '192.168.42.8', 24, discovered_peer)]
                server = DownloadServer(self.apk, selected)
                server._check_link()
                server._check_link()
                self.route.assert_called_with(selected)
                self.discovery.return_value = [UsbLink('test-usb', '192.168.42.8', 24, '192.168.42.2')]
                with self.assertRaisesRegex(RuntimeError, 'изменился'):
                    server._check_link()


if __name__ == "__main__":
    unittest.main()

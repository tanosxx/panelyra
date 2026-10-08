"""Exercise the real notification widgets with isolated state and no network.

GTK tests are skipped on hosts without a display. Fetchers and clocks are
injected; every cache write is confined to a temporary directory.
"""

from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from usbdisplay.notifications import Notice, Source, Store
from usbdisplay.settings import Settings

try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from usbdisplay import launcher, notifications_ui
    from gtk_support import get_test_application
except (ImportError, ValueError):
    Gtk = None


def descendants(widget):
    yield widget
    if isinstance(widget, Gtk.Container):
        for child in widget.get_children():
            yield from descendants(child)


class App:
    russian = False

    def __init__(self):
        self.window = Gtk.Window()

    def tr(self, english, russian):
        return russian if self.russian else english


def news(identifier="welcome", *, url=None):
    return Notice("news:" + identifier, "news", {"en": "From the developer", "ru": "От разработчика"},
                  {"en": "Saved news remains available offline.", "ru": "Новости доступны без сети."},
                  "2026-10-06", url=url)


@unittest.skipIf(Gtk is None, "GTK 3 is not installed")
class NotificationWidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check(None)[0]:
            raise unittest.SkipTest("No GTK display is available")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="panelyra-news-ui-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "notifications.json"
        self.source = Source("tanosx/Panelyra")
        self.now = 100000.0
        self.app = App()
        self.addCleanup(self.app.window.destroy)
        self.fetcher = Mock(return_value=[news()])
        # Even if a test accidentally omits its fetcher, it cannot reach GitHub.
        guard = patch.object(notifications_ui, "fetch_notices",
                             side_effect=AssertionError("Unexpected network access"))
        guard.start()
        self.addCleanup(guard.stop)

    def make_center(self, *, notices=(), checked_at=1, source=None, fetcher=None, store=None):
        source = self.source if source is None else source
        if store is None:
            # Keep the simulated installed version stable as the app advances.
            store = Store(path=self.path, source=source, current_version="0.3.0")
            if notices:
                store.replace_notices(list(notices), checked_at=checked_at)
        center = notifications_ui.NotificationCenter(
            self.app, source=source, store=store,
            fetcher=self.fetcher if fetcher is None else fetcher, clock=lambda: self.now)
        self.addCleanup(center.close)
        self.app.window.add(center.button)
        return center

    @staticmethod
    def labels(widget):
        return [item.get_text() for item in descendants(widget) if isinstance(item, Gtk.Label)]

    def drain_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        context = GLib.MainContext.default()
        while not predicate() and time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.002)
        self.assertTrue(predicate(), "The notification worker did not finish")

    def test_unpublished_build_never_starts_a_worker_or_periodic_timer(self):
        center = self.make_center(source=Source(""))
        with patch.object(notifications_ui.threading, "Thread") as thread, \
                patch.object(GLib, "timeout_add_seconds") as timer:
            center.start()
            center.refresh()
            center.tick()
        thread.assert_not_called()
        timer.assert_not_called()
        self.fetcher.assert_not_called()
        self.assertFalse(self.path.exists())
        self.assertFalse(center.refresh_button.get_sensitive())
        self.assertFalse(center.automatic.get_sensitive())
        self.assertIn("after the project is published", center.status.get_text())
        self.assertIn("No Internet requests", center.privacy.get_text())

    def test_fetch_is_background_nonblocking_and_does_not_duplicate_requests(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        worker_ids = []

        def blocked_fetch(source, version):
            worker_ids.append(threading.get_ident())
            entered.set()
            if not release.wait(2):
                raise RuntimeError("Test did not release worker")
            return [news()]

        center = self.make_center(fetcher=blocked_fetch)
        center.refresh()
        self.assertTrue(entered.wait(1))
        self.assertTrue(center.loading)
        self.assertFalse(center.refresh_button.get_sensitive())
        self.assertNotEqual(worker_ids, [threading.get_ident()])
        center.refresh()
        center.tick()
        self.assertEqual(len(worker_ids), 1)
        responsive = []
        GLib.idle_add(lambda: responsive.append(True) and False)
        self.drain_until(lambda: bool(responsive))
        self.assertTrue(center.loading)
        self.assertFalse(self.path.exists())
        release.set()
        self.drain_until(lambda: not center.loading)
        self.assertEqual(center.store.unread_count, 1)
        self.assertEqual(Store(self.path, self.source).last_checked, self.now)

    def test_close_cancels_timer_and_ignores_a_late_worker_result(self):
        center = self.make_center(notices=[news()], checked_at=self.now)
        before = self.path.read_bytes()
        center.start()
        self.assertIsNotNone(center.timer)
        with patch.object(GLib, "source_remove", wraps=GLib.source_remove) as remove:
            timer = center.timer
            center.close()
            center.close()
        remove.assert_called_once_with(timer)
        self.assertIsNone(center.timer)
        self.assertFalse(center.on_result([news("later")], None))
        self.assertFalse(center.tick())
        center.refresh()
        self.fetcher.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_offline_retains_cached_messages_and_retries_after_fifteen_minutes(self):
        center = self.make_center(notices=[news()], checked_at=1)
        before = self.path.read_bytes()
        self.fetcher.side_effect = OSError("Network is offline")
        center.tick()
        self.drain_until(lambda: not center.loading)
        self.assertEqual(center.error, "network")
        self.assertIn("Saved messages", center.status.get_text())
        self.assertEqual(center.store.notices, [news()])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.fetcher.call_count, 1)
        self.now += notifications_ui.RETRY_INTERVAL - 1
        center.tick()
        self.assertEqual(self.fetcher.call_count, 1)
        self.now += 1
        center.tick()
        self.drain_until(lambda: not center.loading)
        self.assertEqual(self.fetcher.call_count, 2)
        self.now += 1
        center.refresh_button.clicked()
        self.drain_until(lambda: not center.loading)
        self.assertEqual(self.fetcher.call_count, 3)

    def test_recent_success_waits_six_hours_before_automatic_check(self):
        center = self.make_center(notices=[news()], checked_at=self.now)
        center.start()
        self.fetcher.assert_not_called()
        self.now += notifications_ui.CHECK_INTERVAL - 1
        center.tick()
        self.fetcher.assert_not_called()
        self.now += 1
        center.tick()
        self.drain_until(lambda: not center.loading)
        self.fetcher.assert_called_once()
        self.assertEqual(center.store.last_checked, self.now)

    def test_disabling_automatic_checks_persists_but_manual_check_still_works(self):
        center = self.make_center(notices=[news()], checked_at=1)
        center.automatic.set_active(False)
        self.assertFalse(Store(self.path, self.source).auto_check)
        center.start()
        self.now += notifications_ui.CHECK_INTERVAL * 2
        center.tick()
        self.fetcher.assert_not_called()
        center.refresh_button.clicked()
        self.drain_until(lambda: not center.loading)
        self.fetcher.assert_called_once()
        self.assertFalse(center.store.auto_check)
        self.assertFalse(Store(self.path, self.source).auto_check)

    def test_unread_count_and_mark_all_read_survive_reopening(self):
        center = self.make_center(notices=[news(), news("second")])
        self.assertEqual(center.counter.get_text(), "2")
        self.assertTrue(center.counter.get_visible())
        self.assertTrue(center.read_button.get_sensitive())
        center.read_button.clicked()
        self.assertEqual(center.store.unread_count, 0)
        self.assertFalse(center.counter.get_visible())
        self.assertFalse(center.read_button.get_sensitive())
        reloaded = Store(self.path, self.source)
        self.assertEqual(reloaded.unread_count, 0)
        reloaded.replace_notices([news(), news("second"), news("third")], checked_at=self.now)
        self.assertEqual(Store(self.path, self.source).unread_count, 1)

    def test_language_change_translates_cached_news_without_changing_read_state(self):
        center = self.make_center(notices=[news()])
        center.mark_all_read()
        before = self.path.read_bytes()
        for russian in (True, False, True):
            self.app.russian = russian
            center.render()
            self.assertEqual(center.title.get_text(), "Новости и обновления" if russian else "News and updates")
            self.assertIn("От разработчика" if russian else "From the developer", self.labels(center.rows))
            self.assertIn("Новости доступны без сети." if russian else "Saved news remains available offline.",
                          self.labels(center.rows))
            self.assertEqual(center.store.unread_count, 0)
            self.assertEqual(self.path.read_bytes(), before)
        self.fetcher.assert_not_called()

    def test_links_open_only_after_explicit_click_and_never_mark_read_automatically(self):
        url = "https://github.com/tanosx/Panelyra/discussions/1"
        with patch.object(Gtk, "show_uri_on_window") as browser:
            center = self.make_center(notices=[news(url=url)], checked_at=self.now)
            center.start()
            center.render()
            browser.assert_not_called()
            link = next(item for item in descendants(center.rows) if isinstance(item, Gtk.Button))
            link.clicked()
            browser.assert_called_once()
            self.assertIs(browser.call_args.args[0], self.app.window)
            self.assertEqual(browser.call_args.args[1], url)
        self.assertEqual(center.store.unread_count, 1)

    def test_browser_failure_is_visible_and_preserves_news(self):
        center = self.make_center(notices=[news(url="https://github.com/tanosx/Panelyra/discussions/1")])
        link = next(item for item in descendants(center.rows) if isinstance(item, Gtk.Button))
        with patch.object(Gtk, "show_uri_on_window", side_effect=GLib.Error("No browser available")):
            link.clicked()
        self.assertEqual(center.error, "open")
        self.assertIn("Could not open", center.status.get_text())
        self.assertEqual(center.store.unread_count, 1)

    def test_release_card_exposes_the_release_page_and_translates_its_action(self):
        release = Notice("release:0.4.0", "release", {"en": "Panelyra 0.4.0"},
                         {"en": "An update is available.", "ru": "Доступно обновление."},
                         "2026-10-06", url="https://github.com/tanosx/Panelyra/releases/tag/v0.4.0",
                         version="0.4.0")
        center = self.make_center(notices=[release])
        for russian in (False, True):
            self.app.russian = russian
            center.render()
            self.assertIn("Panelyra 0.4.0", self.labels(center.rows))
            self.assertTrue(any(text.startswith("Новая версия" if russian else "New version")
                                for text in self.labels(center.rows)))
            link = next(item for item in descendants(center.rows) if isinstance(item, Gtk.Button))
            self.assertEqual(link.get_label(), "Открыть страницу версии" if russian else "Open release page")

    def test_failed_persistence_reports_error_without_claiming_read_or_saved_preferences(self):
        center = self.make_center(notices=[news()], checked_at=self.now)
        before = self.path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("Disk is full")):
            center.read_button.clicked()
            self.assertEqual(center.error, "storage")
            self.assertEqual(center.store.unread_count, 1)
            self.assertTrue(center.counter.get_visible())
            center.automatic.set_active(False)
            self.assertEqual(center.error, "storage")
            self.assertTrue(center.store.auto_check)
            self.assertTrue(center.automatic.get_active())
            center.on_result([news("unsaved")], None)
            self.assertEqual(center.error, "storage")
            self.assertEqual(center.store.notices, [news()])
            self.assertIn("Could not save", center.status.get_text())
        self.assertEqual(self.path.read_bytes(), before)


@unittest.skipIf(Gtk is None, "GTK 3 is not installed")
class NotificationLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = get_test_application()

    def test_launcher_hosts_translates_starts_and_closes_the_same_notification_center(self):
        with tempfile.TemporaryDirectory(prefix="panelyra-news-integration-") as temporary:
            source = Source("")
            store = Store(Path(temporary) / "notifications.json", source)
            fetcher = Mock(side_effect=AssertionError("Unexpected network access"))
            def factory(app):
                center = notifications_ui.NotificationCenter(
                    app, source=source, store=store, fetcher=fetcher)
                center.start = Mock(wraps=center.start)
                return center
            window_type = Gtk.ApplicationWindow
            with patch.object(launcher, "load", return_value=(Settings(language="en"), None)), \
                    patch.object(launcher, "save"), \
                    patch.object(launcher, "NotificationCenter", side_effect=factory), \
                    patch.object(Gtk, "ApplicationWindow", side_effect=lambda **kwargs:
                                 window_type(**{**kwargs, "application": self.host})):
                app = launcher.Launcher(auto_connect=False)
                # Activation starts news only once, without starting discovery
                # or a stream. The real device timer is removed by on_close.
                with patch.object(app, "refresh_devices") as devices:
                    app.do_activate()
                    app.do_activate()
                devices.assert_called_once()
                self.addCleanup(app.window.destroy)
                self.addCleanup(app.notifications.close)
                center = app.notifications
                center.start.assert_called_once()
                self.assertIn(center.button, list(descendants(app.window.get_titlebar())))
                app.language.set_active_id("ru")
                self.assertIs(app.notifications, center)
                self.assertEqual(center.title.get_text(), "Новости и обновления")
                self.assertIn("Запросов в интернет нет", center.privacy.get_text())
                with patch.object(app, "quit") as quit_app:
                    app.on_close()
                quit_app.assert_called_once()
                self.assertTrue(center.closed)
                fetcher.assert_not_called()


if __name__ == "__main__":
    unittest.main()

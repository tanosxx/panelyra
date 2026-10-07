"""Notification checks must remain bounded, read-only, and safe to cache."""

from dataclasses import asdict, replace
from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from usbdisplay import notifications as news


SOURCE = news.Source("TanosX/Panelyra")


def item(identifier="first", **changes):
    value = {"id": identifier, "published_at": "2026-10-06",
             "title": {"en": "Welcome", "ru": "Добро пожаловать"},
             "body": {"en": "A message from the developer."},
             "url": "https://github.com/TanosX/Panelyra/discussions/1"}
    value.update(changes)
    return value


def feed(*items):
    return {"schema_version": 1, "items": list(items)}


def release(**changes):
    value = {"tag_name": "v0.4.0", "draft": False, "prerelease": False,
             "published_at": "2026-10-06T12:15:00Z",
             "html_url": "https://github.com/TanosX/Panelyra/releases/tag/v0.4.0"}
    value.update(changes)
    return value


class FeedTests(unittest.TestCase):
    def test_localized_plain_text_and_english_fallback(self):
        value = item(title={"en": "<b>New</b> &amp; good", "ru": "Новое"},
                     body={"en": "<script>bad()</script>Hello<p>world</p>"})
        notice, = news.parse_announcements(feed(value), SOURCE)
        self.assertEqual(notice.id, "news:first")
        self.assertEqual(notice.text("title", "fr"), "New & good")
        self.assertEqual(notice.text("title", "ru"), "Новое")
        self.assertEqual(notice.text("body", "ru"), "Hello\nworld")
        with self.assertRaises(ValueError):
            notice.text("url")

    def test_schema_ids_text_and_real_dates_are_validated(self):
        invalid = [None, [], {}, {"schema_version": True, "items": []},
                   {"schema_version": 2, "items": []}, feed(item(), item()),
                   feed(item(id="../x")), feed(item(id="x" * 81)),
                   feed(item(published_at="2026-02-30")), feed(item(published_at="2026-1-01")),
                   feed(item(title={"ru": "Текст"})), feed(item(title={"en": "x" * 141})),
                   feed(item(body={"en": "x" * 1601})), feed(item(body={"en": "<script>x</script>"})),
                   feed(item(title={"en": True})), feed(item(title={"en": "ok", "bad": "no"})),
                   feed(item(title={"en": "hello\x00world"})), feed(item(title={"en": "\ud800"})),
                   feed(*[item(str(index)) for index in range(51)])]
        for data in invalid:
            with self.subTest(data=str(data)[:90]), self.assertRaises(news.NotificationError):
                news.parse_announcements(data, SOURCE)

    def test_only_links_within_repository_are_allowed(self):
        invalid = ["http://github.com/TanosX/Panelyra/issues/1", "javascript:alert(1)",
                   "https://evil.example/TanosX/Panelyra/a", "https://github.com/other/repo/a",
                   "https://github.com/TanosX/Panelyra-extra/a", "https://github.com@evil.example/a",
                   "https://user@github.com/TanosX/Panelyra/a", "https://github.com:123/TanosX/Panelyra/a",
                   "https://github.com/TanosX/Panelyra/../Other/a",
                   "https://github.com/TanosX/Panelyra/%2e%2e/Other/a",
                   "https://github.com/TanosX/Panelyra/%252e%252e/Other/a",
                   "https://github.com/TanosX/Panelyra/\\evil", "https://github.com/TanosX/Panelyra/\na"]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(news.NotificationError):
                news.parse_announcements(feed(item(url=url)), SOURCE)
        self.assertIsNone(news.parse_announcements(feed(item(url=None)), SOURCE)[0].url)

    def test_feed_sorted_newest_first(self):
        result = news.parse_announcements(feed(item("old", published_at="2025-01-01"), item("new")), SOURCE)
        self.assertEqual([value.id for value in result], ["news:new", "news:old"])


class FetchTests(unittest.TestCase):
    def test_unconfigured_source_does_not_access_network(self):
        with patch.object(news, "_read_json") as read:
            self.assertEqual(news.fetch_notices(news.Source(""), "0.3.0"), [])
            read.assert_not_called()

    def test_latest_release_and_news_are_combined(self):
        with patch.object(news, "_read_json", side_effect=[release(), feed(item())]) as read:
            notices = news.fetch_notices(SOURCE, "0.3.0")
        self.assertEqual([notice.kind for notice in notices], ["release", "news"])
        self.assertEqual(notices[0].version, "0.4.0")
        self.assertEqual(notices[0].published_at, "2026-10-06")
        self.assertEqual(read.call_args_list[0].args, ("https://api.github.com/repos/TanosX/Panelyra/releases/latest",))
        self.assertEqual(read.call_args_list[1].args, ("https://raw.githubusercontent.com/TanosX/Panelyra/main/announcements.json",))

    def test_version_comparison_ignores_old_current_and_prereleases(self):
        values = [release(tag_name="v0.2.9"), release(tag_name="v0.3.0+different"),
                  release(tag_name="v0.4.0-rc.1"), release(tag_name="v00.4.0"),
                  release(tag_name="latest"), release(draft=True), release(prerelease=True)]
        for value in values:
            with self.subTest(value=value), patch.object(news, "_read_json", side_effect=[value, feed()]):
                self.assertEqual(news.fetch_notices(SOURCE, "0.3.0+build.1"), [])
        with patch.object(news, "_read_json", side_effect=[release(tag_name="v0.10.0+stable"), feed()]):
            self.assertEqual(news.fetch_notices(SOURCE, "0.9.0")[0].version, "0.10.0+stable")

    def test_release_links_must_be_release_pages_not_other_github_paths(self):
        for url in (None, "https://github.com/TanosX/Panelyra/issues/1", "https://github.com/Other/Repo/releases/v1"):
            with self.subTest(url=url), patch.object(news, "_read_json", return_value=release(html_url=url)):
                with self.assertRaises(news.NotificationError):
                    news.fetch_notices(SOURCE, "0.3.0")

    def test_failure_does_not_return_partial_release_result(self):
        with patch.object(news, "_read_json", side_effect=[release(), URLError("offline")]):
            with self.assertRaises(URLError):
                news.fetch_notices(SOURCE, "0.3.0")

    def test_missing_release_is_normal_but_missing_feed_is_not_an_empty_refresh(self):
        with patch.object(news, "_read_json", side_effect=[None, feed(item())]):
            self.assertEqual(news.fetch_notices(SOURCE, "0.3.0")[0].kind, "news")
        with patch.object(news, "_read_json", side_effect=[None, None]):
            with self.assertRaisesRegex(news.NotificationError, "feed was not found"):
                news.fetch_notices(SOURCE, "0.3.0")

    def test_combined_results_bounded_and_release_reserved(self):
        data = feed(*[item(str(index)) for index in range(50)])
        with patch.object(news, "_read_json", side_effect=[release(), data]):
            result = news.fetch_notices(SOURCE, "0.3.0")
        self.assertEqual(len(result), 50)
        self.assertEqual(result[0].kind, "release")

    def response(self, payload, url="https://api.github.com/repos/TanosX/Panelyra/releases/latest"):
        response = Mock()
        response.geturl.return_value = url
        response.read.return_value = payload
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_request_has_finite_timeout_size_and_no_user_or_auth_headers(self):
        response = self.response(b'{"ok": true}')
        opener = Mock()
        opener.open.return_value = response
        with patch.object(news, "build_opener", return_value=opener):
            result = news._read_json(response.geturl())
        self.assertEqual(result, {"ok": True})
        request = opener.open.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["user-agent"], "Panelyra-Update-Check")
        self.assertEqual(set(headers), {"accept", "user-agent", "x-github-api-version"})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 8)
        response.read.assert_called_once_with(news.MAX_BYTES + 1)

    def test_404_empty_but_rate_limits_and_server_errors_propagate(self):
        url = "https://api.github.com/repos/TanosX/Panelyra/releases/latest"
        for code in (404, 403, 429, 500):
            error = HTTPError(url, code, "failure", {}, BytesIO())
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(code=code), patch.object(news, "build_opener", return_value=opener):
                if code == 404:
                    self.assertIsNone(news._read_json(url))
                else:
                    with self.assertRaises(HTTPError):
                        news._read_json(url)

    def test_invalid_json_oversize_and_final_redirect_fail(self):
        for payload in (b"broken", b"\xff", b"null", b"[]", b" " * (news.MAX_BYTES + 1)):
            response = self.response(payload)
            opener = Mock()
            opener.open.return_value = response
            with self.subTest(payload=payload[:20]), patch.object(news, "build_opener", return_value=opener):
                with self.assertRaises(news.NotificationError):
                    news._read_json(response.geturl())
        response = self.response(b"{}", "https://evil.example/payload")
        opener.open.return_value = response
        with patch.object(news, "build_opener", return_value=opener), self.assertRaises(news.NotificationError):
            news._read_json("https://api.github.com/something")

    def test_redirect_rejects_other_origin_and_plain_http(self):
        redirect = news._SameOriginRedirect()
        request = Request("https://api.github.com/old")
        for target in ("https://evil.example/new", "http://api.github.com/new", "https://user@api.github.com/new"):
            with self.subTest(target=target), self.assertRaises(news.NotificationError):
                redirect.redirect_request(request, None, 302, "Found", {}, target)
        allowed = redirect.redirect_request(request, None, 302, "Found", {}, "https://api.github.com/new")
        self.assertEqual(allowed.full_url, "https://api.github.com/new")


class StoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="panelyra-notifications-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config/notifications.json"
        self.notices = news.parse_announcements(feed(item()), SOURCE)

    def test_defaults_read_only_and_round_trip_private_state(self):
        store = news.Store(self.path, SOURCE)
        self.assertFalse(self.path.exists())
        self.assertEqual(store.unread_count, 0)
        self.assertTrue(store.auto_check)
        self.assertEqual(store.last_checked, 0)
        self.assertIsNone(store.load_error)
        store.replace_notices(self.notices, checked_at=123)
        self.assertEqual(store.unread_count, 1)
        store.mark_all_read()
        store.set_auto_check(False)
        loaded = news.Store(self.path, SOURCE)
        self.assertEqual(loaded.notices, self.notices)
        self.assertEqual(loaded.read_ids, {"news:first"})
        self.assertEqual(loaded.unread_count, 0)
        self.assertFalse(loaded.auto_check)
        self.assertEqual(loaded.last_checked, 123)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_source_changes_do_not_inherit_old_cache_or_read_status(self):
        store = news.Store(self.path, SOURCE)
        store.replace_notices(self.notices, checked_at=123)
        store.mark_all_read()
        store.set_auto_check(False)
        before = self.path.read_bytes()
        for source in (news.Source("Other/Repo"), news.Source("TanosX/Panelyra", "other"), news.Source("")):
            loaded = news.Store(self.path, source)
            self.assertEqual(loaded.notices, [])
            self.assertEqual(loaded.read_ids, set())
            self.assertTrue(loaded.auto_check)
            self.assertIsNone(loaded.load_error)
        self.assertEqual(self.path.read_bytes(), before)

    def test_read_status_survives_refresh_but_new_item_is_unread(self):
        store = news.Store(self.path, SOURCE)
        store.replace_notices(self.notices, checked_at=123)
        store.mark_all_read()
        store.replace_notices(news.parse_announcements(feed(item(), item("second")), SOURCE), checked_at=124)
        self.assertEqual(store.unread_count, 1)
        self.assertEqual(store.read_ids, {"news:first"})
        store.replace_notices([], checked_at=125)
        self.assertEqual(store.read_ids, set())

    def test_upgrade_filters_already_installed_releases_without_writing_cache(self):
        releases = [news._release(release(tag_name=f"v{version}"), SOURCE, (0, 2, 0))
                    for version in ("0.3.0", "0.4.0", "0.5.0")]
        previous = news.Store(self.path, SOURCE, current_version="0.2.0")
        previous.replace_notices(releases + self.notices, checked_at=123)
        previous.mark_all_read()
        previous.set_auto_check(False)
        original = self.path.read_bytes()

        upgraded = news.Store(self.path, SOURCE, current_version="0.4.0+build.1")
        self.assertIsNone(upgraded.load_error)
        self.assertEqual([notice.id for notice in upgraded.notices], ["release:v0.5.0", "news:first"])
        self.assertEqual(upgraded.read_ids, {"release:v0.5.0", "news:first"})
        self.assertEqual(upgraded.unread_count, 0)
        self.assertFalse(upgraded.auto_check)
        self.assertEqual(upgraded.last_checked, 123)
        self.assertEqual(self.path.read_bytes(), original)

    def test_replacing_notices_also_discards_installed_releases(self):
        current = news._release(release(tag_name="v0.3.0"), SOURCE, (0, 2, 0))
        newer = news._release(release(tag_name="v0.4.0"), SOURCE, (0, 2, 0))
        store = news.Store(self.path, SOURCE, current_version="0.3.0")
        store.replace_notices([current, newer] + self.notices, checked_at=123)
        self.assertEqual([notice.id for notice in store.notices], ["release:v0.4.0", "news:first"])
        self.assertEqual(store.unread_count, 2)
        persisted = json.loads(self.path.read_text())
        self.assertEqual([notice["id"] for notice in persisted["notices"]], ["release:v0.4.0", "news:first"])

    def test_write_failures_preserve_previous_memory_and_file(self):
        store = news.Store(self.path, SOURCE)
        store.replace_notices(self.notices, checked_at=123)
        original = self.path.read_bytes()
        actions = [store.mark_all_read, lambda: store.set_auto_check(False),
                   lambda: store.replace_notices([], checked_at=124), store.save]
        for action in actions:
            with self.subTest(action=action), patch.object(Path, "replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    action()
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(store.notices, self.notices)
            self.assertEqual(store.read_ids, set())
            self.assertTrue(store.auto_check)
            self.assertEqual(store.last_checked, 123)
            self.assertEqual(list(self.path.parent.glob(".notifications-*")), [])

    def test_corrupt_cache_preserved_and_not_partially_loaded(self):
        store = news.Store(self.path, SOURCE)
        store.replace_notices(self.notices, checked_at=123)
        valid = json.loads(self.path.read_text())
        corruptions = ["{", "[]", "null", " " * (news.MAX_BYTES + 1)]
        for changes in ({"read_ids": ["unknown"]}, {"auto_check": 1}, {"last_checked": float("nan")},
                        {"last_checked": -1}, {"last_checked": 1e300}, {"last_checked": 10 ** 400},
                        {"notices": [None]}, {"notices": [{"kind": "bad"}]},
                        {"notices": [asdict(replace(self.notices[0], url="https://evil.example"))]}):
            corruptions.append(json.dumps(dict(valid, **changes)))
        for text in corruptions:
            with self.subTest(text=text[:100]):
                self.path.write_text(text)
                loaded = news.Store(self.path, SOURCE)
                self.assertTrue(loaded.load_error)
                self.assertEqual(loaded.notices, [])
                self.assertTrue(loaded.auto_check)
                self.assertEqual(loaded.last_checked, 0)
                self.assertEqual(self.path.read_text(), text)

    def test_validation_failure_does_not_write_or_mutate(self):
        store = news.Store(self.path, SOURCE)
        store.replace_notices(self.notices, checked_at=123)
        before = self.path.read_bytes()
        for changes in ({"url": "https://evil.example"}, {"id": "bad"}, {"kind": "script"}):
            with self.subTest(changes=changes), self.assertRaises(news.NotificationError):
                store.replace_notices([replace(self.notices[0], **changes)], checked_at=124)
        with self.assertRaises(news.NotificationError):
            store.set_auto_check("yes")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(store.notices, self.notices)

    def test_load_source_reads_bundled_config_only(self):
        with patch.object(news.notification_source, "GITHUB_REPOSITORY", "Other/Repo"), \
                patch.object(news.notification_source, "NEWS_BRANCH", "stable/news"):
            self.assertEqual(news.load_source(), news.Source("Other/Repo", "stable/news"))
        for repository in ("../bad", "https://github.com/a/b", "a/b/c", "a/b?x"):
            with self.subTest(repository=repository), self.assertRaises(news.NotificationError):
                news.Source(repository)
        with self.assertRaises(news.NotificationError):
            news.Source("Other/Repo", "../main")


if __name__ == "__main__":
    unittest.main()

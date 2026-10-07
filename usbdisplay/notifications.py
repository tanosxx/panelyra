"""Read-only release/news checks and private, source-scoped notification state.

Network access happens only in ``fetch_notices``. Remote text is displayed as
plain text and can link only to the configured public GitHub repository.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__, notification_source


MAX_BYTES = 256 * 1024
MAX_NOTICES = 50
TIMEOUT = 8
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
_VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z")
_NEWS_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_LANGUAGE = re.compile(r"[a-z]{2}(?:-[A-Z]{2})?\Z")


class NotificationError(ValueError):
    """The configured source or its response cannot be safely used."""


@dataclass(frozen=True)
class Source:
    repository: str
    branch: str = "main"

    def __post_init__(self):
        if not isinstance(self.repository, str) or (self.repository and not _REPOSITORY.fullmatch(self.repository)):
            raise NotificationError("Expected a GitHub owner/repository")
        if (not isinstance(self.branch, str) or not self.branch or len(self.branch) > 160
                or not re.fullmatch(r"[A-Za-z0-9_./-]+", self.branch)
                or any(part in ("", ".", "..") for part in self.branch.split("/"))):
            raise NotificationError("Invalid news branch")

    @property
    def configured(self):
        return bool(self.repository)

    @property
    def key(self):
        return f"{self.repository.lower()}@{self.branch}" if self.configured else ""


def load_source():
    return Source(notification_source.GITHUB_REPOSITORY, notification_source.NEWS_BRANCH)


@dataclass(frozen=True)
class Notice:
    id: str
    kind: str
    title: dict[str, str]
    body: dict[str, str]
    published_at: str
    url: str | None = None
    version: str | None = None

    def text(self, field, language="en"):
        if field not in ("title", "body"):
            raise ValueError("Expected title or body")
        translations = getattr(self, field)
        return translations.get(language) or translations.get("en", "")


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag in ("br", "p", "div", "li") and not self.hidden:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value):
    parser = _PlainText()
    parser.feed(unescape(value))
    parser.close()
    text = "".join(parser.parts)
    return "\n".join(" ".join(line.split()) for line in text.splitlines()
                     if line.strip()).strip()


def _translations(value, limit):
    if not isinstance(value, dict) or not 1 <= len(value) <= 8 or "en" not in value:
        raise NotificationError("Text must include English and at most eight languages")
    result = {}
    for language, text in value.items():
        if (not isinstance(language, str) or not _LANGUAGE.fullmatch(language)
                or not isinstance(text, str) or not 1 <= len(text) <= limit):
            raise NotificationError("Invalid localized text or length")
        try:
            text.encode("utf-8")
        except UnicodeError as error:
            raise NotificationError("Invalid Unicode in notification text") from error
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise NotificationError("Control characters are not allowed in notification text")
        clean = _plain(text)
        if not clean:
            raise NotificationError("Notification text cannot be empty")
        result[language] = clean
    return result


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise NotificationError("Expected a YYYY-MM-DD date")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise NotificationError("Invalid publication date") from error
    return value


def _version(value):
    match = _VERSION.fullmatch(value) if isinstance(value, str) and len(value) <= 100 else None
    return tuple(int(part) for part in match.groups()) if match else None


def _url(value, source, *, release=False):
    if value is None:
        return None
    if not source.configured or not isinstance(value, str) or len(value) > 2048:
        raise NotificationError("Invalid notification link")
    # Reject ambiguous paths that browsers may normalize before navigation.
    if any(ord(char) < 33 or ord(char) == 127 for char in value) or "\\" in value:
        raise NotificationError("Invalid notification link")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise NotificationError("Invalid notification link") from error
    path = unquote(parsed.path)
    prefix = f"/{source.repository}/" + ("releases/" if release else "")
    if (parsed.scheme != "https" or parsed.hostname != "github.com"
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or not path.lower().startswith(prefix.lower())
            or any(part in (".", "..") for part in path.split("/"))
            or "\\" in path or "%" in path):
        raise NotificationError("Notification links must stay inside the GitHub repository")
    return value


def parse_announcements(data, source):
    """Validate an already-decoded schema-v1 feed, returning plain-text notices."""
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1 or not isinstance(data.get("items"), list)
            or len(data["items"]) > MAX_NOTICES):
        raise NotificationError("Expected a schema-v1 feed with at most 50 items")
    notices, seen = [], set()
    for item in data["items"]:
        if not isinstance(item, dict):
            raise NotificationError("Each announcement must be an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not _NEWS_ID.fullmatch(identifier) or identifier in seen:
            raise NotificationError("Announcement IDs must be unique and at most 80 characters")
        seen.add(identifier)
        notices.append(Notice(
            id=f"news:{identifier}", kind="news",
            title=_translations(item.get("title"), 140),
            body=_translations(item.get("body"), 1600),
            published_at=_date(item.get("published_at")),
            url=_url(item.get("url"), source),
        ))
    return sorted(notices, key=lambda notice: (notice.published_at, notice.id), reverse=True)


def _origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or 443


class _SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (target.username is not None or target.password is not None
                or _origin(req.full_url) != _origin(newurl)):
            raise NotificationError("Cross-origin notification redirect rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_json(url):
    request = Request(url, headers={
        "Accept": "application/vnd.github+json" if urlsplit(url).hostname == "api.github.com" else "application/json",
        "User-Agent": "Panelyra-Update-Check",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    opener = build_opener(_SameOriginRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            if _origin(response.geturl()) != _origin(url):
                raise NotificationError("Cross-origin notification response rejected")
            payload = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        error.close()
        if error.code == 404:
            return None
        raise
    if len(payload) > MAX_BYTES:
        raise NotificationError("Notification response exceeds 256 KiB")
    try:
        result = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise NotificationError("Invalid notification JSON") from error
    if not isinstance(result, dict):
        raise NotificationError("Notification JSON must be an object")
    return result


def _release(data, source, current_version):
    if data is None:
        return None
    if not isinstance(data, dict):
        raise NotificationError("Invalid release response")
    if data.get("draft") is not False or data.get("prerelease") is not False:
        return None
    tag = data.get("tag_name")
    version = _version(tag)
    if version is None or version <= current_version:
        return None
    link = _url(data.get("html_url"), source, release=True)
    if link is None:
        raise NotificationError("Release is missing its GitHub link")
    published = data.get("published_at")
    try:
        if not isinstance(published, str) or len(published) > 40:
            raise ValueError("Missing publication date")
        published = datetime.fromisoformat(published.replace("Z", "+00:00")).date().isoformat()
    except ValueError as error:
        raise NotificationError("Invalid release publication date") from error
    visible_version = tag.removeprefix("v")
    return Notice(
        id=f"release:{tag}", kind="release", version=visible_version,
        title={"en": f"Panelyra {visible_version} is available", "ru": f"Доступна Panelyra {visible_version}"},
        body={"en": "A new stable version is available. Open the release page to read the changes and download it.",
              "ru": "Вышла новая стабильная версия. На странице релиза можно прочитать об изменениях и скачать её."},
        published_at=published, url=link,
    )


def fetch_notices(source, current_version):
    if not source.configured:
        return []
    current = _version(current_version)
    if current is None:
        raise NotificationError("Current application version must be stable x.y.z")
    data = _read_json(f"https://api.github.com/repos/{source.repository}/releases/latest")
    release = _release(data, source, current)
    feed = _read_json(f"https://raw.githubusercontent.com/{source.repository}/{quote(source.branch, safe='/')}/announcements.json")
    if feed is None:
        raise NotificationError("The configured announcements feed was not found")
    news = parse_announcements(feed, source)
    # Reserve a visible place for a release even if the feed has fifty items.
    return ([release] + news[:MAX_NOTICES - 1]) if release else news


def config_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "panelyra" / "notifications.json"


def _checked_at(value):
    if type(value) not in (int, float) or not 0 <= value <= 253402300799:
        raise NotificationError("Invalid notification check time")
    return float(value)


def _validate_notices(notices, source):
    if not isinstance(notices, list) or len(notices) > MAX_NOTICES:
        raise NotificationError("Expected at most 50 notifications")
    result, seen = [], set()
    for notice in notices:
        if not isinstance(notice, Notice) or not isinstance(notice.id, str) or notice.id in seen:
            raise NotificationError("Invalid or duplicate cached notification")
        seen.add(notice.id)
        if notice.kind == "news":
            if not notice.id.startswith("news:") or not _NEWS_ID.fullmatch(notice.id[5:]) or notice.version is not None:
                raise NotificationError("Invalid news notification")
        elif notice.kind == "release":
            if (not notice.id.startswith("release:") or _version(notice.id[8:]) is None
                    or _version(notice.version) != _version(notice.id[8:]) or notice.url is None):
                raise NotificationError("Invalid release notification")
        else:
            raise NotificationError("Unknown notification kind")
        result.append(Notice(notice.id, notice.kind, _translations(notice.title, 140),
                             _translations(notice.body, 1600), _date(notice.published_at),
                             _url(notice.url, source, release=notice.kind == "release"), notice.version))
    return result


class Store:
    def __init__(self, path=None, source=None, *, current_version=None):
        self.path = Path(path) if path is not None else config_path()
        self.source = source if source is not None else load_source()
        self._installed_version = _version(__version__ if current_version is None else current_version)
        if self._installed_version is None:
            raise NotificationError("Current application version must be stable x.y.z")
        self.notices = []
        self.read_ids = set()
        self.auto_check = True
        self.last_checked = 0.0
        self.load_error = None
        try:
            with self.path.open("rb") as stream:
                content = stream.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise NotificationError("Notification cache exceeds 256 KiB")
            data = json.loads(content.decode("utf-8"))
            if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
                raise NotificationError("Invalid notification cache schema")
            if data.get("source") != self.source.key:
                return
            if not isinstance(data.get("notices"), list) or len(data["notices"]) > MAX_NOTICES:
                raise NotificationError("Invalid cached notifications")
            notices = _validate_notices([Notice(**item) for item in data["notices"]], self.source)
            read_ids = data.get("read_ids")
            known = {notice.id for notice in notices}
            if (not isinstance(read_ids, list) or len(read_ids) > MAX_NOTICES
                    or any(not isinstance(identifier, str) or identifier not in known for identifier in read_ids)):
                raise NotificationError("Invalid read notification IDs")
            if type(data.get("auto_check")) is not bool:
                raise NotificationError("Invalid notification preference")
            checked = _checked_at(data.get("last_checked"))
            self.notices = self._newer_releases(notices)
            self.read_ids = set(read_ids) & {notice.id for notice in self.notices}
            self.auto_check, self.last_checked = data["auto_check"], checked
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, RecursionError) as error:
            self.load_error = str(error)

    @property
    def unread_count(self):
        return sum(notice.id not in self.read_ids for notice in self.notices)

    def _newer_releases(self, notices):
        # An application upgrade can happen while the previous cache is still
        # fresh. Never keep advertising a release that is already installed.
        return [notice for notice in notices if notice.kind != "release"
                or _version(notice.version) > self._installed_version]

    def _write(self, notices, read_ids, auto_check, last_checked):
        notices = _validate_notices(notices, self.source)
        if type(auto_check) is not bool:
            raise NotificationError("Invalid notification preference")
        if not isinstance(read_ids, set) or not read_ids <= {notice.id for notice in notices}:
            raise NotificationError("Invalid read notification IDs")
        notices = self._newer_releases(notices)
        read_ids = read_ids & {notice.id for notice in notices}
        last_checked = _checked_at(last_checked)
        data = {"schema_version": 1, "source": self.source.key,
                "notices": [asdict(notice) for notice in notices], "read_ids": sorted(read_ids),
                "auto_check": auto_check, "last_checked": last_checked}
        content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(content) > MAX_BYTES:
            raise NotificationError("Notification cache exceeds 256 KiB")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("wb", dir=self.path.parent, prefix=".notifications-", delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.notices, self.read_ids = notices, set(read_ids)
        self.auto_check, self.last_checked = auto_check, last_checked
        self.load_error = None

    def save(self):
        self._write(self.notices, self.read_ids, self.auto_check, self.last_checked)

    def replace_notices(self, notices, checked_at=None):
        notices = _validate_notices(notices, self.source)
        retained = self.read_ids & {notice.id for notice in notices}
        self._write(notices, retained, self.auto_check, time.time() if checked_at is None else checked_at)

    def mark_all_read(self):
        self._write(self.notices, {notice.id for notice in self.notices}, self.auto_check, self.last_checked)

    def set_auto_check(self, enabled):
        self._write(self.notices, self.read_ids, enabled, self.last_checked)

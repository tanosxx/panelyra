"""GTK notification bell; all network work stays off the desktop thread."""

import threading
import time
from pathlib import Path

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk, Pango

from . import __version__
from .notifications import Store, fetch_notices, load_source

CHECK_INTERVAL = 6 * 60 * 60
RETRY_INTERVAL = 15 * 60


class NotificationCenter:
    def __init__(self, app, *, source=None, store=None, fetcher=None, clock=None):
        self.app = app
        self.source = source if source is not None else load_source()
        self.store = store if store is not None else Store(source=self.source)
        self.fetcher = fetcher if fetcher is not None else fetch_notices
        self.clock = clock if clock is not None else time.time
        self.timer = None
        self.loading = self.closed = self.rendering = False
        self.last_attempt = None
        self.error = 'load' if self.store.load_error else None
        self.button = Gtk.MenuButton()
        box = Gtk.Box(spacing=6)
        icon = Path(__file__).resolve().parent / 'assets/panelyra-bell.svg'
        box.pack_start(Gtk.Image.new_from_file(str(icon)), False, False, 0)
        self.counter = Gtk.Label()
        self.counter.get_style_context().add_class('notification-count')
        self.counter.set_no_show_all(True)
        box.pack_start(self.counter, False, False, 0)
        self.button.add(box)
        self.popover = Gtk.Popover(relative_to=self.button)
        self.popover.get_style_context().add_class('panelyra')
        self.popover.get_style_context().add_class('notification-popover')
        self.button.set_popover(self.popover)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_border_width(16)
        content.set_size_request(420, -1)
        self.popover.add(content)
        self.title = self.label('', 'section-title')
        content.pack_start(self.title, False, False, 0)
        self.status = self.label('', 'device-detail')
        content.pack_start(self.status, False, False, 0)
        self.scroll = scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(160)
        scroll.set_max_content_height(360)
        scroll.set_propagate_natural_height(True)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        scroll.add(self.rows)
        content.pack_start(scroll, True, True, 0)
        actions = Gtk.Box(spacing=8)
        self.refresh_button = Gtk.Button()
        self.refresh_button.connect('clicked', lambda _: self.refresh())
        self.read_button = Gtk.Button()
        self.read_button.connect('clicked', self.mark_all_read)
        actions.pack_start(self.refresh_button, True, True, 0)
        actions.pack_start(self.read_button, True, True, 0)
        content.pack_start(actions, False, False, 0)
        self.automatic = Gtk.CheckButton()
        self.automatic.connect('toggled', self.on_automatic)
        content.pack_start(self.automatic, False, False, 0)
        self.privacy = self.label('', 'tiny')
        content.pack_start(self.privacy, False, False, 0)
        self.render()
        content.show_all()

    def tr(self, english, russian):
        return self.app.tr(english, russian)

    @staticmethod
    def label(text, style=None):
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(44)
        if style:
            label.get_style_context().add_class(style)
        return label

    def render(self):
        if self.closed:
            return
        self.rendering = True
        try:
            count = self.store.unread_count
            self.counter.set_text(str(count) if count < 100 else '99+')
            self.counter.set_visible(bool(count))
            tooltip = self.tr(f'Notifications · {count} unread', f'Уведомления · непрочитанных: {count}')
            self.button.set_tooltip_text(tooltip)
            self.button.get_accessible().set_name(tooltip)
            self.title.set_text(self.tr('News and updates', 'Новости и обновления'))
            if not self.source.configured:
                status = self.tr('News will be available after the project is published.',
                                 'Новости появятся после публикации проекта.')
            elif self.loading:
                status = self.tr('Checking for news…', 'Проверяем новости…')
            elif self.error == 'network':
                status = self.tr('Could not check for news. Saved messages are still available.',
                                 'Не удалось проверить новости. Сохранённые сообщения доступны.')
            elif self.error == 'storage':
                status = self.tr('Could not save notification preferences. Please try again.',
                                 'Не удалось сохранить настройки уведомлений. Попробуйте ещё раз.')
            elif self.error == 'load':
                status = self.tr('Could not read saved notifications. Check again to reload them.',
                                 'Не удалось прочитать сохранённые уведомления. Проверьте новости ещё раз.')
            elif self.error == 'open':
                status = self.tr('Could not open the link in your browser.',
                                 'Не удалось открыть ссылку в браузере.')
            elif self.store.last_checked:
                try:
                    checked = time.strftime('%d.%m.%Y %H:%M', time.localtime(self.store.last_checked))
                except (OverflowError, OSError, ValueError):
                    checked = self.tr('unknown', 'неизвестно')
                status = self.tr(f'Last checked: {checked}', f'Проверено: {checked}')
            else:
                status = self.tr('New versions and news from the developer.',
                                 'Новые версии и новости от разработчика.')
            self.status.set_text(status)
            self.refresh_button.set_label(self.tr('Check now', 'Проверить'))
            self.refresh_button.set_sensitive(self.source.configured and not self.loading)
            self.read_button.set_label(self.tr('Mark all read', 'Прочитать всё'))
            self.read_button.set_sensitive(bool(count))
            self.automatic.set_label(self.tr('Check automatically', 'Проверять автоматически'))
            self.automatic.set_active(self.store.auto_check)
            self.automatic.set_sensitive(self.source.configured)
            self.privacy.set_text(self.tr(
                'Checks GitHub every 6 hours while Panelyra is open. Updates are installed by you.',
                'Проверяем GitHub раз в 6 часов, пока Panelyra открыта. Обновления устанавливаете вы.')
                if self.source.configured else self.tr(
                    'This build has no news source yet. No Internet requests are made.',
                    'В этой сборке источник новостей ещё не подключён. Запросов в интернет нет.'))
            for child in self.rows.get_children():
                child.destroy()
            self.scroll.set_min_content_height(320 if self.store.notices else 160)
            if not self.store.notices:
                empty = self.label(self.tr('No notifications yet', 'Уведомлений пока нет'), 'muted')
                empty.set_margin_top(24)
                empty.set_margin_bottom(24)
                self.rows.pack_start(empty, True, True, 0)
            language = 'ru' if self.app.russian else 'en'
            for notice in self.store.notices:
                row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
                row.get_style_context().add_class('notification-card')
                unread = notice.id not in self.store.read_ids
                if unread:
                    row.get_style_context().add_class('notification-unread')
                kind = self.tr('New version', 'Новая версия') if notice.kind == 'release' else self.tr('Developer news', 'Новости разработчика')
                if unread:
                    kind += self.tr(' · unread', ' · новое')
                row.pack_start(self.label(f'{kind} · {notice.published_at[:10]}', 'tiny'), False, False, 0)
                row.pack_start(self.label(notice.text('title', language), 'device-name'), False, False, 0)
                body = self.label(notice.text('body', language))
                row.pack_start(body, False, False, 0)
                if notice.url:
                    link = Gtk.Button(label=self.tr('Open release page', 'Открыть страницу версии')
                                      if notice.kind == 'release' else self.tr('Read more', 'Подробнее'))
                    link.set_halign(Gtk.Align.START)
                    link.connect('clicked', lambda _, url=notice.url: self.open_link(url))
                    row.pack_start(link, False, False, 0)
                self.rows.pack_start(row, False, False, 0)
            self.rows.show_all()
        finally:
            self.rendering = False

    def start(self):
        if self.closed or self.timer is not None or not self.source.configured:
            return
        self.tick()
        self.timer = GLib.timeout_add_seconds(60, self.tick)

    def tick(self):
        if self.closed:
            return False
        now = self.clock()
        elapsed = now - self.store.last_checked
        retry_due = self.last_attempt is None or now - self.last_attempt >= RETRY_INTERVAL
        if self.store.auto_check and retry_due and (not self.store.last_checked or elapsed >= CHECK_INTERVAL or elapsed < 0):
            self.refresh()
        return True

    def refresh(self):
        if self.closed or self.loading or not self.source.configured:
            return
        self.loading = True
        self.error = None
        self.last_attempt = self.clock()
        self.render()
        threading.Thread(target=self.fetch, daemon=True, name='panelyra-news').start()

    def fetch(self):
        try:
            notices = self.fetcher(self.source, __version__)
            error = None
        except (OSError, ValueError, RuntimeError):
            notices, error = None, 'network'
        GLib.idle_add(self.on_result, notices, error)

    def on_result(self, notices, error):
        if self.closed:
            return False
        self.loading = False
        self.error = error
        if error is None:
            try:
                self.store.replace_notices(notices, checked_at=self.clock())
            except (OSError, ValueError):
                self.error = 'storage'
        self.render()
        return False

    def mark_all_read(self, *_):
        try:
            self.store.mark_all_read()
            self.error = None
        except (OSError, ValueError):
            self.error = 'storage'
        self.render()

    def on_automatic(self, *_):
        if self.rendering or self.closed:
            return
        try:
            self.store.set_auto_check(self.automatic.get_active())
            self.error = None
        except (OSError, ValueError):
            self.error = 'storage'
        self.render()
        if self.store.auto_check:
            self.tick()

    def open_link(self, url):
        try:
            Gtk.show_uri_on_window(self.app.window, url, Gtk.get_current_event_time())
        except GLib.Error:
            self.error = 'open'
            self.render()

    def close(self):
        self.closed = True
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None

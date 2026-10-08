"""Panelyra desktop UI. GTK is loaded only by the optional GUI entry point."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from . import APP_ID, __version__
from . import runtime
from . import themes
from .appearance_ui import AppearancePanel
from .devices import discover_devices
from .download_ui import DownloadPanel
from .internet_ui import InternetPanel
from .language import resolve_language
from .notifications_ui import NotificationCenter
from .settings import PRESETS, Settings, load, save
from .window_geometry import WindowGeometry
from .theme_preview import DeviceIllustration

PROJECT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).resolve().parent / 'assets'


def running_stream():
    """Also recognise pre-Panelyra streams, without signalling those processes."""
    try:
        state = runtime.status()
        if state.get('running'):
            return state.get('pid', -1)
    except (RuntimeError, OSError):
        pass
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = (entry / 'cmdline').read_bytes().split(b'\0')
            if len(argv) >= 4 and argv[1:4] == [b'-m', b'usbdisplay', b'start']:
                return int(entry.name)
        except OSError:
            continue
    return None


CSS = b'''
.panelyra { background: #111927; color: #f5f7fc; }
.panelyra headerbar { background: #111927; border-bottom: 1px solid #29344a; color: #f5f7fc; }
.panelyra .headline { font-size: 24px; font-weight: 800; }
.panelyra .section-title { font-size: 16px; font-weight: 700; }
.panelyra .muted { color: #a5b1c6; }
.panelyra .tiny { font-size: 11px; }
.panelyra .eyebrow { font-size: 10px; font-weight: 700; letter-spacing: 2px; color: #69e4cb; }
.panelyra .hero { background: linear-gradient(125deg, #282946, #1b2c3b); border: 1px solid #3b4263; border-radius: 20px; }
.panelyra .card { background: #1a2435; border: 1px solid #2b374e; border-radius: 16px; }
.panelyra .badge { background: #233b3b; color: #83edd6; border-radius: 12px; padding: 5px 12px; font-weight: 600; }
.panelyra .badge.error { background: #452b35; color: #ffabb8; }
.panelyra .badge.busy { background: #383257; color: #c9c0ff; }
.panelyra button { background: #29364c; color: #f5f7fc; border: 1px solid #3b4860; border-radius: 9px; padding: 9px 15px; box-shadow: none; text-shadow: none; }
.panelyra button:hover { background: #354461; }
.panelyra button:disabled { opacity: .45; }
.panelyra button.primary { background: #746bff; border-color: #8a81ff; font-weight: 700; }
.panelyra button.primary:hover { background: #8b83ff; }
.panelyra button.stop { background: #42303e; border-color: #704456; }
.panelyra button.link { background: transparent; border-color: transparent; color: #a9a4ff; padding: 3px 0; }
.panelyra entry, .panelyra spinbutton { background: #111c2e; color: #f5f7fc; border-color: #39465e; border-radius: 8px; }
.panelyra spinbutton entry { min-width: 40px; padding: 6px; margin: 0; border: none; box-shadow: none; }
.panelyra spinbutton button { min-width: 26px; min-height: 30px; padding: 0; margin: 0; border-radius: 0; border-width: 0; border-left-width: 1px; box-shadow: none; }
.panelyra spinbutton button.up { border-radius: 0 7px 7px 0; }
.panelyra .device-name { font-size: 15px; font-weight: 700; }
.panelyra .device-detail { color: #a5b1c6; font-size: 12px; }
.panelyra button.internet-toggle { padding: 6px 10px; }
.panelyra button.compact { padding: 6px 10px; }
.panelyra .page-switcher button { background: transparent; border-color: transparent; padding: 9px 24px; color: #a5b1c6; }
.panelyra .page-switcher button:checked { background: #303552; border-color: #514d7b; color: #f5f7fc; }
.panelyra scrollbar { background: transparent; }
.panelyra scrollbar slider { background: #43506a; border: none; border-radius: 6px; min-width: 6px; min-height: 30px; }
.panelyra.notification-popover { background: #111927; border: 1px solid #39465e; }
.panelyra .notification-count { background: #746bff; color: #ffffff; border-radius: 9px; padding: 1px 5px; font-size: 11px; font-weight: 700; }
.panelyra .notification-card { background: #1a2435; border: 1px solid #2b374e; border-radius: 10px; padding: 10px; }
.panelyra .notification-unread { border-left: 3px solid #746bff; }
.panelyra combobox button { padding: 7px 10px; }
.panelyra textview, .panelyra textview text { background: #101826; color: #b8c7dc; font-family: monospace; font-size: 11px; }
.panelyra .step-number { background: #303552; color: #bfb7ff; border-radius: 10px; padding: 5px 10px; font-weight: 800; }
'''


class Launcher(Gtk.Application):
    def __init__(self, auto_connect=None, language=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.settings, self.settings_warning = load()
        if language is not None:
            self.settings = replace(self.settings, language=language)
        self.russian = self.uses_russian(self.settings.language)
        self.translations = []
        self.status_text = None
        self.status_kind = 'ready'
        self.auto_connect = self.settings.auto_connect if auto_connect is None else auto_connect
        self.window = self.process = None
        self.closing = self.stopped = self.updating = False
        self.force_timer = self.external_timer = None
        self.language_timer = None
        self.pending_language = None
        self.theme_timer = None
        self.pending_theme = None
        self.device_timer = None
        self.device_scan_running = False
        self.device_snapshot = None
        self.last_error = None
        self.lines = []
        self.controls = []
        self.visual_theme = self.settings.theme
        self.layout_signature = None
        self.layout_source = None
        self.previous_page = 'connection'

    def tr(self, english, russian):
        return russian if self.russian else english

    @staticmethod
    def uses_russian(language):
        return resolve_language(language) == 'ru'

    def bind_text(self, setter, text):
        if isinstance(text, tuple):
            self.translations.append((setter, text))
            text = self.tr(*text)
        setter(text)

    def localized(self, widget, prop, text):
        self.bind_text(lambda value: widget.set_property(prop, value), text)
        return widget

    def update_language(self):
        russian = self.uses_russian(self.settings.language)
        if self.russian == russian:
            return
        self.russian = russian
        previous = self.updating
        self.updating = True
        try:
            for setter, text in self.translations:
                setter(self.tr(*text))
            if self.status_text is not None:
                self.set_status(self.status_text, self.status_kind)
            if self.device_snapshot is not None:
                self.render_devices(*self.device_snapshot[:2])
            else:
                self.device_waiting.set_text(self.tr('Checking USB devices…', 'Проверяем USB-устройства…'))
            self.notifications.render()
            self.downloads.render()
            self.internet.render()
            self.appearance.render()
            self.update_overview()
            self.update_theme_header()
        finally:
            self.updating = previous

    def do_activate(self):
        if self.window is None:
            self.build_window()
            self.window.show_all()
            self.refresh_devices()
            self.device_timer = GLib.timeout_add_seconds(3, self.refresh_devices)
            self.notifications.start()
            if self.settings_warning:
                self.append_log('Preferences notice: ' + self.settings_warning)
            if self.auto_connect:
                self.start()
        self.window.present()

    @staticmethod
    def style(widget, *classes):
        for name in classes:
            widget.get_style_context().add_class(name)
        return widget

    def label(self, text, style=None, wrap=False):
        item = Gtk.Label(xalign=0)
        self.bind_text(item.set_text, text)
        if wrap:
            item.set_line_wrap(True)
            item.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            item.set_max_width_chars(47)
        if style:
            self.style(item, style)
        return item

    def card(self, spacing=12):
        outer = self.style(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), 'card')
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        inner.set_border_width(18)
        outer.pack_start(inner, True, True, 0)
        return outer, inner

    def combo(self, values, active):
        combo = Gtk.ComboBoxText()
        for value, title in values:
            combo.append(value, '')
            model = combo.get_model()
            row = model.iter_nth_child(None, len(model) - 1)
            self.bind_text(lambda text, model=model, row=row: model.set_value(row, 0, text), title)
        combo.set_active_id(str(active))
        self.controls.append(combo)
        return combo

    def build_window(self):
        self.window = Gtk.ApplicationWindow(application=self, title='Panelyra')
        self.window.set_default_size(780, 760)
        self.window_geometry = WindowGeometry(self.window, (780, 760))
        self.window.set_icon_from_file(str(ASSETS / 'panelyra-128.png'))
        self.style(self.window, 'panelyra')
        self.window.connect('delete-event', self.on_close)
        self.window.connect('destroy', self.on_window_destroy)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        theme_provider = Gtk.CssProvider()
        theme_provider.load_from_data(themes.CSS)
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), theme_provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.style_theme(self.window)
        header = self.localized(Gtk.HeaderBar(title='Panelyra'), 'subtitle',
                                ('A second life. A second screen.', 'Вторая жизнь. Второй экран.'))
        header.set_show_close_button(True)
        about = Gtk.Button.new_from_icon_name('help-about-symbolic', Gtk.IconSize.BUTTON)
        self.localized(about, 'tooltip-text', ('About Panelyra', 'О Panelyra'))
        about.connect('clicked', self.about)
        header.pack_end(about)
        self.notifications = NotificationCenter(self)
        self.style_theme(self.notifications.popover)
        header.pack_end(self.notifications.button)
        self.window.set_titlebar(header)
        body = self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        body.set_border_width(16)
        self.window.add(body)
        self.workarea = Gtk.Box(spacing=14)
        body.pack_start(self.workarea, True, True, 0)
        self.pages = Gtk.Stack()
        self.pages.set_homogeneous(True)
        self.pages.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.pages.set_transition_duration(120)
        self.sidebar_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.sidebar_frame.set_no_show_all(True)
        brand = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        brand.pack_start(Gtk.Image.new_from_file(str(ASSETS / 'panelyra-64.png')), False, False, 0)
        brand.pack_start(self.label('Panelyra', 'theme-sidebar-brand'), False, False, 0)
        brand.set_halign(Gtk.Align.CENTER)
        brand.show_all()
        self.sidebar_frame.pack_start(brand, False, False, 8)
        self.sidebar = self.style(Gtk.StackSidebar(stack=self.pages), 'theme-sidebar')
        self.sidebar.set_no_show_all(True)
        self.sidebar_frame.pack_start(self.sidebar, True, True, 0)
        self.workarea.pack_start(self.sidebar_frame, False, False, 0)
        content = self.page_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.workarea.pack_start(content, True, True, 0)
        self.switcher = self.style(Gtk.StackSwitcher(stack=self.pages), 'page-switcher')
        self.switcher.set_halign(Gtk.Align.CENTER)
        self.switcher.set_no_show_all(True)
        content.pack_start(self.switcher, False, False, 0)
        content.pack_start(self.pages, True, True, 0)
        connection = self.add_page('connection', ('Connection', 'Подключение'))
        preferences = self.add_page('settings', ('Settings', 'Настройки'))
        appearance = self.add_page('appearance', ('Appearance', 'Оформление'), footer=True)
        self.build_connection_page(connection)
        self.build_settings_page(preferences)
        self.build_overview()
        self.appearance = AppearancePanel(self)
        appearance.pack_start(self.appearance.widget, False, False, 0)
        self.appearance.widget.remove(self.appearance.actions)
        self.appearance_frame.pack_end(self.appearance.actions, False, False, 0)
        self.build_footer(body)
        self.pages.connect('notify::visible-child-name', self.on_page_changed)
        self.workarea.connect('size-allocate', self.on_layout_size)
        self.preview_theme(self.settings.theme)

    def add_page(self, name, title, footer=False):
        # Each page scrolls independently. Hidden content and expanded options
        # must never enlarge the window or push connection controls off-screen.
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)
        scroll.set_propagate_natural_width(False)
        scroll.set_propagate_natural_height(False)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_margin_end(4)
        scroll.add(content)
        page = scroll
        if footer:
            page = self.appearance_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            page.pack_start(scroll, True, True, 0)
        self.pages.add_named(page, name)
        self.bind_text(lambda text: self.pages.child_set_property(page, 'title', text), title)
        return content

    def build_connection_page(self, body):
        body.set_spacing(10)
        self.connection_grid = Gtk.Grid(column_spacing=14, row_spacing=14)
        body.pack_start(self.connection_grid, False, False, 0)
        hero = self.hero = self.style(Gtk.Box(spacing=16), 'hero')
        image = Gtk.Image.new_from_file(str(ASSETS / 'panelyra-64.png'))
        self.hero_logo = image
        image.set_no_show_all(True)
        image.set_margin_start(18)
        hero.pack_start(image, False, False, 0)
        intro = self.hero_intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        intro.set_margin_top(18)
        intro.set_margin_bottom(18)
        intro.set_margin_end(18)
        self.hero_eyebrow = self.label(('YOUR TABLET, REIMAGINED', 'ВАШ ПЛАНШЕТ МОЖЕТ БОЛЬШЕ'), 'eyebrow', True)
        self.hero_title = self.label(('Room for one more idea.', 'Место для новой идеи.'), 'headline', True)
        self.hero_description = self.label(('Extend your Linux desktop over one USB cable.', 'Расширьте рабочий стол Linux одним USB-кабелем.'), 'muted', True)
        for item in (self.hero_eyebrow, self.hero_title, self.hero_description):
            item.set_no_show_all(True)
            intro.pack_start(item, False, False, 0)
        hero.pack_start(intro, True, True, 0)
        self.connection_art = DeviceIllustration(self.visual_theme)
        self.connection_art.set_no_show_all(True)
        hero.pack_start(self.connection_art, True, True, 0)

        card, connect = self.card(spacing=6)
        self.device_card = self.style(card, 'connection-device')
        self.device_content = self.style(connect, 'device-content')
        connect.set_border_width(12)
        self.device_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        connect.pack_start(self.device_info, True, True, 0)
        self.device_art = DeviceIllustration(self.visual_theme)
        self.device_art.set_no_show_all(True)
        connect.pack_start(self.device_art, True, True, 0)
        device_header = self.device_header = Gtk.Box(spacing=8)
        device_header.set_no_show_all(True)
        device_header.pack_start(self.label(('Connected device', 'Устройство'), 'section-title'), True, True, 0)
        refresh = self.device_refresh = Gtk.Button.new_from_icon_name('view-refresh-symbolic', Gtk.IconSize.MENU)
        self.style(refresh, 'compact')
        self.localized(refresh, 'tooltip-text', ('Refresh devices', 'Обновить список устройств'))
        refresh.connect('clicked', lambda _: self.refresh_devices())
        device_header.pack_end(refresh, False, False, 0)
        self.device_info.pack_start(device_header, False, False, 0)
        self.device_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.device_waiting = self.label(self.tr('Checking USB devices…', 'Проверяем USB-устройства…'), 'muted', True)
        self.device_rows.pack_start(self.device_waiting, False, False, 0)
        self.device_info.pack_start(self.device_rows, False, False, 0)
        setup = self.localized(Gtk.Expander(), 'label', ('How to connect', 'Как подключить'))
        steps_box = self.device_steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        steps_box.set_margin_top(12)
        steps = [('Connect a USB data cable.', 'Подключите USB-кабель с передачей данных.'),
                 ('Enable USB tethering on Android.', 'Включите USB-модем в настройках Android.'),
                 ('Open Panelyra on your tablet.', 'Откройте Panelyra на планшете.')]
        for number, text in enumerate(steps, 1):
            row = Gtk.Box(spacing=10)
            badge = self.label(str(number), 'step-number')
            badge.set_valign(Gtk.Align.START)
            row.pack_start(badge, False, False, 0)
            step = self.label(text, 'muted', True)
            row.pack_start(step, True, True, 0)
            steps_box.pack_start(row, False, False, 2)
        setup.add(steps_box)
        self.device_setup = setup
        self.device_info.pack_start(setup, False, False, 0)
        help_button = self.style(self.localized(Gtk.Button(), 'label', ('Connection help', 'Помощь с подключением')), 'link')
        help_button.connect('clicked', self.help)
        support = self.device_support = self.style(Gtk.Box(spacing=8), 'device-support')
        support.pack_start(help_button, False, False, 0)
        self.downloads = DownloadPanel(self)
        self.style(self.downloads.button, 'compact')
        support.pack_end(self.downloads.button, False, False, 0)
        steps_box.pack_start(support, False, False, 0)
        self.device_actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.device_actions.set_no_show_all(True)
        connect.pack_end(self.device_actions, False, False, 0)
        self.network_card, self.network_content = self.card(spacing=4)
        self.style(self.network_card, 'network-card')
        self.style(self.network_content, 'network-content')
        self.network_content.set_border_width(10)
        self.connection_left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.connection_right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        self.details = self.localized(Gtk.Expander(), 'label', ('Connection log','Журнал подключения'))
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_min_content_height(120)
        view = Gtk.TextView(editable=False,cursor_visible=False,wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log = view.get_buffer()
        log_scroll.add(view)
        self.details.add(log_scroll)
        body.pack_start(self.details, False, False, 0)

    def build_overview(self):
        self.overview_card, summary = self.card(spacing=10)
        self.overview_detail = summary
        self.style(self.overview_card, 'overview-card')
        summary.pack_start(self.label(('Image settings', 'Параметры изображения'), 'section-title', True), False, False, 0)
        self.overview_profile = self.label('', 'muted', True)
        summary.pack_start(self.overview_profile, False, False, 0)
        self.overview_values = []
        for caption in (('Resolution', 'Разрешение'), ('Frame rate', 'Плавность'), ('Quality', 'Качество')):
            row = Gtk.Box(spacing=8)
            row.pack_start(self.label(caption, 'muted', True), True, True, 0)
            value = self.label('', 'mode-value')
            row.pack_end(value, False, False, 0)
            self.overview_values.append(value)
            summary.pack_start(row, False, False, 0)
        summary.pack_start(Gtk.Separator(), False, False, 2)
        self.overview_internet = self.label('', 'muted', True)
        summary.pack_start(self.overview_internet, False, False, 0)
        action = self.localized(Gtk.Button(), 'label', ('Adjust settings', 'Настроить'))
        action.connect('clicked', lambda _: self.pages.set_visible_child_name('settings'))
        summary.pack_start(action, False, False, 0)
        # Compact windows keep the useful current profile in a short strip.
        # The complete editor remains on Settings; wide layouts use the card.
        self.overview_compact = self.style(Gtk.Box(spacing=12), 'overview-strip')
        self.overview_compact.set_border_width(10)
        self.overview_short = self.label('', 'muted', True)
        self.overview_compact.pack_start(self.overview_short, True, True, 0)
        adjust = self.style(self.localized(Gtk.Button(), 'label', ('Adjust', 'Настроить')), 'compact')
        adjust.set_valign(Gtk.Align.CENTER)
        adjust.connect('clicked', lambda _: self.pages.set_visible_child_name('settings'))
        self.overview_compact.pack_end(adjust, False, False, 0)
        self.overview_card.pack_start(self.overview_compact, False, False, 0)
        self.update_overview()
        self.overview_card.show_all()
        self.overview_detail.set_no_show_all(True)
        self.overview_compact.set_no_show_all(True)

    def update_overview(self):
        if not hasattr(self, 'overview_values'):
            return
        names = {'balanced': ('Balanced', 'Баланс'), 'economy': ('Lightweight', 'Лёгкий'),
                 'crisp': ('Crisp', 'Чёткий'), 'custom': ('Custom', 'Свои настройки')}
        self.overview_profile.set_text(self.tr(*names[self.detect_profile()]))
        for widget, value in zip(self.overview_values, (
                f'{self.settings.width} × {self.settings.height}',
                f'{self.settings.fps} fps', f'QP {self.settings.quality}')):
            widget.set_text(value)
        state = self.internet.state
        if state is None:
            text = self.tr('Tablet Internet: unavailable', 'Интернет планшета: недоступен')
        elif state.enabled:
            text = self.tr('Tablet Internet: allowed', 'Интернет планшета: разрешён')
        else:
            text = self.tr('Tablet Internet: off', 'Интернет планшета: отключён')
        self.overview_internet.set_text(text)
        self.overview_short.set_text(
            f'{self.overview_profile.get_text()} · {self.settings.width} × {self.settings.height}'
            f' · {self.settings.fps} fps · QP {self.settings.quality}')
        self.overview_short.set_tooltip_text(text)

    def style_theme(self, widget):
        context = widget.get_style_context()
        context.add_class('panelyra')
        for theme_id in themes.THEMES:
            context.remove_class('theme-' + theme_id)
        context.add_class('theme-' + self.visual_theme)

    def preview_theme(self, theme_id):
        if theme_id not in themes.THEMES:
            raise ValueError('Unknown theme')
        self.visual_theme = theme_id
        for widget in (self.window, self.notifications.popover, self.downloads.window):
            if widget is not None:
                self.style_theme(widget)
        self.notifications.set_theme(theme_id)
        self.connection_art.set_theme(theme_id)
        self.device_art.set_theme(theme_id)
        self.layout_signature = None
        if self.layout_source is not None:
            GLib.source_remove(self.layout_source)
            self.layout_source = None
        self.apply_theme_layout()

    def commit_theme(self, theme_id):
        try:
            # Save only appearance; an unfinished video edit must stay a draft.
            value = replace(self.settings, theme=theme_id).validate()
            save(value)
        except (OSError, ValueError) as error:
            self.append_log(self.tr('Could not save appearance: ', 'Не удалось сохранить оформление: ') + str(error))
            return False
        self.settings = value
        self.preview_theme(theme_id)
        self.sync_theme()
        return True

    def on_page_changed(self, *_):
        page = self.pages.get_visible_child_name()
        if self.previous_page == 'appearance' and page != 'appearance':
            self.appearance.cancel()
        self.previous_page = page
        self.layout_signature = None
        self.apply_theme_layout()

    def on_layout_size(self, *_):
        if self.layout_source is None and not self.closing:
            self.layout_source = GLib.idle_add(self.apply_theme_layout)

    def on_window_destroy(self, *_):
        self.closing = True
        self.clear_pending_theme()
        if self.layout_source is not None:
            GLib.source_remove(self.layout_source)
            self.layout_source = None

    def update_theme_header(self):
        if not hasattr(self, 'hero_title'):
            return
        layout = themes.THEMES[self.visual_theme].layout
        self.hero_title.set_text('Panelyra' if layout == 'stacked'
                                 else self.tr('Your second screen', 'Ваш второй экран') if layout == 'editorial'
                                 else self.tr('Panelyra · USB display', 'Panelyra · USB-экран') if layout == 'console'
                                 else self.tr('Room for one more idea.', 'Место для новой идеи.'))
        self.hero_description.set_text(self.tr('A second life. A second screen.', 'Вторая жизнь. Второй экран.')
                                       if layout == 'stacked' else self.tr(
                                           'Extend your Linux desktop over one USB cable.',
                                           'Расширьте рабочий стол Linux одним USB-кабелем.'))

    @staticmethod
    def detach(widget):
        parent = widget.get_parent()
        if parent is not None:
            parent.remove(widget)

    def arrange_picture_fields(self, narrow):
        for child in self.picture_grid.get_children():
            self.picture_grid.remove(child)
        self.picture_grid.set_column_homogeneous(not narrow)
        positions = ((0, 0, 2), (0, 1, 2), (0, 2, 1), (1, 2, 1)) if narrow else (
            (0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1))
        for field, (column, row, span) in zip(self.picture_fields, positions):
            self.picture_grid.attach(field, column, row, span, 1)
        self.picture_grid.show_all()

    def apply_theme_layout(self):
        self.layout_source = None
        if self.closing:
            return False
        layout = themes.THEMES[self.visual_theme].layout
        width = self.window.get_allocated_width()
        if width <= 1:
            width = self.window.get_default_size().width
        height = self.window.get_allocated_height()
        if height <= 1:
            height = self.window.get_default_size().height
        sidebar = layout == 'studio' and width >= 760
        page = self.pages.get_visible_child_name()
        short = height < 720
        signature = (self.visual_theme, sidebar, short, page)
        self.update_theme_header()
        if signature == self.layout_signature:
            return False
        self.layout_signature = signature
        window_context = self.window.get_style_context()
        (window_context.add_class if short else window_context.remove_class)('short-window')
        self.body.set_spacing(10 if short else 14)
        self.page_content.set_spacing(10 if short else 14)
        self.sidebar_frame.set_visible(sidebar)
        self.sidebar.set_visible(sidebar)
        self.switcher.set_visible(not sidebar)
        self.connection_grid.set_column_spacing(12)
        self.connection_grid.set_row_spacing((6 if layout == 'stacked' else 8) if short else 10)
        self.connection_grid.set_column_homogeneous(layout == 'editorial')

        # Reparent the real editors, never copies of their values. Switching
        # pages or previewing a design cannot discard an unfinished edit.
        for widget in (self.hero, self.device_card, self.picture_card,
                       self.network_card, self.connection_left, self.connection_right,
                       self.internet.widget):
            self.detach(widget)
        for column in (self.connection_left, self.connection_right):
            for child in column.get_children():
                column.remove(child)
        main_editor = layout != 'classic' and page != 'settings'
        if main_editor:
            self.network_content.pack_start(self.internet.widget, False, False, 0)
        else:
            self.settings_connection.pack_start(self.internet.widget, False, False, 0)
            self.settings_body.pack_start(self.picture_card, False, False, 0)
            self.settings_body.reorder_child(self.picture_card, 1)
        self.arrange_picture_fields(main_editor and layout in ('studio', 'glass', 'console', 'editorial'))
        self.picture_content.set_border_width((8 if short else 10) if main_editor else 18)
        self.picture_content.set_spacing(6 if main_editor else 12)
        self.device_content.set_spacing(12)
        self.device_content.set_border_width(8 if short else 10)
        self.device_info.set_spacing(4)
        self.device_header.set_visible(layout != 'stacked')
        self.device_actions.set_visible(layout == 'stacked')
        for widget in (self.device_refresh, self.downloads.button):
            self.detach(widget)
        if layout == 'stacked':
            self.device_actions.pack_start(self.downloads.button, False, False, 0)
            self.device_actions.pack_start(self.device_refresh, False, False, 0)
            self.device_refresh.set_halign(Gtk.Align.END)
            self.device_content.reorder_child(self.device_art, 0)
        else:
            self.device_header.pack_end(self.device_refresh, False, False, 0)
            self.device_support.pack_end(self.downloads.button, False, False, 0)
            self.device_refresh.set_halign(Gtk.Align.FILL)
        self.detach(self.device_support)
        support_parent = self.device_info if layout == 'classic' else self.device_steps
        support_parent.pack_start(self.device_support, False, False, 0)
        self.device_content.set_orientation(Gtk.Orientation.HORIZONTAL if layout in
                                            ('stacked', 'editorial') else Gtk.Orientation.VERTICAL)
        self.device_content.reorder_child(self.device_art, 0 if layout in ('console', 'stacked') else 1)
        self.device_info.set_valign(Gtk.Align.CENTER if layout in ('stacked', 'editorial') else Gtk.Align.START)
        self.device_art.set_visible(layout in ('stacked', 'studio', 'editorial', 'console'))
        self.device_art.set_variant('pair' if layout == 'studio' else 'tablet')
        self.device_art.set_size_request(76 if layout == 'stacked' else 88 if layout == 'editorial' else 180,
                                        66 if layout == 'stacked' and short else 76 if layout == 'stacked'
                                        else (104 if short else 124) if layout == 'editorial'
                                        else 174 if short else 214)
        self.device_art.set_hexpand(layout in ('studio', 'console'))
        self.device_support.set_orientation(Gtk.Orientation.VERTICAL if layout in
                                            ('studio', 'glass', 'editorial', 'console') else Gtk.Orientation.HORIZONTAL)
        self.hero.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.hero.set_spacing(12)
        self.hero.set_halign(Gtk.Align.CENTER if layout == 'stacked' else Gtk.Align.FILL)
        self.hero_logo.set_visible(layout in ('classic', 'stacked'))
        logo_size = 44 if layout == 'stacked' else 64
        self.hero_logo.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(
            str(ASSETS / 'panelyra-64.png'), logo_size, logo_size, True))
        self.hero_logo.set_margin_start(0 if layout == 'stacked' else 16)
        self.hero_eyebrow.set_visible(layout in ('classic', 'editorial'))
        self.hero_title.show()
        self.hero_description.set_visible(layout != 'console')
        self.hero_intro.set_margin_start(0 if layout in ('stacked', 'editorial') else 12)
        self.hero_intro.set_margin_top((0 if short else 4) if layout == 'stacked' else 4 if short else 12)
        self.hero_intro.set_margin_bottom((0 if short else 4) if layout == 'stacked' else 4 if short else 12)
        self.hero_intro.set_margin_end(0)
        self.hero_intro.set_spacing(4)
        for label in (self.hero_title, self.hero_description):
            label.set_xalign(.5 if layout == 'stacked' else 0)
            label.set_justify(Gtk.Justification.CENTER if layout == 'stacked' else Gtk.Justification.LEFT)
        if layout == 'stacked':
            self.hero_title.set_text('Panelyra')
            self.hero_description.set_text(self.tr('A second life. A second screen.', 'Вторая жизнь. Второй экран.'))
        self.connection_art.set_visible(layout == 'glass')
        self.connection_art.set_variant('aurora')
        self.connection_art.set_size_request(260, 116 if short else 140)
        self.hero.set_child_packing(self.connection_art, True, True, 0, Gtk.PackType.START)
        self.network_content.set_border_width(10)
        self.internet.set_compact(main_editor, horizontal=layout == 'stacked')

        def column(box, widgets):
            for widget in widgets:
                box.pack_start(widget, False, False, 0)
            return box

        if layout == 'classic':
            placement = [(self.hero, 0, 0, 2), (self.device_card, 0, 1, 2)]
        elif layout == 'stacked':
            placement = [(self.hero, 0, 0, 2), (self.device_card, 0, 1, 2)]
            if main_editor:
                placement += [(self.picture_card, 0, 2, 2), (self.network_card, 0, 3, 2)]
        elif layout == 'editorial':
            placement = [(self.hero, 0, 0, 1), (self.device_card, 1, 0, 1)]
            if main_editor:
                placement += [(self.picture_card, 0, 1, 1), (self.network_card, 1, 1, 1)]
        elif layout == 'glass':
            left = column(self.connection_left, [self.device_card] + ([self.network_card] if main_editor else []))
            placement = [(self.hero, 0, 0, 2), (left, 0, 1, 1)]
            if main_editor:
                placement.append((self.picture_card, 1, 1, 1))
        else:
            placement = [(self.device_card, 0, 0, 1)]
            if main_editor:
                right = column(self.connection_right, [self.picture_card, self.network_card])
                placement.append((right, 1, 0, 1))
        for widget, col, row, span in placement:
            widget.set_hexpand(True)
            widget.set_valign(Gtk.Align.START)
            self.connection_grid.attach(widget, col, row, span, 1)
            widget.show_all()
        # show_all must respect the selected design's hidden decorative parts.
        self.device_art.set_visible(layout in ('stacked', 'studio', 'editorial', 'console'))
        self.device_header.set_visible(layout != 'stacked')
        self.device_header.get_children()[0].show()
        self.device_refresh.show()
        self.device_actions.set_visible(layout == 'stacked')
        self.downloads.button.show()
        self.hero_logo.set_visible(layout in ('classic', 'stacked'))
        self.hero_eyebrow.set_visible(layout in ('classic', 'editorial'))
        self.connection_art.set_visible(layout == 'glass')
        self.picture_card.show_all()
        self.picture_extra.set_visible(not main_editor)
        self.internet.widget.show_all()
        return False

    def build_settings_page(self, body):
        self.settings_body = body
        card, connection = self.card()
        self.settings_connection = connection
        body.pack_start(card, False, False, 0)
        connection.pack_start(self.label(('Connection', 'Подключение'), 'section-title'), False, False, 0)
        row = Gtk.Box(spacing=12)
        row.pack_start(self.label(('Connection method', 'Способ подключения'), 'muted', True), True, True, 0)
        self.transport = self.combo([('usb', 'USB'), ('adb', 'ADB'), ('auto', ('Automatic', 'Автоматически'))], self.settings.transport)
        row.pack_end(self.transport, False, False, 0)
        connection.pack_start(row, False, False, 0)
        connection.pack_start(Gtk.Separator(), False, False, 2)
        self.internet = InternetPanel(self)
        connection.pack_start(self.internet.widget, False, False, 0)

        card, settings = self.card()
        self.picture_card = self.style(card, 'picture-card')
        self.picture_content = self.style(settings, 'picture-content')
        body.pack_start(card, False, False, 0)
        settings.pack_start(self.label(('Picture', 'Изображение'), 'section-title'), False, False, 0)
        self.profile = self.combo([
            ('balanced', ('Balanced — everyday work', 'Баланс — на каждый день')),
            ('economy', ('Lightweight — older tablets', 'Лёгкий — для слабого планшета')),
            ('crisp', ('Crisp — detailed text', 'Чёткий — для работы с текстом')),
            ('custom', ('Custom settings', 'Свои настройки'))], self.detect_profile())
        for renderer in self.profile.get_cells():
            renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
            renderer.set_property('max-width-chars', 18)
        grid = self.picture_grid = Gtk.Grid(column_spacing=14, row_spacing=7)
        settings.pack_start(grid, False, False, 0)
        self.width = Gtk.SpinButton.new_with_range(160, 2560, 2)
        self.height = Gtk.SpinButton.new_with_range(160, 2560, 2)
        self.width.set_value(self.settings.width)
        self.height.set_value(self.settings.height)
        self.width.set_numeric(True)
        self.height.set_numeric(True)
        self.width.set_width_chars(4)
        self.width.set_max_width_chars(4)
        self.height.set_width_chars(4)
        self.height.set_max_width_chars(4)
        size_row = Gtk.Box(spacing=5)
        size_row.pack_start(self.width, True, True, 0)
        size_row.pack_start(Gtk.Label(label='×'), False, False, 0)
        size_row.pack_start(self.height, True, True, 0)
        self.fps = self.combo([(str(n), (f'{n} fps · experimental', f'{n} fps · эксперимент')
                               if n == 60 else f'{n} fps') for n in (15,20,30,60)], self.settings.fps)
        for renderer in self.fps.get_cells():
            renderer.set_property('ellipsize', Pango.EllipsizeMode.END)
            renderer.set_property('max-width-chars', 6)
        size_row.set_hexpand(True)
        self.fps.set_hexpand(True)
        if self.fps.get_active_id() is None:
            self.fps.append(str(self.settings.fps), f'{self.settings.fps} fps')
            self.fps.set_active_id(str(self.settings.fps))
        self.controls.extend((self.width, self.height))
        self.quality = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 10,35,1)
        self.quality.set_value(self.settings.quality)
        self.quality.set_digits(0)
        self.quality.set_value_pos(Gtk.PositionType.RIGHT)
        self.localized(self.quality, 'tooltip-text', ('Lower QP means more detail and more USB traffic.', 'Меньше QP — больше деталей и трафика по USB.'))
        self.picture_fields = []
        for caption, widget in ((('Profile', 'Профиль'), self.profile),
                                (('Resolution', 'Разрешение'), size_row),
                                (('Frame rate', 'Плавность'), self.fps),
                                (('Quality · QP', 'Качество · QP'), self.quality)):
            field = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            field.set_hexpand(True)
            field.pack_start(self.label(caption, 'muted'), False, False, 0)
            field.pack_start(widget, False, False, 0)
            self.picture_fields.append(field)
        # The same editor moves between Connection and Settings; drafts,
        # sensitivity and signal handlers remain attached to these widgets.
        self.arrange_picture_fields(False)
        self.controls.append(self.quality)
        extra = self.picture_extra = self.localized(Gtk.Expander(), 'label', ('More options', 'Дополнительно'))
        extra.set_no_show_all(True)
        extras = Gtk.Box(spacing=12)
        extras.set_margin_top(12)
        self.capture = Gtk.SpinButton.new_with_range(5,60,1)
        self.capture.set_value(self.settings.capture_fps)
        self.capture.set_width_chars(2)
        self.capture.set_max_width_chars(2)
        self.controls.append(self.capture)
        extras.pack_start(self.label(('PC capture rate', 'Частота захвата ПК'), 'muted', True), True, True, 0)
        extras.pack_end(self.capture, False, False, 0)
        extras.show_all()
        extra.add(extras)
        settings.pack_start(extra, False, False, 0)

        card, general = self.card()
        body.pack_start(card, False, False, 0)
        general.pack_start(self.label(('General', 'Общие'), 'section-title'), False, False, 0)
        row = Gtk.Box(spacing=12)
        row.pack_start(self.label(('Language', 'Язык'), 'muted'), True, True, 0)
        self.language = self.combo([('auto',('System language','Язык системы')),('ru','Русский'),('en','English')],self.settings.language)
        # Language can change on both devices without restarting a stream.
        self.controls.remove(self.language)
        row.pack_end(self.language, False, False, 0)
        general.pack_start(row, False, False, 0)
        general.pack_start(self.label(('Changes immediately on this PC.\nThe tablet follows when connected.',
                                     'На ПК язык меняется сразу.\nНа планшете — при подключении.'), 'tiny', True), False, False, 0)
        self.autoconnect = self.localized(Gtk.CheckButton(), 'label', ('Connect when opened', 'Подключать при запуске'))
        self.autoconnect.set_active(self.settings.auto_connect)
        general.pack_start(self.autoconnect, False, False, 0)
        self.profile.connect('changed', self.on_profile)
        for widget in (self.width,self.height,self.quality,self.capture):
            widget.connect('value-changed',self.on_settings_changed)
        for widget in (self.fps,self.transport):
            widget.connect('changed',self.on_settings_changed)
        self.language.connect('changed', self.on_language_changed)
        self.autoconnect.connect('toggled',self.on_settings_changed)

    def build_footer(self, body):
        footer, status_box = self.card(spacing=0)
        self.style(footer, 'stream-footer')
        status_box.set_border_width(12)
        body.pack_start(footer, False, False, 0)
        line = Gtk.Box(spacing=10)
        self.badge = self.style(Gtk.Label(), 'badge')
        self.badge.set_valign(Gtk.Align.CENTER)
        line.pack_start(self.badge, False, False, 0)
        self.status = self.label('', None, True)
        self.status.set_lines(2)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.set_max_width_chars(26)
        self.status.set_size_request(-1, 38)
        self.set_status(('Your next screen is one click away.', 'Ещё один экран — в одном нажатии.'))
        line.pack_start(self.status, True, True, 0)
        self.start_button = self.style(self.localized(Gtk.Button(), 'label', ('Connect tablet','Подключить планшет')), 'primary')
        self.start_button.set_valign(Gtk.Align.CENTER)
        self.start_button.connect('clicked',lambda _: self.start())
        self.stop_button = self.style(self.localized(Gtk.Button(), 'label', ('Stop','Остановить')), 'stop')
        self.stop_button.set_valign(Gtk.Align.CENTER)
        self.stop_button.set_sensitive(False)
        self.stop_button.connect('clicked',lambda _: self.stop())
        line.pack_start(self.start_button, False, False, 0)
        line.pack_start(self.stop_button, False, False, 0)
        status_box.pack_start(line, False, False, 0)
        bottom = Gtk.Box(spacing=12)
        bottom.pack_start(self.label(('Closing this window stops its stream.','Закрытие окна останавливает запущенную здесь передачу.'),'tiny',True),True,True,0)
        bottom.pack_end(self.label(f'v{__version__} · TanosX','muted'),False,False,0)
        body.pack_start(bottom,False,False,0)

    def refresh_devices(self):
        if self.closing:
            return False
        if not self.device_scan_running:
            self.device_scan_running = True
            threading.Thread(target=self.scan_devices, daemon=True).start()
            self.internet.refresh()
        return True

    def scan_devices(self):
        try:
            devices, error = discover_devices(), None
        except (OSError, RuntimeError, ValueError) as failure:
            devices, error = [], str(failure)
        GLib.idle_add(self.on_devices, devices, error)

    def on_devices(self, devices, error):
        self.device_scan_running = False
        return self.render_devices(devices, error)

    def render_devices(self, devices, error):
        if self.closing:
            return False
        snapshot = (tuple(devices), error, self.settings.transport, self.russian)
        if snapshot == self.device_snapshot:
            return False
        self.device_snapshot = snapshot
        for child in self.device_rows.get_children():
            child.destroy()

        def text(value, style='device-detail'):
            widget = self.label(value, style, True)
            widget.set_max_width_chars(58)
            widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.device_rows.pack_start(widget, False, False, 0)

        if error:
            text(self.tr('Device check unavailable', 'Не удалось проверить USB'), 'device-name')
            text(self.tr('Reconnect the cable or refresh the list.', 'Переподключите кабель или обновите список.'))
            self.device_rows.set_tooltip_text(error)
        elif not devices:
            text(self.tr('No tablet connected', 'Планшет не подключён'), 'device-name')
            text(self.tr('Connect a USB data cable. Your device will appear here automatically.',
                         'Подключите USB-кабель с передачей данных. Устройство появится здесь автоматически.'))
            self.device_rows.set_tooltip_text(None)
        else:
            self.device_rows.set_tooltip_text(None)
            for index, device in enumerate(devices):
                if index:
                    self.device_rows.pack_start(Gtk.Separator(), False, False, 3)
                text(device.name, 'device-name')
                if device.state == 'usb-ready':
                    text(self.tr('USB tethering enabled', 'USB-модем включён'))
                    text(' · '.join(value for value in (device.interface, device.tablet_ip) if value))
                elif device.state == 'usb-no-address':
                    text(self.tr('USB detected · waiting for an address', 'USB найден · ожидаем адрес'))
                elif self.settings.transport == 'adb':
                    text(self.tr('USB detected · ADB selected', 'USB подключён · выбран режим ADB'))
                else:
                    text(self.tr('USB detected · enable USB tethering', 'USB подключён · включите USB-модем'))
                if device.interface and device.state != 'usb-ready':
                    text(device.interface)
            if len(devices) > 1:
                text(self.tr('Several devices detected. Keep the tablet you want to use connected.',
                             'Найдено несколько устройств. Оставьте подключённым нужный планшет.'))
        self.device_rows.show_all()
        return False

    def detect_profile(self):
        for name, mode in PRESETS.items():
            if all(getattr(self.settings,key)==value for key,value in mode.items()):
                return name
        return 'custom'

    def values(self):
        return replace(self.settings, width=self.width.get_value_as_int(),height=self.height.get_value_as_int(),
            fps=int(self.fps.get_active_id()),capture_fps=self.capture.get_value_as_int(),
            quality=round(self.quality.get_value()),transport=self.transport.get_active_id(),
            auto_connect=self.autoconnect.get_active(),language=self.language.get_active_id()).validate()

    def save_apk_settings(self, **values):
        allowed = {'apk_path', 'apk_port', 'apk_duration', 'apk_interface'}
        if set(values) - allowed:
            raise ValueError('Unknown APK server setting')
        settings = replace(self.settings, **values).validate()
        save(settings)
        self.settings = settings

    def on_profile(self,*_):
        mode = PRESETS.get(self.profile.get_active_id())
        if mode is None or self.updating:
            return
        self.updating=True
        self.width.set_value(mode['width']); self.height.set_value(mode['height'])
        self.fps.set_active_id(str(mode['fps'])); self.capture.set_value(mode['capture_fps'])
        self.quality.set_value(mode['quality'])
        self.updating=False
        self.on_settings_changed()

    def on_language_changed(self, *_):
        if self.updating:
            return
        try:
            # Incomplete video edits must not block a language change.
            value = replace(self.settings, language=self.language.get_active_id())
            save(value)
        except (OSError, ValueError) as error:
            self.updating = True
            try:
                self.language.set_active_id(self.settings.language)
            finally:
                self.updating = False
            self.set_status((f'Could not save the language: {error}',
                             f'Не удалось сохранить язык: {error}'), 'error')
            return
        self.settings = value
        self.update_language()
        self.sync_language()

    def sync_language(self):
        """Queue only the latest selection; never wait for a child on the UI thread."""
        if self.process is None or self.stopped:
            return
        self.pending_language = resolve_language(self.settings.language)
        if self.language_timer is None and self.send_pending_language():
            self.language_timer = GLib.timeout_add(100, self.send_pending_language)

    def send_pending_language(self):
        if self.process is not None and not self.stopped and self.pending_language:
            payload = (json.dumps({'language': self.pending_language}) + '\n').encode('ascii')
            try:
                # A small write to this nonblocking pipe is atomic (below PIPE_BUF).
                os.write(self.process.stdin.fileno(), payload)
            except BlockingIOError:
                return True
            except (OSError, ValueError):
                self.append_log(self.tr('Tablet language control is unavailable. Reconnect to try again.',
                                        'Передача языка недоступна. Попробуйте переподключить планшет.'))
        self.pending_language = None
        self.language_timer = None
        return False

    def clear_pending_language(self):
        if self.language_timer is not None:
            GLib.source_remove(self.language_timer)
            self.language_timer = None
        self.pending_language = None

    def sync_theme(self):
        """Send only an applied design to the local sender, without restarting it."""
        if self.process is None or self.stopped:
            return
        self.pending_theme = self.settings.theme
        if self.theme_timer is None and self.send_pending_theme():
            self.theme_timer = GLib.timeout_add(100, self.send_pending_theme)

    def send_pending_theme(self):
        if self.process is not None and not self.stopped and self.pending_theme:
            payload = (json.dumps({'theme': self.pending_theme}) + '\n').encode('ascii')
            try:
                # The control pipe is local to the PC; no Android protocol change.
                os.write(self.process.stdin.fileno(), payload)
            except BlockingIOError:
                return True
            except (OSError, ValueError):
                self.append_log(self.tr(
                    'Lock screen style saved. Reconnect the display to use it.',
                    'Стиль заставки сохранён. Переподключите экран, чтобы применить его.'))
        self.pending_theme = None
        self.theme_timer = None
        return False

    def clear_pending_theme(self):
        if self.theme_timer is not None:
            GLib.source_remove(self.theme_timer)
            self.theme_timer = None
        self.pending_theme = None

    def on_settings_changed(self,*_):
        if self.updating:
            return
        try:
            value=self.values()
            save(value)
            self.settings=value
            self.update_language()
            self.updating=True
            self.profile.set_active_id(self.detect_profile())
            self.updating=False
            self.update_overview()
            if hasattr(self,'status') and self.process is None and self.external_timer is None:
                self.set_status(('Your settings are saved. Ready to connect.', 'Настройки сохранены. Можно подключить планшет.'))
        except (OSError,ValueError) as error:
            if hasattr(self,'status'):
                self.set_status((f'Check the settings: {error}', f'Проверьте настройки: {error}'),'error')

    def set_status(self,text,kind='ready'):
        self.status_text, self.status_kind = text, kind
        localized = self.tr(*text) if isinstance(text, tuple) else text
        self.status.set_text(localized)
        self.status.set_tooltip_text(localized)
        context=self.badge.get_style_context()
        for name in ('busy','error'):
            context.remove_class(name)
        if kind in ('busy','error'):
            context.add_class(kind)
        self.badge.set_text({'ready':self.tr('READY','ГОТОВО'),'busy':self.tr('CONNECTING','ПОДКЛЮЧЕНИЕ'),
                             'connected':self.tr('CONNECTED','ПОДКЛЮЧЕНО'),'error':self.tr('ATTENTION','ВНИМАНИЕ')}[kind])

    def set_busy(self,busy):
        for control in self.controls:
            control.set_sensitive(not busy)
        self.start_button.set_sensitive(not busy)
        self.stop_button.set_sensitive(busy)

    def append_log(self,line):
        self.lines=(self.lines+[line])[-120:]
        self.log.set_text('\n'.join(self.lines))

    def start(self):
        if self.process is not None or self.closing:
            return
        if running_stream() is not None:
            self.set_status(('A stream is already running. Use its window or “panelyra stop”.',
                'Передача уже работает. Управляйте ею в её окне или командой «panelyra stop».'),'connected')
            self.set_busy(True)
            self.stop_button.set_sensitive(False)
            if self.external_timer is None:
                self.external_timer=GLib.timeout_add_seconds(2,self.check_external)
            return
        try:
            self.settings=self.values()
            save(self.settings)
        except (OSError,ValueError) as error:
            self.set_status(str(error),'error')
            return
        self.stopped=False; self.last_error=None; self.lines=[]
        self.log.set_text('')
        self.set_status(('Looking for your tablet…','Подключаем планшет…'),'busy')
        self.set_busy(True)
        env={**os.environ,'PYTHONUNBUFFERED':'1'}
        env['PYTHONPATH']=str(PROJECT)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        local_adb=PROJECT/'.tools/bin'
        if local_adb.is_dir():
            env['PATH']=str(local_adb)+os.pathsep+env.get('PATH','')
        try:
            self.process=subprocess.Popen([sys.executable,'-m','usbdisplay',*self.settings.command_args(),
                '--json-events','--language',resolve_language(self.settings.language),
                '--theme',self.settings.theme,'--control-stdin'],
                cwd=PROJECT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                encoding='utf-8',errors='replace',env=env)
        except OSError as error:
            self.set_status(str(error),'error'); self.set_busy(False)
            return
        os.set_blocking(self.process.stdin.fileno(), False)
        threading.Thread(target=self.read_output,args=(self.process,),daemon=True).start()

    def check_external(self):
        if running_stream() is not None:
            return True
        self.external_timer=None
        self.set_status(('The stream has ended. Ready to connect.','Передача завершена. Можно подключить планшет.'))
        self.set_busy(False)
        return False

    def read_output(self,process):
        for line in process.stdout:
            GLib.idle_add(self.on_output,process,line.rstrip())
        process.stdout.close()
        GLib.idle_add(self.on_finished,process,process.wait())

    def on_output(self,process,line):
        if process is not self.process:
            return False
        if line.startswith('@panelyra '):
            try:
                event=json.loads(line[len('@panelyra '):])
                if event['event']=='ready' and not self.stopped:
                    self.set_status(('Connected. Move a window onto your new screen.',
                                             'Подключено. Перетащите окно на новый экран.'),'connected')
                elif event['event']=='locked' and not self.stopped:
                    self.set_status(('Computer locked. The screen will reconnect after unlocking.',
                        'Компьютер заблокирован. Экран подключится после разблокировки.'),'connected')
                elif event['event']=='resuming' and not self.stopped:
                    self.set_status(('Restoring your tablet display…',
                        'Восстанавливаем экран планшета…'),'busy')
                elif event['event']=='error':
                    self.last_error=event.get('message','')
                elif event['event']=='language' and event.get('synced') is False:
                    self.append_log(self.tr('Tablet language was not synchronized. Update the Android app and reconnect.',
                        'Язык планшета не синхронизирован. Обновите Android-приложение и переподключитесь.'))
            except (ValueError,KeyError):
                pass
        else:
            self.append_log(line)
        return False

    def stop(self):
        if self.process is None or self.stopped:
            return
        self.stopped=True
        self.clear_pending_language()
        self.clear_pending_theme()
        self.set_status(('Stopping the stream…','Останавливаем передачу…'),'busy')
        self.stop_button.set_sensitive(False)
        if self.process.poll() is None:
            try: self.process.terminate()
            except ProcessLookupError: pass
            self.force_timer=GLib.timeout_add_seconds(8,self.force_stop,self.process)

    def force_stop(self,process):
        self.force_timer=None
        if process is self.process and process.poll() is None:
            try: process.kill()
            except ProcessLookupError: pass
        return False

    def on_finished(self,process,code):
        if process is not self.process:
            return False
        self.clear_pending_language()
        self.clear_pending_theme()
        if process.stdin is not None:
            try: process.stdin.close()
            except OSError: pass
        self.process=None
        if self.force_timer is not None:
            GLib.source_remove(self.force_timer); self.force_timer=None
        if self.closing:
            self.quit(); return False
        if self.stopped or not code:
            self.set_status(('Stream stopped. Your settings are saved.','Передача остановлена. Настройки сохранены.'))
        else:
            self.set_status(('Could not connect. Check the USB cable, tethering and Android app. See the log for details.',
                'Не удалось подключиться. Проверьте кабель, USB-модем и приложение на планшете. Подробности — в журнале.'),'error')
            if self.last_error and self.last_error not in '\n'.join(self.lines):
                self.append_log(self.last_error)
        self.set_busy(False)
        return False

    def on_close(self,*_):
        self.appearance.close()
        self.closing=True
        if self.layout_source is not None:
            GLib.source_remove(self.layout_source)
            self.layout_source = None
        self.window_geometry.close()
        self.notifications.close()
        self.downloads.close()
        self.internet.close()
        if self.device_timer is not None:
            GLib.source_remove(self.device_timer)
            self.device_timer = None
        if self.external_timer is not None:
            GLib.source_remove(self.external_timer); self.external_timer=None
        if self.process is None:
            self.quit()
        else:
            self.stop()
        return True

    def about(self,*_):
        dialog=Gtk.AboutDialog(transient_for=self.window,modal=True,program_name='Panelyra',version=__version__,
            comments=self.tr('Give your Android tablet a second life as a Linux display.',
                             'Дайте Android-планшету вторую жизнь в роли экрана Linux.'),
            authors=['TanosX'],copyright='© 2026 TanosX',license_type=Gtk.License.MIT_X11)
        dialog.set_logo(self.window.get_icon())
        self.style_theme(dialog)
        dialog.run(); dialog.destroy()

    def help(self,*_):
        dialog=Gtk.MessageDialog(transient_for=self.window,modal=True,buttons=Gtk.ButtonsType.CLOSE,
            message_type=Gtk.MessageType.INFO,text=self.tr('One cable. Two screens.','Один кабель. Два экрана.'))
        self.style_theme(dialog)
        dialog.format_secondary_text(self.tr(
            '1. Install the Panelyra Android APK.\n2. Connect a data-capable USB cable and enable USB tethering.\n3. Open Panelyra on Android, then click Connect tablet.\n\nArrange the new screen in GNOME Settings → Displays. USB debugging is only needed for the optional ADB mode.\n\nSupported desktop: GNOME on Wayland. No audio or tablet touch input yet.',
            '1. Установите APK Panelyra на Android.\n2. Подключите USB-кабель с передачей данных и включите USB-модем.\n3. Откройте Panelyra на Android и нажмите «Подключить планшет».\n\nРасположите новый экран в «Настройки GNOME → Дисплеи». Отладка USB нужна только для дополнительного режима ADB.\n\nПоддерживается GNOME Wayland. Звук и касания планшета пока не передаются.'))
        dialog.run(); dialog.destroy()


def main(auto_connect=None,language=None):
    Gdk.set_program_class(APP_ID)
    return Launcher(auto_connect=auto_connect,language=language).run([])


if __name__=='__main__':
    raise SystemExit(main())

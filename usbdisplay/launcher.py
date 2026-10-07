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
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from . import APP_ID, __version__
from . import runtime
from .devices import discover_devices
from .download_ui import DownloadPanel
from .language import resolve_language
from .notifications_ui import NotificationCenter
from .settings import PRESETS, Settings, load, save
from .window_geometry import WindowGeometry

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
.panelyra .headline { font-size: 27px; font-weight: 800; }
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
        self.device_timer = None
        self.device_scan_running = False
        self.device_snapshot = None
        self.last_error = None
        self.lines = []
        self.controls = []

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
                self.append_log('Preferences reset: ' + self.settings_warning)
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
        self.window.set_default_size(880, 820)
        self.window_geometry = WindowGeometry(self.window, (880, 820))
        self.window.set_icon_from_file(str(ASSETS / 'panelyra-128.png'))
        self.style(self.window, 'panelyra')
        self.window.connect('delete-event', self.on_close)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(self.window.get_screen(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        header = self.localized(Gtk.HeaderBar(title='Panelyra'), 'subtitle',
                                ('A second life. A second screen.', 'Вторая жизнь. Второй экран.'))
        header.set_show_close_button(True)
        about = Gtk.Button.new_from_icon_name('help-about-symbolic', Gtk.IconSize.BUTTON)
        self.localized(about, 'tooltip-text', ('About Panelyra', 'О Panelyra'))
        about.connect('clicked', self.about)
        header.pack_end(about)
        self.notifications = NotificationCenter(self)
        header.pack_end(self.notifications.button)
        self.window.set_titlebar(header)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.window.add(scroll)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        body.set_border_width(24)
        scroll.add(body)

        hero = self.style(Gtk.Box(spacing=18), 'hero')
        hero.set_border_width(0)
        image = Gtk.Image.new_from_file(str(ASSETS / 'panelyra-128.png'))
        image.set_margin_start(20)
        image.set_margin_top(14)
        image.set_margin_bottom(14)
        hero.pack_start(image, False, False, 0)
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        intro.set_margin_top(20)
        intro.set_margin_bottom(20)
        intro.set_margin_end(20)
        intro.pack_start(self.label(('YOUR TABLET, REIMAGINED', 'ВАШ ПЛАНШЕТ МОЖЕТ БОЛЬШЕ'), 'eyebrow'), False, False, 0)
        intro.pack_start(self.label(('Room for one more idea.', 'Место для новой идеи.'), 'headline'), False, False, 0)
        intro.pack_start(self.label(('Extend your Linux desktop over one USB cable.', 'Расширьте рабочий стол Linux одним USB-кабелем.'), 'muted', True), False, False, 0)
        hero.pack_start(intro, True, True, 0)
        body.pack_start(hero, False, False, 0)

        columns = Gtk.Box(spacing=18)
        body.pack_start(columns, False, False, 0)
        card, connect = self.card()
        card.set_size_request(245, -1)
        columns.pack_start(card, False, False, 0)
        device_header = Gtk.Box(spacing=8)
        device_header.pack_start(self.label(('Connected device', 'Устройство'), 'section-title'), True, True, 0)
        refresh = Gtk.Button.new_from_icon_name('view-refresh-symbolic', Gtk.IconSize.MENU)
        self.localized(refresh, 'tooltip-text', ('Refresh devices', 'Обновить список устройств'))
        refresh.connect('clicked', lambda _: self.refresh_devices())
        device_header.pack_end(refresh, False, False, 0)
        connect.pack_start(device_header, False, False, 0)
        self.device_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.device_waiting = self.label(self.tr('Checking USB devices…', 'Проверяем USB-устройства…'), 'muted', True)
        self.device_waiting.set_max_width_chars(23)
        self.device_rows.pack_start(self.device_waiting, False, False, 0)
        connect.pack_start(self.device_rows, False, False, 0)
        setup = self.localized(Gtk.Expander(), 'label', ('How to connect', 'Как подключить'))
        steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
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
            step.set_max_width_chars(23)
            row.pack_start(step, True, True, 0)
            steps_box.pack_start(row, False, False, 2)
        setup.add(steps_box)
        connect.pack_start(setup, False, False, 0)
        connect.pack_start(self.label(('USB only · no cloud · no account', 'По USB · без облака · без аккаунта'), 'tiny', True), False, False, 6)
        help_button = self.style(self.localized(Gtk.Button(), 'label', ('Connection help', 'Помощь с подключением')), 'link')
        help_button.connect('clicked', self.help)
        connect.pack_start(help_button, False, False, 0)
        self.downloads = DownloadPanel(self)
        connect.pack_start(self.downloads.button, False, False, 0)

        card, settings = self.card()
        columns.pack_start(card, True, True, 0)
        settings.pack_start(self.label(('Make it yours', 'Настройте под себя'), 'section-title'), False, False, 0)
        self.profile = self.combo([
            ('balanced', ('Balanced — everyday work', 'Баланс — на каждый день')),
            ('economy', ('Lightweight — older tablets', 'Лёгкий — для слабого планшета')),
            ('crisp', ('Crisp — detailed text', 'Чёткий — для работы с текстом')),
            ('custom', ('Custom settings', 'Свои настройки'))], self.detect_profile())
        settings.pack_start(self.profile, False, False, 0)
        grid = Gtk.Grid(column_spacing=14, row_spacing=7)
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
        if self.fps.get_active_id() is None:
            self.fps.append(str(self.settings.fps), f'{self.settings.fps} fps')
            self.fps.set_active_id(str(self.settings.fps))
        grid.attach(self.label(('Resolution', 'Разрешение'), 'muted'), 0,0,1,1)
        grid.attach(self.label(('Frame rate', 'Плавность'), 'muted'), 1,0,1,1)
        grid.attach(size_row, 0,1,1,1)
        grid.attach(self.fps, 1,1,1,1)
        self.controls.extend((self.width, self.height))
        self.quality = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 10,35,1)
        self.quality.set_value(self.settings.quality)
        self.quality.set_digits(0)
        self.quality.set_value_pos(Gtk.PositionType.RIGHT)
        self.localized(self.quality, 'tooltip-text', ('Lower QP means more detail and more USB traffic.', 'Меньше QP — больше деталей и трафика по USB.'))
        settings.pack_start(self.label(('Image quality · lower is sharper', 'Качество · меньше число — чётче'), 'muted'), False, False, 0)
        settings.pack_start(self.quality, False, False, 0)
        self.controls.append(self.quality)
        extra = self.localized(Gtk.Expander(), 'label', ('More options', 'Дополнительно'))
        extras = Gtk.Grid(column_spacing=12, row_spacing=10)
        extras.set_margin_top(12)
        self.transport = self.combo([('usb', 'USB'), ('adb', 'ADB'), ('auto', ('Automatic', 'Автоматически'))], self.settings.transport)
        self.capture = Gtk.SpinButton.new_with_range(5,60,1)
        self.capture.set_value(self.settings.capture_fps)
        self.capture.set_width_chars(2)
        self.capture.set_max_width_chars(2)
        self.controls.append(self.capture)
        self.language = self.combo([('auto',('System language','Язык системы')),('ru','Русский'),('en','English')],self.settings.language)
        # Language can change on both devices without restarting a stream.
        self.controls.remove(self.language)
        self.autoconnect = self.localized(Gtk.CheckButton(), 'label', ('Connect when opened', 'Подключать при запуске'))
        self.autoconnect.set_active(self.settings.auto_connect)
        for row,(title,control) in enumerate([
            (('Connection','Подключение'),self.transport),
            (('PC capture rate','Частота захвата ПК'),self.capture),
            (('Language','Язык'),self.language)]):
            extras.attach(self.label(title,'muted'),0,row,1,1)
            extras.attach(control,1,row,1,1)
        extras.attach(self.autoconnect,0,3,2,1)
        extras.attach(self.label(('Changes immediately on this PC.\nThe tablet follows when connected.',
                                 'На ПК язык меняется сразу.\nНа планшете — при подключении.'),'tiny'),0,4,2,1)
        extra.add(extras)
        settings.pack_start(extra, False, False, 0)
        self.profile.connect('changed', self.on_profile)
        for widget in (self.width,self.height,self.quality,self.capture):
            widget.connect('value-changed',self.on_settings_changed)
        for widget in (self.fps,self.transport):
            widget.connect('changed',self.on_settings_changed)
        self.language.connect('changed', self.on_language_changed)
        self.autoconnect.connect('toggled',self.on_settings_changed)

        footer, status_box = self.card(spacing=10)
        body.pack_start(footer, False, False, 0)
        line = Gtk.Box(spacing=12)
        self.badge = self.style(Gtk.Label(), 'badge')
        line.pack_start(self.badge,False,False,0)
        self.status = self.label('', None, True)
        self.set_status(('Your next screen is one click away.', 'Ещё один экран — в одном нажатии.'))
        line.pack_start(self.status,True,True,0)
        status_box.pack_start(line,False,False,0)
        actions = Gtk.Box(spacing=10)
        self.start_button = self.style(self.localized(Gtk.Button(), 'label', ('Connect tablet','Подключить планшет')), 'primary')
        self.start_button.connect('clicked',lambda _: self.start())
        self.stop_button = self.style(self.localized(Gtk.Button(), 'label', ('Stop','Остановить')), 'stop')
        self.stop_button.set_sensitive(False)
        self.stop_button.connect('clicked',lambda _: self.stop())
        actions.pack_start(self.start_button,True,True,0)
        actions.pack_start(self.stop_button,False,False,0)
        status_box.pack_start(actions,False,False,0)
        self.details = self.localized(Gtk.Expander(), 'label', ('Connection log','Журнал подключения'))
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(120)
        view = Gtk.TextView(editable=False,cursor_visible=False,wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log = view.get_buffer()
        log_scroll.add(view)
        self.details.add(log_scroll)
        body.pack_start(self.details,False,False,0)
        bottom = Gtk.Box()
        bottom.pack_start(self.label(('Closing this window stops its stream.','Закрытие окна останавливает запущенную здесь передачу.'),'tiny'),True,True,0)
        bottom.pack_end(self.label(f'v{__version__} · TanosX','muted'),False,False,0)
        body.pack_start(bottom,False,False,0)

    def refresh_devices(self):
        if self.closing:
            return False
        if not self.device_scan_running:
            self.device_scan_running = True
            threading.Thread(target=self.scan_devices, daemon=True).start()
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
            widget.set_max_width_chars(23)
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
                    text(device.tablet_ip or '')
                elif device.state == 'usb-no-address':
                    text(self.tr('USB detected · waiting for an address', 'USB найден · ожидаем адрес'))
                elif self.settings.transport == 'adb':
                    text(self.tr('USB detected · ADB selected', 'USB подключён · выбран режим ADB'))
                else:
                    text(self.tr('USB detected · enable USB tethering', 'USB подключён · включите USB-модем'))
                if device.interface:
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
            if hasattr(self,'status') and self.process is None and self.external_timer is None:
                self.set_status(('Your settings are saved. Ready to connect.', 'Настройки сохранены. Можно подключить планшет.'))
        except (OSError,ValueError) as error:
            if hasattr(self,'status'):
                self.set_status((f'Check the settings: {error}', f'Проверьте настройки: {error}'),'error')

    def set_status(self,text,kind='ready'):
        self.status_text, self.status_kind = text, kind
        self.status.set_text(self.tr(*text) if isinstance(text, tuple) else text)
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
                '--json-events','--language',resolve_language(self.settings.language),'--control-stdin'],
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
        self.closing=True
        self.window_geometry.close()
        self.notifications.close()
        self.downloads.close()
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
        dialog.run(); dialog.destroy()

    def help(self,*_):
        dialog=Gtk.MessageDialog(transient_for=self.window,modal=True,buttons=Gtk.ButtonsType.CLOSE,
            message_type=Gtk.MessageType.INFO,text=self.tr('One cable. Two screens.','Один кабель. Два экрана.'))
        dialog.format_secondary_text(self.tr(
            '1. Install the Panelyra Android APK.\n2. Connect a data-capable USB cable and enable USB tethering.\n3. Open Panelyra on Android, then click Connect tablet.\n\nArrange the new screen in GNOME Settings → Displays. USB debugging is only needed for the optional ADB mode.\n\nSupported desktop: GNOME on Wayland. No audio or tablet touch input yet.',
            '1. Установите APK Panelyra на Android.\n2. Подключите USB-кабель с передачей данных и включите USB-модем.\n3. Откройте Panelyra на Android и нажмите «Подключить планшет».\n\nРасположите новый экран в «Настройки GNOME → Дисплеи». Отладка USB нужна только для дополнительного режима ADB.\n\nПоддерживается GNOME Wayland. Звук и касания планшета пока не передаются.'))
        dialog.run(); dialog.destroy()


def main(auto_connect=None,language=None):
    Gdk.set_program_class(APP_ID)
    return Launcher(auto_connect=auto_connect,language=language).run([])


if __name__=='__main__':
    raise SystemExit(main())

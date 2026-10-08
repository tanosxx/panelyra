"""USB APK download settings; server and device discovery run outside GTK."""

import errno
from pathlib import Path
import threading

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, GLib, Gtk, Pango

from . import download, network


class DownloadPanel:
    def __init__(self, app):
        self.app = app
        self.window = None
        self.closed = self.rendering = False
        self.state = 'idle'
        self.reason = self.error = self.error_kind = None
        self.count = 0
        self.url = ''
        self.generation = 0
        self.scan_generation = 0
        self.scanning = False
        self.links = []
        self._lock = threading.Lock()
        self._owned = None
        self._cancel = threading.Event()
        self.button = Gtk.Button()
        self.button.connect('clicked', lambda _: self.present())
        self.render()

    def tr(self, english, russian):
        return self.app.tr(english, russian)

    @staticmethod
    def style(widget, *classes):
        for name in classes:
            widget.get_style_context().add_class(name)
        return widget

    @staticmethod
    def label(style=None):
        label = Gtk.Label(xalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(58)
        if style:
            label.get_style_context().add_class(style)
        return label

    def present(self):
        if self.closed:
            return
        if self.window is None:
            self.build_window()
        self.render()
        self.window.show_all()
        self.window.present()
        self.refresh_interfaces()

    def build_window(self):
        self.window = Gtk.Window(transient_for=self.app.window, destroy_with_parent=True)
        self.style(self.window, 'panelyra')
        if hasattr(self.app, 'style_theme'):
            self.app.style_theme(self.window)
        self.window.set_default_size(640, 740)
        self.window.connect('delete-event', self.hide)
        header = Gtk.HeaderBar(show_close_button=True)
        self.window.set_titlebar(header)
        self.header = header
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.window.add(scroll)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        content.set_border_width(22)
        scroll.add(content)
        self.intro = self.label('muted')
        content.pack_start(self.intro, False, False, 0)

        self.apk_label = self.label('section-title')
        content.pack_start(self.apk_label, False, False, 0)
        self.apk_entry = Gtk.Entry(editable=False, hexpand=True)
        self.apk_entry.set_can_focus(True)
        content.pack_start(self.apk_entry, False, False, 0)
        files = Gtk.Box(spacing=8)
        self.choose_button = Gtk.Button()
        self.choose_button.connect('clicked', self.choose_apk)
        self.auto_button = Gtk.Button()
        self.auto_button.connect('clicked', self.use_default_apk)
        files.pack_start(self.choose_button, True, True, 0)
        files.pack_start(self.auto_button, True, True, 0)
        content.pack_start(files, False, False, 0)

        grid = Gtk.Grid(column_spacing=14, row_spacing=10)
        self.interface_label = self.label()
        self.interfaces = Gtk.ComboBoxText(hexpand=True)
        self.interfaces.append('', '')
        self.interfaces.set_active_id('')
        self.refresh_button = Gtk.Button.new_from_icon_name('view-refresh-symbolic', Gtk.IconSize.BUTTON)
        self.refresh_button.connect('clicked', lambda _: self.refresh_interfaces())
        interface_row = Gtk.Box(spacing=8)
        interface_row.pack_start(self.interfaces, True, True, 0)
        interface_row.pack_start(self.refresh_button, False, False, 0)
        grid.attach(self.interface_label, 0, 0, 1, 1)
        grid.attach(interface_row, 1, 0, 1, 1)
        self.port_label = self.label()
        self.port = Gtk.SpinButton.new_with_range(1024, 65535, 1)
        self.port.set_numeric(True)
        self.port.set_digits(0)
        self.port.set_width_chars(7)
        self.port.set_value(self.app.settings.apk_port)
        self.port.set_hexpand(False)
        self.port.set_halign(Gtk.Align.START)
        grid.attach(self.port_label, 0, 1, 1, 1)
        grid.attach(self.port, 1, 1, 1, 1)
        self.duration_label = self.label()
        self.duration = Gtk.ComboBoxText()
        durations = sorted({300, 900, 1800, 3600, self.app.settings.apk_duration})
        for seconds in durations:
            self.duration.append(str(seconds), '')
        self.duration.set_active_id(str(self.app.settings.apk_duration))
        grid.attach(self.duration_label, 0, 2, 1, 1)
        grid.attach(self.duration, 1, 2, 1, 1)
        content.pack_start(grid, False, False, 0)
        self.interfaces.connect('changed', self.preferences_changed)
        self.port.connect('value-changed', self.preferences_changed)
        self.duration.connect('changed', self.preferences_changed)
        self.config_controls = [self.choose_button, self.auto_button, self.interfaces, self.port,
                                self.duration]

        actions = Gtk.Box(spacing=8)
        self.start_button = self.style(Gtk.Button(), 'primary')
        self.start_button.connect('clicked', lambda _: self.start())
        self.stop_button = self.style(Gtk.Button(), 'stop')
        self.stop_button.connect('clicked', lambda _: self.stop())
        actions.pack_start(self.start_button, True, True, 0)
        actions.pack_start(self.stop_button, False, False, 0)
        content.pack_start(actions, False, False, 0)
        self.status = self.label('device-name')
        self.detail = self.label('device-detail')
        content.pack_start(self.status, False, False, 0)
        content.pack_start(self.detail, False, False, 0)
        self.url_label = self.label()
        content.pack_start(self.url_label, False, False, 0)
        link_row = Gtk.Box(spacing=8)
        self.url_entry = Gtk.Entry(editable=False, hexpand=True)
        self.url_entry.connect('focus-in-event', lambda widget, _: widget.select_region(0, -1))
        self.copy_button = Gtk.Button()
        self.copy_button.connect('clicked', self.copy_url)
        link_row.pack_start(self.url_entry, True, True, 0)
        link_row.pack_start(self.copy_button, False, False, 0)
        content.pack_start(link_row, False, False, 0)
        self.instructions = self.label()
        self.privacy = self.label('tiny')
        content.pack_start(self.instructions, False, False, 0)
        content.pack_start(self.privacy, False, False, 0)
        self.render_interfaces()

    def hide(self, *_):
        self.window.hide()
        return True

    def render(self):
        if self.closed:
            return
        active = self.state in ('starting', 'running', 'stopping')
        self.button.set_label(self.tr('Android app', 'Приложение Android'))
        self.button.set_tooltip_text(self.tr('Download server is running', 'Сервер скачивания работает')
                                     if self.state == 'running' else
                                     self.tr('Install or update the app on your tablet',
                                             'Установить или обновить приложение на планшете'))
        if self.window is None:
            return
        self.rendering = True
        try:
            title = self.tr('Android app', 'Приложение Android')
            self.window.set_title(title + ' · Panelyra')
            self.header.set_title(title)
            self.header.set_subtitle(self.tr('Download over the USB cable', 'Скачивание по USB-кабелю'))
            self.intro.set_text(self.tr(
                'Start a temporary download server, then open its address in the tablet browser.',
                'Запустите временный сервер скачивания, затем откройте его адрес в браузере планшета.'))
            self.apk_label.set_text(self.tr('Android installation file', 'Установочный файл Android'))
            path = self.app.settings.apk_path or str(download.default_apk())
            self.apk_entry.set_text(path)
            self.apk_entry.set_tooltip_text(path)
            self.choose_button.set_label(self.tr('Choose APK…', 'Выбрать APK…'))
            self.auto_button.set_label(self.tr('Use bundled APK', 'APK из комплекта'))
            self.interface_label.set_text(self.tr('USB connection', 'USB-подключение'))
            self.port_label.set_text(self.tr('Port', 'Порт'))
            self.duration_label.set_text(self.tr('Stop automatically', 'Остановить через'))
            self.refresh_button.set_tooltip_text(self.tr('Refresh USB connections', 'Обновить USB-подключения'))
            model = self.interfaces.get_model()
            first = model.get_iter_first()
            if first is not None:
                model.set_value(first, 0, self.tr('Automatic', 'Автоматически'))
            for row in self.duration.get_model():
                seconds = int(row[1])
                row[0] = (self.tr(f'{seconds // 60} minutes', f'{seconds // 60} мин.')
                          if seconds % 60 == 0 else self.tr(f'{seconds} seconds', f'{seconds} сек.'))
            self.start_button.set_label(self.tr('Start server', 'Запустить сервер'))
            self.stop_button.set_label(self.tr('Stop server', 'Остановить сервер'))
            self.start_button.set_sensitive(not active)
            self.stop_button.set_sensitive(self.state in ('starting', 'running'))
            for widget in self.config_controls:
                widget.set_sensitive(not active)
            self.refresh_button.set_sensitive(not active and not self.scanning)
            status = {
                'starting': ('Starting server…', 'Запускаем сервер…'),
                'running': ('Ready to download on the tablet', 'Можно скачивать на планшете'),
                'stopping': ('Stopping server…', 'Останавливаем сервер…'),
                'idle': ('Server is off', 'Сервер выключен'),
            }[self.state]
            self.status.set_text(self.tr(*status))
            if self.error:
                explanations = {
                    'port': ('This port is already in use. Choose another port or stop the other server.',
                             'Порт уже занят. Выберите другой порт или остановите другой сервер.'),
                    'save': ('Could not save the download settings.', 'Не удалось сохранить настройки скачивания.'),
                    'scan': ('Could not refresh USB connections. Check the cable and USB tethering.',
                             'Не удалось обновить подключения. Проверьте кабель и USB-модем.'),
                    'start': ('Could not start the server. Check the APK, cable and USB tethering.',
                              'Не удалось запустить сервер. Проверьте APK, кабель и USB-модем.'),
                    'server': ('The download server stopped. Check the USB connection and start it again.',
                               'Сервер скачивания остановился. Проверьте USB-подключение и запустите его снова.'),
                }
                help_text = self.tr(*explanations.get(self.error_kind, explanations['start']))
                self.detail.set_text(help_text + '\n' + self.error)
            elif self.reason == 'expired':
                self.detail.set_text(self.tr('The selected time has elapsed. Start again if needed.',
                                            'Выбранное время истекло. При необходимости запустите снова.'))
            elif self.reason == 'disconnected':
                self.detail.set_text(self.tr('The USB connection changed. Reconnect and start the server again.',
                                            'USB-подключение изменилось. Подключите планшет и запустите сервер снова.'))
            elif self.count:
                self.detail.set_text(self.tr(
                    f'Completed APK downloads: {self.count}. Open the downloaded file on Android to install it.',
                    f'Завершено скачиваний APK: {self.count}. Для установки откройте скачанный файл на Android.'))
            else:
                self.detail.set_text(self.tr('The server stops when you close Panelyra.',
                                            'При закрытии Panelyra сервер остановится.'))
            self.url_label.set_text(self.tr('Open this address on the tablet', 'Откройте этот адрес на планшете'))
            self.url_entry.set_text(self.url)
            self.url_entry.set_placeholder_text(self.tr('Address appears after starting', 'Адрес появится после запуска'))
            self.copy_button.set_label(self.tr('Copy address', 'Копировать адрес'))
            self.copy_button.set_sensitive(self.state == 'running' and bool(self.url))
            self.instructions.set_text(self.tr(
                '1. Connect the USB cable and enable USB tethering on Android.\n'
                '2. Open the address above in the tablet browser and download the APK.\n'
                '3. Open the file and install it over the existing app. Allow installation from this browser if Android asks.\n'
                '4. Open Panelyra on the tablet and connect from the PC.',
                '1. Подключите USB-кабель и включите USB-модем на Android.\n'
                '2. Откройте адрес выше в браузере планшета и скачайте APK.\n'
                '3. Откройте файл и установите поверх приложения. Если Android попросит, разрешите установку из этого браузера.\n'
                '4. Откройте Panelyra на планшете и подключитесь с ПК.'))
            self.privacy.set_text(self.tr(
                'Only the selected APK is shared with the USB tablet. Closing this window keeps the server running until its timer ends.',
                'Планшету по USB доступен только выбранный APK. Закрытие этого окна оставляет сервер работать до окончания таймера.'))
        finally:
            self.rendering = False

    def render_interfaces(self):
        self.rendering = True
        try:
            selected = self.app.settings.apk_interface
            self.interfaces.remove_all()
            self.interfaces.append('', self.tr('Automatic', 'Автоматически'))
            seen = set()
            for link in self.links:
                if link.interface not in seen:
                    seen.add(link.interface)
                    self.interfaces.append(link.interface, f'{link.interface} · {link.local_ip}')
            if selected and selected not in seen:
                self.interfaces.append(selected, selected)
            self.interfaces.set_active_id(selected)
        finally:
            self.rendering = False

    def refresh_interfaces(self):
        if self.closed or self.scanning or self.state != 'idle':
            return
        self.scanning = True
        self.scan_generation += 1
        generation = self.scan_generation
        self.render()

        def work():
            try:
                links, error = network.discover_links(), None
            except (OSError, RuntimeError, ValueError) as exc:
                links, error = [], str(exc)
            GLib.idle_add(self._on_interfaces, generation, links, error)

        threading.Thread(target=work, name='panelyra-apk-usb', daemon=True).start()

    def _on_interfaces(self, generation, links, error):
        if self.closed or generation != self.scan_generation:
            return False
        self.scanning = False
        self.links = links
        self.render_interfaces()
        if self.state == 'idle':
            if error:
                self.error, self.error_kind = error, 'scan'
            elif self.error_kind == 'scan':
                self.error = self.error_kind = None
        self.render()
        return False

    def preferences_changed(self, *_):
        if self.rendering or self.closed:
            return
        try:
            self.app.save_apk_settings(apk_port=self.port.get_value_as_int(),
                                       apk_duration=int(self.duration.get_active_id()),
                                       apk_interface=self.interfaces.get_active_id() or '')
            if self.error_kind == 'save':
                self.error = self.error_kind = None
                self.render()
        except (OSError, ValueError, RuntimeError) as exc:
            self.error, self.error_kind = str(exc), 'save'
            self.render()

    def choose_apk(self, *_):
        chooser = Gtk.FileChooserDialog(title=self.tr('Choose Android APK', 'Выберите APK для Android'),
                                       transient_for=self.window, action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons(self.tr('Cancel', 'Отмена'), Gtk.ResponseType.CANCEL,
                            self.tr('Choose', 'Выбрать'), Gtk.ResponseType.OK)
        apk_filter = Gtk.FileFilter()
        apk_filter.set_name('Android APK')
        apk_filter.add_pattern('*.apk')
        chooser.add_filter(apk_filter)
        path = self.app.settings.apk_path or str(download.default_apk())
        if Path(path).is_file():
            chooser.set_filename(path)
        if chooser.run() == Gtk.ResponseType.OK:
            self.set_apk_path(chooser.get_filename())
        chooser.destroy()

    def use_default_apk(self, *_):
        self.set_apk_path('')

    def set_apk_path(self, path):
        try:
            self.app.save_apk_settings(apk_path=path)
            if self.error_kind == 'save':
                self.error = self.error_kind = None
        except (OSError, ValueError, RuntimeError) as exc:
            self.error, self.error_kind = str(exc), 'save'
        self.render()

    def copy_url(self, *_):
        if self.state == 'running' and self.url:
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self.url, -1)

    def start(self):
        if self.closed or self.state != 'idle':
            return
        if self.window is None:
            self.build_window()
        self.preferences_changed()
        if self.error_kind == 'save':
            return
        settings = self.app.settings
        apk = Path(settings.apk_path) if settings.apk_path else download.default_apk()
        language = 'ru' if self.app.russian else 'en'
        self.generation += 1
        generation = self.generation
        cancel = threading.Event()
        with self._lock:
            self._cancel = cancel
            self._owned = None
        self.state = 'starting'
        self.count = 0
        self.url = ''
        self.error = self.error_kind = self.reason = None
        self.render()

        def work():
            server = None
            try:
                link = network.select_link(settings.apk_interface or None)
                if cancel.is_set():
                    return
                callback = lambda name, **details: GLib.idle_add(self._event, generation, name, details)
                server = download.DownloadServer(apk, link, port=settings.apk_port,
                                                 duration=settings.apk_duration,
                                                 language=language, on_event=callback)
                with self._lock:
                    if cancel.is_set():
                        return
                    self._owned = server
                server.start()
                if cancel.is_set():
                    server.stop()
                    return
                GLib.idle_add(self._started, generation, server)
            except (OSError, RuntimeError, ValueError) as exc:
                if server is not None:
                    server.stop()
                kind = 'port' if isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE else 'start'
                GLib.idle_add(self._failed, generation, str(exc), kind)

        threading.Thread(target=work, name='panelyra-apk-start', daemon=True).start()

    def _started(self, generation, server):
        if self.closed or generation != self.generation or self.state != 'starting':
            return False
        self.url = server.url
        self.state = 'running' if server.running else 'idle'
        self.render()
        return False

    def _failed(self, generation, error, kind):
        if self.closed or generation != self.generation:
            return False
        self.state = 'idle'
        self.error, self.error_kind = error, kind
        self.render()
        return False

    def _event(self, generation, name, details):
        if self.closed or generation != self.generation:
            return False
        if name == 'download':
            self.count += 1
        elif name == 'stopped':
            self.state = 'idle'
            self.reason = details.get('reason')
        elif name == 'error':
            self.error, self.error_kind = details.get('message', ''), 'server'
        self.render()
        return False

    def stop(self):
        if self.closed or self.state not in ('starting', 'running'):
            return
        self.generation += 1
        generation = self.generation
        with self._lock:
            self._cancel.set()
            server, self._owned = self._owned, None
        self.state = 'stopping'
        self.render()

        def work():
            if server is not None:
                server.stop()
            GLib.idle_add(self._stopped, generation)

        threading.Thread(target=work, name='panelyra-apk-stop', daemon=True).start()

    def _stopped(self, generation):
        if not self.closed and generation == self.generation:
            self.state, self.reason = 'idle', 'requested'
            self.render()
        return False

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.generation += 1
        self.scan_generation += 1
        with self._lock:
            self._cancel.set()
            server, self._owned = self._owned, None
        if server is not None:
            threading.Thread(target=server.stop, name='panelyra-apk-close', daemon=True).start()
        if self.window is not None:
            self.window.destroy()

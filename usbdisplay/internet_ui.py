"""Tablet Internet control; NetworkManager work never runs on the GTK thread."""

import threading

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk, Pango

from . import internet


ERRORS = {
    'no-usb': ('Enable USB tethering on your tablet.',
               'Включите USB-модем на планшете.'),
    'ambiguous': ('Keep only the tablet you want to change connected.',
                  'Оставьте подключённым только нужный планшет.'),
    'network-manager': ('NetworkManager is unavailable.',
                        'NetworkManager недоступен.'),
    'changed': ('The USB connection changed. Try again.',
                'USB-подключение изменилось. Попробуйте ещё раз.'),
    'permission': ('NetworkManager did not allow this change.',
                   'NetworkManager не разрешил изменить сеть.'),
    'restore-conflict': ('Network settings changed elsewhere. Reconnect USB to reset them.',
                         'Настройки сети изменены другой программой. Переподключите USB для сброса.'),
    'unsupported': ('This USB connection cannot be changed without reconnecting it.',
                    'Это USB-подключение нельзя изменить без переподключения.'),
    'storage': ('Could not save the settings needed to restore tablet Internet.',
                'Не удалось сохранить настройки для возврата интернета через планшет.'),
}


class InternetPanel:
    def __init__(self, app):
        self.app = app
        self.controller = internet.InternetController()
        self.closed = self.working = self.mutating = False
        self.generation = 0
        self.state = None
        self.reason = self.error = self.diagnostic = None
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        row = self.row = Gtk.Box(spacing=16)
        summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.title = self.label('device-name')
        summary.pack_start(self.title, False, False, 0)
        self.status = self.label('device-detail')
        self.status.set_line_wrap(False)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        summary.pack_start(self.status, False, False, 0)
        row.pack_start(summary, True, True, 0)
        self.button = Gtk.Button()
        self.button.set_valign(Gtk.Align.CENTER)
        self.button.get_style_context().add_class('internet-toggle')
        self.button.connect('clicked', self.toggle)
        row.pack_end(self.button, False, False, 0)
        self.widget.pack_start(row, False, False, 0)
        self.detail = self.label('tiny')
        self.detail.get_style_context().add_class('muted')
        self.widget.pack_start(self.detail, False, False, 0)
        self.render()

    def set_compact(self, compact, horizontal=False):
        """Fit the shared control into a theme card without changing its state."""
        self.widget.set_spacing(4 if compact else 7)
        self.row.set_spacing(8 if compact else 16)
        self.widget.set_orientation(Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL)
        self.widget.set_child_packing(self.row, horizontal, horizontal, 0, Gtk.PackType.START)
        self.detail.set_margin_start(12 if horizontal else 0)
        self.detail.set_valign(Gtk.Align.CENTER)
        self.title.set_max_width_chars(20 if compact and not horizontal else 58)

    @staticmethod
    def label(style):
        label = Gtk.Label(xalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(58)
        label.get_style_context().add_class(style)
        return label

    def tr(self, english, russian):
        return self.app.tr(english, russian)

    def render(self):
        if self.closed:
            return
        self.title.set_text(self.tr('Internet via tablet', 'Интернет через планшет'))
        if self.mutating:
            status = self.tr('Changing…', 'Меняем…')
            action = self.tr('Applying…', 'Применяем…')
        elif self.state is not None:
            status = self.tr('Allowed', 'Разрешён') if self.state.enabled else self.tr('Disabled', 'Отключён')
            action = self.tr('Turn off', 'Отключить') if self.state.enabled else self.tr('Turn on', 'Включить')
        else:
            status = self.tr('Unavailable', 'Недоступен') if self.reason else self.tr('Checking…', 'Проверяем…')
            action = self.tr('Turn on', 'Включить')
        self.status.set_text(status)
        self.button.set_label(action)
        self.button.set_sensitive(self.state is not None and not self.working)
        problem = self.error or self.reason
        if problem:
            text = self.tr(*ERRORS.get(problem, (
                'Could not change or check tablet Internet. Try again.',
                'Не удалось изменить или проверить интернет через планшет. Попробуйте ещё раз.')))
        else:
            text = self.tr('Until USB is reconnected.\nYour second screen keeps working.',
                           'До переподключения USB.\nВторой экран продолжит работать.')
        self.detail.set_text(text)
        self.detail.set_tooltip_text(self.diagnostic)
        self.button.set_tooltip_text(self.tr(
            'Allow or block Internet routes and DNS from this USB connection. Other networks stay available.',
            'Разрешить или отключить маршруты интернета и DNS этого USB-подключения. Другие сети останутся доступны.'))
        self.status.set_tooltip_text(self.tr(
            'Allowed means the tablet may provide Internet. The system chooses which network to use.',
            '«Разрешён» означает, что планшет может раздавать интернет. Система выбирает, какую сеть использовать.'))
        if hasattr(self.app, 'update_overview'):
            self.app.update_overview()

    @staticmethod
    def failure(error):
        return getattr(error, 'code', 'failed'), str(error)

    def refresh(self):
        if self.closed or self.working:
            return
        self.working = True
        generation = self.generation
        self.render()

        def work():
            try:
                state = self.controller.inspect()
                reason, diagnostic = None, None
            except Exception as failure:
                state = None
                reason, diagnostic = self.failure(failure)
            GLib.idle_add(self.on_refresh, generation, state, reason, diagnostic)

        threading.Thread(target=work, daemon=True).start()

    def on_refresh(self, generation, state, reason, diagnostic):
        if self.closed or generation != self.generation:
            return False
        previous = self.state
        self.state, self.reason = state, reason
        self.working = False
        if reason in ('no-usb', 'ambiguous'):
            self.error = None
        if reason:
            self.diagnostic = diagnostic
        elif self.error is None:
            self.diagnostic = None
        # A reconnect must not leave an error from the old connection visible.
        if previous is not None and state is not None and previous.active_path != state.active_path:
            self.error = self.diagnostic = None
        self.render()
        return False

    def toggle(self, *_):
        if self.closed or self.working or self.state is None:
            return
        self.working = self.mutating = True
        self.error = self.diagnostic = None
        state, enabled, generation = self.state, not self.state.enabled, self.generation
        self.render()

        def work():
            try:
                current = self.controller.set_enabled(state, enabled)
                error, diagnostic, reason = None, None, None
            except Exception as failure:
                error, diagnostic = self.failure(failure)
                # Re-read after a failed operation; never offer a toggle based
                # on a stale state or assume that rollback has succeeded.
                try:
                    current, reason = self.controller.inspect(), None
                except Exception as scan_failure:
                    current = None
                    reason, _ = self.failure(scan_failure)
            GLib.idle_add(self.on_changed, generation, current, error, diagnostic, reason)

        threading.Thread(target=work, daemon=True).start()

    def on_changed(self, generation, state, error, diagnostic, reason):
        if self.closed or generation != self.generation:
            return False
        self.working = self.mutating = False
        self.state, self.error, self.diagnostic, self.reason = state, error, diagnostic, reason
        if error:
            self.app.append_log(self.tr('Tablet Internet: ', 'Интернет через планшет: ') + diagnostic)
        elif state is not None:
            self.app.append_log(self.tr('Internet via tablet allowed.', 'Интернет через планшет разрешён.')
                                if state.enabled else
                                self.tr('Internet via tablet disabled.', 'Интернет через планшет отключён.'))
        self.render()
        return False

    def close(self):
        self.closed = True
        self.generation += 1

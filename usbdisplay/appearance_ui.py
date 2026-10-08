"""Appearance chooser with temporary, reversible previews of the whole window."""

from types import SimpleNamespace

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Pango

from .themes import THEMES
from .theme_preview import ThemePreview
from .lock_preview import LockPreview


class AppearancePanel:
    """Keep a preview separate from the settings until the user applies it."""

    def __init__(self, app):
        self.app = app
        self.closed = False
        self.selected_theme = self.saved_theme()
        self.message = None
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.widget.get_style_context().add_class('appearance-panel')

        introduction = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.title = self.label('section-title')
        self.explanation = self.label('muted')
        introduction.pack_start(self.title, False, False, 0)
        introduction.pack_start(self.explanation, False, False, 0)
        self.widget.pack_start(introduction, False, False, 0)

        # Six compact choices stay beside one preview, so comparing designs
        # never requires scrolling back from a long gallery to the illustration.
        gallery = Gtk.Box(spacing=12)
        self.choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.choices.set_size_request(210, -1)
        self.choices.set_valign(Gtk.Align.START)
        gallery.pack_start(self.choices, False, False, 0)
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail.set_hexpand(True)
        gallery.pack_start(detail, True, True, 0)
        self.preview_stack = Gtk.Stack()
        self.preview_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.interface_preview = ThemePreview(self.selected_theme)
        self.interface_preview.set_size_request(200, 172)
        self.lock_preview = LockPreview(self.selected_theme)
        self.lock_preview.set_size_request(200, 172)
        self.preview_stack.add_named(self.interface_preview, 'interface')
        self.preview_stack.add_named(self.lock_preview, 'lock')
        self.preview_stack.show_all()
        self.preview_stack.set_visible_child_name('interface')
        self.preview_switcher = Gtk.StackSwitcher(stack=self.preview_stack)
        self.preview_switcher.get_style_context().add_class('appearance-preview-switcher')
        self.preview_switcher.set_halign(Gtk.Align.CENTER)
        detail.pack_start(self.preview_switcher, False, False, 0)
        detail.pack_start(self.preview_stack, False, False, 0)
        self.description = self.label('theme-description')
        self.description.set_max_width_chars(34)
        detail.pack_start(self.description, False, False, 0)
        self.lock_explanation = self.label('muted')
        self.lock_explanation.set_max_width_chars(34)
        self.lock_explanation.set_no_show_all(True)
        detail.pack_start(self.lock_explanation, False, False, 0)
        self.preview_stack.connect('notify::visible-child-name', self.on_preview_changed)
        self.widget.pack_start(gallery, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.actions = actions
        actions.get_style_context().add_class('appearance-actions')
        self.status = self.label('appearance-status')
        actions.pack_start(self.status, False, False, 0)
        buttons = Gtk.Box(spacing=8)
        self.apply_button = Gtk.Button()
        self.apply_button.get_style_context().add_class('primary')
        self.apply_button.connect('clicked', self.apply)
        self.cancel_button = Gtk.Button()
        self.cancel_button.connect('clicked', self.cancel)
        buttons.pack_start(self.apply_button, False, False, 0)
        buttons.pack_start(self.cancel_button, False, False, 0)
        actions.pack_start(buttons, False, False, 0)
        self.widget.pack_start(actions, False, False, 0)

        self.tiles = {}
        for theme_id in THEMES:
            self.add_theme(theme_id)
        self.render()

    @staticmethod
    def label(style):
        label = Gtk.Label(xalign=0)
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(50)
        label.get_style_context().add_class(style)
        return label

    def saved_theme(self):
        theme_id = getattr(self.app.settings, 'theme', 'classic')
        return theme_id if theme_id in THEMES else 'classic'

    def tr(self, english, russian):
        return self.app.tr(english, russian)

    def add_theme(self, theme_id):
        button = Gtk.Button()
        button.set_hexpand(True)
        button.get_style_context().add_class('theme-choice')
        button.get_style_context().add_class('appearance-choice-row')
        button.connect('clicked', lambda *_: self.select(theme_id))
        content = Gtk.Box(spacing=8)
        swatch = Gtk.DrawingArea()
        swatch.set_size_request(18, 20)
        swatch.connect('draw', lambda widget, ctx: self.draw_swatch(widget, ctx, theme_id))
        content.pack_start(swatch, False, False, 0)
        name = self.label('theme-name')
        name.set_line_wrap(False)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(24)
        content.pack_start(name, True, True, 0)
        # The full state is announced in the accessible name and tooltip. A
        # small check identifies the saved choice without another text row.
        badge = self.label('theme-state')
        marker = Gtk.Label()
        marker.set_width_chars(1)
        content.pack_end(marker, False, False, 0)
        button.add(content)
        self.choices.pack_start(button, False, False, 0)
        self.tiles[theme_id] = SimpleNamespace(
            button=button, name=name, badge=badge, marker=marker)

    @staticmethod
    def draw_swatch(widget, ctx, theme_id):
        colors = THEMES[theme_id].colors
        x, y = (widget.get_allocated_width() - 16) / 2, (widget.get_allocated_height() - 18) / 2
        for index, color in enumerate((colors['bg'], colors['accent'], colors['good'])):
            ctx.set_source_rgb(*(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)))
            ctx.rectangle(x, y + index * 6, 16, 6)
            ctx.fill()
        return False

    def render(self):
        if self.closed:
            return
        saved = self.saved_theme()
        pending = self.selected_theme != saved
        self.title.set_text(self.tr('Make Panelyra yours', 'Panelyra в вашем стиле'))
        self.explanation.set_text(self.tr(
            'Choose a design, then Apply. Leaving this section cancels an unsaved preview.',
            'Выберите дизайн и нажмите «Применить». При выходе несохранённый предпросмотр отменится.'))
        self.apply_button.set_label(self.tr('Apply', 'Применить'))
        self.cancel_button.set_label(self.tr('Cancel preview', 'Отменить предпросмотр'))
        self.apply_button.set_sensitive(pending)
        self.cancel_button.set_sensitive(pending or self.message == 'error')
        self.status.get_style_context().remove_class('error')
        if self.message == 'error':
            self.status.set_text(self.tr(
                'Could not save the style. Try again or cancel the preview.',
                'Не удалось сохранить стиль. Попробуйте ещё раз или отмените предпросмотр.'))
            self.status.get_style_context().add_class('error')
        elif pending:
            self.status.set_text(self.tr('Preview · Not saved', 'Предпросмотр · Не сохранено'))
        elif self.message == 'saved':
            self.status.set_text(self.tr('Style saved', 'Стиль сохранён'))
        else:
            self.status.set_text(self.tr('Current style is saved', 'Текущий стиль сохранён'))
        language = self.tr('en', 'ru')
        self.preview_stack.child_set_property(self.interface_preview, 'title', self.tr('Interface', 'Интерфейс'))
        self.preview_stack.child_set_property(self.lock_preview, 'title', self.tr('Lock screen', 'Заставка'))
        self.description.set_text(self.tr(*THEMES[self.selected_theme].description))
        self.lock_explanation.set_text(self.tr(
            'Apply to use this animation at the next computer lock.',
            'После применения появится при следующей блокировке ПК.'))
        self.interface_preview.set_theme(self.selected_theme)
        self.interface_preview.set_language(language)
        self.lock_preview.set_style(self.selected_theme, language)
        self.on_preview_changed()
        for theme_id, tile in self.tiles.items():
            theme = THEMES[theme_id]
            name, description = self.tr(*theme.name), self.tr(*theme.description)
            tile.name.set_text(name)
            context = tile.button.get_style_context()
            context.remove_class('selected')
            context.remove_class('active-theme')
            if theme_id == self.selected_theme:
                context.add_class('selected')
                context.add_class('active-theme')
            if theme_id == self.selected_theme and pending:
                badge = self.tr('Preview', 'Предпросмотр')
            elif theme_id == saved:
                badge = self.tr('Current style', 'Текущий стиль')
            else:
                badge = ''
            tile.badge.set_text(badge)
            tile.marker.set_text('✓' if theme_id == saved else '')
            tile.button.set_tooltip_text(name + '\n' + description + ('\n' + badge if badge else ''))
            tile.button.get_accessible().set_name(name + (': ' + badge if badge else ''))

    def on_preview_changed(self, *_):
        animated = self.preview_stack.get_visible_child_name() == 'lock'
        self.lock_preview.set_active(animated)
        self.description.set_visible(not animated)
        self.description.set_no_show_all(animated)
        self.lock_explanation.set_visible(animated)

    def select(self, theme_id):
        if self.closed or theme_id not in THEMES or theme_id == self.selected_theme:
            return
        self.selected_theme = theme_id
        self.message = None
        self.app.preview_theme(theme_id)
        self.render()

    def apply(self, *_):
        if self.closed or self.selected_theme == self.saved_theme():
            return
        applied = self.app.commit_theme(self.selected_theme)
        self.message = ('saved' if applied and self.saved_theme() == self.selected_theme
                        else 'error')
        self.render()

    def cancel(self, *_):
        if self.closed:
            return
        saved = self.saved_theme()
        if self.selected_theme != saved:
            self.selected_theme = saved
            self.app.preview_theme(saved)
        self.message = None
        self.render()

    def close(self):
        if not self.closed:
            self.cancel()
            self.closed = True
            self.lock_preview.close()

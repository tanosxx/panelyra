"""A local preview of the real lock scene, animated only while it is visible."""

from collections import OrderedDict
import time

import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import GLib, Gtk, Pango, PangoCairo

from .lockscreen import LockScene
from .themes import get_theme


class LockPreview(Gtk.DrawingArea):
    """Render the sender's scene without touching the desktop or the stream."""

    WIDTH, HEIGHT = 800, 500
    INTERVAL_MS = 100  # Ten frames per second is sufficient for a small preview.

    def __init__(self, theme='classic', language='en'):
        super().__init__()
        self.theme = get_theme(theme).id
        self.language = 'ru' if language == 'ru' else 'en'
        self.enabled = False
        self.closed = False
        self.source_id = None
        self.failed = False
        self.started = time.monotonic()
        self.scenes = OrderedDict()
        self.set_size_request(240, 230)
        self.set_hexpand(True)
        self.get_style_context().add_class('lock-preview')
        self.get_accessible().set_name('Lock screen preview')
        self.connect('draw', self.on_draw)
        self.connect('map', self.on_map)
        self.connect('unmap', self.on_unmap)
        self.connect('destroy', self.close)

    def set_style(self, theme, language):
        """An unsaved choice affects this illustration only."""
        theme = get_theme(theme).id
        language = 'ru' if language == 'ru' else 'en'
        if (theme, language) != (self.theme, self.language):
            self.theme, self.language = theme, language
            self.failed = False
            self.queue_draw()
            self.start_timer()
        self.get_accessible().set_name(
            'Предпросмотр заставки' if language == 'ru' else 'Lock screen preview')

    def set_active(self, active):
        self.enabled = bool(active)
        if self.enabled:
            self.start_timer()
            self.queue_draw()
        else:
            self.stop_timer()

    def start_timer(self):
        if (not self.closed and self.enabled and self.get_mapped()
                and not self.failed and self.source_id is None):
            self.source_id = GLib.timeout_add(self.INTERVAL_MS, self.tick)

    def stop_timer(self):
        if self.source_id is not None:
            GLib.source_remove(self.source_id)
            self.source_id = None

    def tick(self):
        if self.closed or not self.enabled or not self.get_mapped() or self.failed:
            self.source_id = None
            return GLib.SOURCE_REMOVE
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def on_map(self, *_):
        self.start_timer()

    def on_unmap(self, *_):
        self.stop_timer()

    def close(self, *_):
        self.closed = True
        self.stop_timer()
        self.scenes.clear()

    def scene(self):
        key = self.theme, self.language
        if key not in self.scenes:
            self.scenes[key] = LockScene(self.WIDTH, self.HEIGHT,
                                         language=self.language, theme=self.theme)
            # Cache the current and most recent choices, including backgrounds,
            # without retaining a separate large bitmap for every UI language.
            if len(self.scenes) > 3:
                self.scenes.popitem(last=False)
        self.scenes.move_to_end(key)
        return self.scenes[key]

    def fallback(self, ctx, width, height):
        colors = get_theme(self.theme).colors
        ctx.set_source_rgb(*(int(colors['surface'][i:i + 2], 16) / 255
                             for i in (1, 3, 5)))
        ctx.paint()
        text = ('Предпросмотр временно недоступен' if self.language == 'ru'
                else 'Preview is temporarily unavailable')
        layout = PangoCairo.create_layout(ctx)
        layout.set_text(text, -1)
        layout.set_font_description(Pango.FontDescription('Sans 11'))
        layout.set_width(max(1, int(width - 32)) * Pango.SCALE)
        layout.set_alignment(Pango.Alignment.CENTER)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        _text_width, text_height = layout.get_pixel_size()
        ctx.set_source_rgb(*(int(colors['text'][i:i + 2], 16) / 255
                             for i in (1, 3, 5)))
        ctx.move_to(16, (height - text_height) / 2)
        PangoCairo.show_layout(ctx, layout)

    def on_draw(self, _widget, ctx):
        if self.closed:
            return False
        width, height = self.get_allocated_width(), self.get_allocated_height()
        if self.failed:
            self.fallback(ctx, width, height)
            return False
        ctx.save()
        try:
            colors = get_theme(self.theme).colors
            ctx.set_source_rgb(*(int(colors['bg'][i:i + 2], 16) / 255
                                 for i in (1, 3, 5)))
            ctx.paint()
            scale = min(width / self.WIDTH, height / self.HEIGHT)
            ctx.translate((width - self.WIDTH * scale) / 2,
                          (height - self.HEIGHT * scale) / 2)
            ctx.scale(scale, scale)
            ctx.rectangle(0, 0, self.WIDTH, self.HEIGHT)
            ctx.clip()
            # No fixed test clock: the real scene uses the current local time.
            self.scene().render(ctx, time.monotonic() - self.started)
        except Exception:
            self.failed = True
            self.stop_timer()
        finally:
            ctx.restore()
        if self.failed:
            self.fallback(ctx, width, height)
        return False

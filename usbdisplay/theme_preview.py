"""Small, deterministic Cairo illustrations of the available application designs.

The previews use the same palette and layout identifiers as the live interface.
They contain illustrative device data, never screenshots of the user's screen.
"""

import math

import cairo
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Pango, PangoCairo

from .themes import get_theme, mix


def _color(cr, value, opacity=1):
    cr.set_source_rgba(*(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)), opacity)


def _path(cr, x, y, width, height, radius=0):
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    for cx, cy, start in ((x + width - radius, y + radius, -math.pi / 2),
                          (x + width - radius, y + height - radius, 0),
                          (x + radius, y + height - radius, math.pi / 2),
                          (x + radius, y + radius, math.pi)):
        cr.arc(cx, cy, radius, start, start + math.pi / 2)
    cr.close_path()


def _rect(cr, x, y, width, height, fill, radius=0, stroke=None, line_width=1):
    _path(cr, x, y, width, height, radius)
    _color(cr, fill)
    cr.fill_preserve()
    if stroke:
        cr.set_line_width(line_width)
        _color(cr, stroke)
        cr.stroke()
    else:
        cr.new_path()


def _text(cr, value, x, y, color, size=12, weight=False, width=None, font='Sans'):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription()
    desc.set_family(font)
    desc.set_absolute_size(size * Pango.SCALE)
    desc.set_weight(Pango.Weight.BOLD if weight else Pango.Weight.NORMAL)
    layout.set_font_description(desc)
    layout.set_text(value, -1)
    if width is not None:
        layout.set_width(int(width * Pango.SCALE))
        layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_single_paragraph_mode(True)
    _color(cr, color)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def _line(cr, x, y, width, color, height=1):
    _rect(cr, x, y, width, height, color)


def _gradient(cr, x, y, width, height, stops):
    gradient = cairo.LinearGradient(x, y, x + width, y + height)
    for position, color in stops:
        gradient.add_color_stop_rgb(position, *(int(color[i:i + 2], 16) / 255
                                                for i in (1, 3, 5)))
    cr.set_source(gradient)


def _wallpaper(cr, theme, x, y, width, height):
    """Original abstract wallpaper, clipped to the device's visible screen."""
    cr.save()
    _path(cr, x, y, width, height, 3)
    cr.clip()
    scale = max(width / 200, height / 150)
    cr.translate(x + (width - 200 * scale) / 2,
                 y + (height - 150 * scale) / 2)
    cr.scale(scale, scale)
    if theme.id == 'editorial':
        _gradient(cr, 0, 0, 200, 150, ((0, '#fff1d8'), (1, '#e9decb')))
        cr.paint()
        _color(cr, '#efd3b5')
        cr.arc(133, 45, 19, 0, 2 * math.pi)
        cr.fill()
        for color, level, bulge in (('#a5ac98', 93, -25), ('#858f7e', 110, 8),
                                    ('#647567', 143, -12)):
            _color(cr, color)
            cr.move_to(-10, level)
            cr.curve_to(50, level + bulge, 110, level + 35, 210, level - 20)
            cr.line_to(210, 160)
            cr.line_to(-10, 160)
            cr.close_path()
            cr.fill()
    elif theme.id == 'graphite':
        _gradient(cr, 0, 0, 200, 150, ((0, '#15272a'), (1, '#202d31')))
        cr.paint()
        for pos in range(-100, 251, 20):
            _color(cr, '#839c74', .14)
            cr.set_line_width(.6)
            cr.move_to(pos, 0)
            cr.line_to(pos + 100, 150)
            cr.stroke()
        _color(cr, '#b9e86a', .08)
        cr.arc(100, 75, 39, 0, 2 * math.pi)
        cr.fill()
        _color(cr, '#b9e86a')
        cr.set_line_width(2.2)
        _path(cr, 79, 56, 42, 31, 3)
        cr.stroke()
        cr.move_to(100, 87)
        cr.line_to(100, 96)
        cr.move_to(89, 96)
        cr.line_to(111, 96)
        cr.stroke()
        _color(cr, '#b9e86a')
        cr.arc(123, 52, 3, 0, 2 * math.pi)
        cr.fill()
    elif theme.id == 'midnight':
        _gradient(cr, 0, 0, 200, 150, ((0, '#243353'), (1, '#101d30')))
        cr.paint()
        for color, points in (('#303b62', ((-15, 140), (73, 40), (157, 140))),
                               ('#495282', ((44, 150), (119, 56), (213, 150))),
                               ('#222d4e', ((-10, 160), (52, 96), (151, 160)))):
            _color(cr, color)
            cr.move_to(*points[0])
            for point in points[1:]:
                cr.line_to(*point)
            cr.close_path()
            cr.fill()
        _color(cr, '#80dec9', .7)
        cr.arc(165, 27, 2, 0, math.tau)
        cr.fill()
    elif theme.id == 'light':
        _gradient(cr, 0, 0, 200, 150, ((0, '#e2d9ff'), (1, '#f7f7fd')))
        cr.paint()
        for cx, cy, radius, fill in ((80, 108, 90, '#d3c5fa'),
                                    (158, 115, 78, '#aa96e1'),
                                    (145, 147, 81, '#ede8fc')):
            _color(cr, fill)
            cr.arc(cx, cy, radius, 0, math.tau)
            cr.fill()
    else:
        _gradient(cr, 0, 0, 200, 150, ((0, '#154465'), (.58, '#21365e'), (1, '#302c62')))
        cr.paint()
        for color, opacity, shift in (('#5475d9', .6, 20), ('#4dc5d3', .5, -7),
                                       ('#9af7e1', .65, -14)):
            _color(cr, color, opacity)
            cr.move_to(-10, 100 + shift)
            cr.curve_to(41, 63 + shift, 74, 157 + shift, 140, 65 + shift)
            cr.curve_to(174, 13 + shift, 197, 34 + shift, 220, 0)
            cr.line_to(220, 12)
            cr.curve_to(152, 29, 163, 99, 90, 117 + shift)
            cr.curve_to(50, 129 + shift, 15, 87 + shift, -10, 105 + shift)
            cr.close_path()
            cr.fill()
        for sx, sy in ((22, 26), (39, 41), (67, 22), (152, 39), (179, 86)):
            _color(cr, '#dcfff9', .45)
            cr.arc(sx, sy, .65, 0, math.tau)
            cr.fill()
    cr.restore()


def draw_device_art(cr, theme, x, y, width, height, variant='pair'):
    """Draw connected desktop/tablet hardware, or a portrait tablet study."""
    base_width, base_height = (128, 186) if variant == 'tablet' else (320, 196)
    factor = min(width / base_width, height / base_height)
    if factor <= 0:
        return
    cr.save()
    cr.translate(x + (width - base_width * factor) / 2,
                 y + (height - base_height * factor) / 2)
    cr.scale(factor, factor)
    # Hardware keeps a neutral frame so every theme's wallpaper reads clearly.
    frame, rim = '#26313e', '#8692a8'
    shadow = '#091522'

    def screen(sx, sy, sw, sh, portrait=False):
        _rect(cr, sx - 2, sy - 2, sw + 4, sh + 4, rim, 9)
        _rect(cr, sx, sy, sw, sh, frame, 8, '#121a22', 2)
        inset = 8
        _wallpaper(cr, theme, sx + inset, sy + inset, sw - 2 * inset,
                   sh - 2 * inset)
        _color(cr, '#8da0b5')
        if portrait:
            cr.arc(sx + sw / 2, sy + 4, 1.3, 0, math.tau)
        else:
            cr.arc(sx + sw - 4, sy + sh / 2, 1.3, 0, math.tau)
        cr.fill()

    if variant == 'tablet':
        _color(cr, shadow, .13)
        cr.save()
        cr.translate(64, 174)
        cr.scale(1, .12)
        cr.arc(0, 0, 52, 0, math.tau)
        cr.fill()
        cr.restore()
        screen(15, 8, 98, 161, portrait=True)
    else:
        # A visibly connected cable follows the space between both devices.
        _color(cr, shadow, .10)
        cr.save()
        cr.translate(160, 177)
        cr.scale(1, .08)
        cr.arc(0, 0, 150, 0, math.tau)
        cr.fill()
        cr.restore()
        cable_y = 102
        cr.move_to(168, cable_y)
        cr.curve_to(211, cable_y - 3, 190, 153, 226, 146)
        cr.set_line_width(5)
        _color(cr, '#263440')
        cr.stroke_preserve()
        cr.set_line_width(1.5)
        _color(cr, '#8699ac')
        cr.stroke()
        _rect(cr, 165, cable_y - 5, 14, 10, '#536477', 2)
        if variant == 'aurora':
            screen(6, 15, 178, 121)
            _rect(cr, 79, 136, 32, 24, '#344253', 2)
            _rect(cr, 59, 158, 72, 6, '#3a495b', 3)
            screen(204, 83, 110, 80)
        else:
            screen(5, 41, 176, 118)
            _rect(cr, 0, 159, 187, 8, '#59647e', 4, '#a3a9c2', .8)
            _rect(cr, 73, 159, 42, 3, '#2c394c', 2)
            screen(220, 15, 88, 145, portrait=True)
    cr.restore()


def draw_devices(cr, theme, x, y, width, height):
    """Compatibility wrapper for callers drawing the connected-device study."""
    draw_device_art(cr, theme, x, y, width, height, 'pair')


def draw_preview(cr, theme_id, width, height, language='en', compact=False):
    """A schematic of each actual composition, with a shared operating model."""
    theme = get_theme(theme_id)
    c = theme.colors
    ru = language == 'ru'
    t = lambda en, russian: russian if ru else en
    scale = min(width / 640, height / 400)
    if scale <= 0:
        return
    cr.save()
    cr.translate((width - 640 * scale) / 2, (height - 400 * scale) / 2)
    cr.scale(scale, scale)
    _path(cr, 0, 0, 640, 400, max(6, theme.radius))
    cr.clip()
    if theme.id == 'aurora':
        _gradient(cr, 0, 0, 640, 400, ((0, '#91cbe5'), (.4, '#c7f1f1'),
                                     (.76, '#becbee'), (1, '#a4dce7')))
        cr.paint()
    else:
        _color(cr, c['bg'])
        cr.paint()
    editorial = theme.id == 'editorial'
    header_fg = '#faf8f2' if editorial else c['text']
    if editorial:
        _rect(cr, 0, 0, 640, 36, '#282c2b')
    _text(cr, 'Panelyra', 20, 10, header_fg, 14, True)
    for index in range(3):
        _rect(cr, 584 + index * 16, 16, 6, 6, header_fg, 3)
    _line(cr, 0, 35, 640, c['border'])
    nav = [t('Connection', 'Подключение'), t('Settings', 'Настройки'),
           t('Appearance', 'Оформление')]
    studio = theme.id == 'midnight'
    console = theme.id == 'graphite'
    if studio:
        _rect(cr, 0, 36, 112, 364, '#0c1926')
        _line(cr, 111, 36, 1, c['border'], 364)
        _rect(cr, 35, 57, 40, 40, '#273348', 11)
        _text(cr, 'P', 47, 61, '#9d8ff3', 27, True)
        _text(cr, 'Panelyra', 24, 105, c['text'], 15, True)
        for index, label in enumerate(nav):
            yy = 157 + index * 40
            if index == 0:
                _rect(cr, 6, yy - 7, 100, 32, '#2c2856', 6)
            _text(cr, label, 14, yy, c['text'] if not index else c['muted'], 10,
                  not index, 90)
    else:
        for index, label in enumerate(nav):
            tw = 112
            xx = (244 if console else 145) + index * tw
            yy = 13 if console else 50
            if index == 0:
                _rect(cr, xx - 9, yy - 6, tw - 4, 27,
                      mix(c['surface'], c['accent'], .12), min(theme.radius, 10))
            _text(cr, label, xx, yy, c['text'] if not index else c['muted'], 10,
                  not index, tw - 8)

    radius = 12 if theme.id == 'light' else min(theme.radius, 15)

    def card(cx, cy, cw, ch):
        _rect(cr, cx, cy, cw, ch, c['surface'], radius, c['border'])

    def fields(cx, cy, cw, ch, vertical=False):
        card(cx, cy, cw, ch)
        _text(cr, t('Picture settings', 'Настройки изображения'), cx + 13, cy + 11,
              c['text'], 12, True, cw - 20)
        labels = (t('Profile', 'Профиль'), t('Resolution', 'Разрешение'),
                  t('Frame rate', 'Частота'), t('Quality', 'Качество'))
        values = (t('Balanced', 'Баланс'), '1920 × 1080', '30 fps', '18')
        if vertical:
            for index, (label, value) in enumerate(zip(labels, values)):
                yy = cy + 38 + index * max(25, (ch - 51) / 4)
                _text(cr, label, cx + 13, yy + 5, c['muted'], 9)
                _rect(cr, cx + cw * .48, yy, cw * .46, 23, c['raised'], min(radius, 4), c['border'])
                _text(cr, value, cx + cw * .48 + 8, yy + 6, c['text'], 9)
        else:
            gap = 12
            ww = (cw - 26 - gap) / 2
            for index, (label, value) in enumerate(zip(labels, values)):
                xx = cx + 13 + (index % 2) * (ww + gap)
                yy = cy + 32 + (index // 2) * 40
                _text(cr, label, xx, yy, c['muted'], 9)
                _rect(cr, xx, yy + 14, ww, 23, c['bg'], min(radius, 4), c['border'])
                _text(cr, value, xx + 8, yy + 20, c['text'], 9)

    def network(cx, cy, cw, ch=45):
        card(cx, cy, cw, ch)
        _text(cr, t('Internet via tablet', 'Интернет через планшет'), cx + 12, cy + 10,
              c['text'], 10, True, cw - 65)
        if ch > 37:
            _text(cr, t('Use the tablet connection', 'Использовать подключение планшета'),
                  cx + 12, cy + 27, c['muted'], 8, width=cw - 65)
        switch_y = cy + (6 if ch < 35 else 13)
        _rect(cr, cx + cw - 47, switch_y, 32, 17, c['raised'], 9, c['border'])
        _rect(cr, cx + cw - 44, switch_y + 3, 11, 11, c['muted'], 6)

    def device(cx, cy, cw, ch, art=None, horizontal=False):
        card(cx, cy, cw, ch)
        _text(cr, t('Device', 'Устройство'), cx + 13, cy + 10, c['muted'], 10, True)
        tx, ty = cx + 13, cy + 30
        if art:
            if horizontal:
                draw_device_art(cr, theme, cx + 9, cy + 25, cw * .34, ch - 34, art)
                tx, ty = cx + cw * .4, cy + 37
            else:
                draw_device_art(cr, theme, cx + 16, cy + 31, cw - 32, ch * .55, art)
                ty = cy + ch * .66
        _text(cr, t('Android tablet', 'Планшет Android'), tx, ty, c['text'], 13, True,
              cw - (tx - cx) - 10)
        _rect(cr, tx, ty + 23, 5, 5, c['good'], 3)
        _text(cr, t('USB ready', 'USB готов'), tx + 11, ty + 18, c['good'], 10, True)
        if ch > 82:
            _text(cr, t('Ready to connect', 'Готов к подключению'), tx, ty + 37,
                  c['muted'], 9, width=cw - (tx - cx) - 8)

    if theme.id == 'classic':
        card(20, 92, 600, 74)
        _rect(cr, 37, 111, 38, 38, '#2f314e', 10)
        _text(cr, 'P', 48, 113, '#b4a1ff', 25, True)
        _text(cr, t('Room for one more idea.', 'Место для новой идеи.'),
              92, 107, c['text'], 19, True)
        _text(cr, t('Extend your Linux desktop over USB.', 'Расширьте рабочий стол Linux по USB.'),
              92, 136, c['muted'], 10)
        device(20, 178, 600, 114)
        _text(cr, t('Connection log', 'Журнал подключения'), 29, 309, c['muted'], 10)
    elif theme.id == 'light':
        _rect(cr, 233, 87, 35, 35, '#282d41', 10)
        _text(cr, 'P', 244, 90, '#a595ed', 25, True)
        _text(cr, 'Panelyra', 281, 88, c['text'], 23, True)
        _text(cr, t('Second life. Second screen.', 'Вторая жизнь. Второй экран.'),
              281, 115, c['muted'], 9)
        device(20, 134, 600, 64)
        fields(20, 208, 600, 112)
        network(20, 328, 600, 27)
    elif theme.id == 'midnight':
        device(124, 50, 279, 291, 'pair')
        fields(415, 50, 211, 194, True)
        network(415, 254, 211, 87)
    elif theme.id == 'aurora':
        _text(cr, 'Panelyra', 26, 101, c['text'], 31, True)
        _text(cr, t('Room for a new idea.', 'Место для новой идеи.'),
              28, 139, c['text'], 14, True, 260)
        _text(cr, t('One USB cable. More possibilities.', 'Один USB-кабель. Больше возможностей.'),
              28, 165, c['muted'], 10, width=260)
        draw_device_art(cr, theme, 307, 82, 306, 144, 'aurora')
        device(20, 213, 262, 105)
        fields(294, 213, 326, 105)
        network(20, 326, 600, 29)
    elif theme.id == 'editorial':
        _text(cr, t('Your second', 'Ваш второй'), 24, 91, c['text'], 35, True)
        _text(cr, t('screen.', 'экран.'), 24, 128, c['text'], 35, True)
        _text(cr, t('Give your Android tablet a new purpose.', 'Подарите планшету Android новую роль.'),
              27, 178, c['muted'], 10, width=265)
        device(318, 84, 302, 146, 'tablet', True)
        fields(20, 241, 347, 109)
        network(379, 241, 241, 109)
    else:
        device(20, 53, 235, 287, 'tablet')
        fields(267, 53, 353, 193, True)
        network(267, 258, 353, 82)

    # Actual designs share stream actions; their chrome differs with the theme.
    footer_x = 124 if studio else 20
    footer_w = 502 if studio else 600
    footer_y = 361
    if editorial:
        _rect(cr, footer_x, footer_y - 3, footer_w, 37, '#282c2b', 5)
    elif console or studio:
        _rect(cr, footer_x, footer_y - 3, footer_w, 37, c['surface'], radius, c['border'])
    else:
        _line(cr, footer_x, footer_y - 6, footer_w, c['border'])
    _text(cr, t('Ready', 'Готово'), footer_x + 12, footer_y + 7,
          '#faf8f2' if editorial else c['muted'], 10)
    _rect(cr, footer_x + footer_w - 206, footer_y + 1, 194, 27,
          c['accent'], min(theme.radius, 7))
    _text(cr, t('Connect tablet', 'Подключить планшет'), footer_x + footer_w - 187,
          footer_y + 8, c['accent_text'], 10, True)
    cr.restore()


class ThemePreview(Gtk.DrawingArea):
    def __init__(self, theme_id, compact=False):
        super().__init__()
        self.theme_id = get_theme(theme_id).id
        self.compact = compact
        self.language = 'en'
        self.set_size_request(-1, 125 if compact else 280)
        self.set_hexpand(True)
        self.get_accessible().set_name('Panelyra theme preview')
        self.connect('draw', self._draw)

    def set_theme(self, theme_id):
        self.theme_id = get_theme(theme_id).id
        self.queue_draw()

    def set_language(self, language):
        self.language = 'ru' if language == 'ru' else 'en'
        self.get_accessible().set_name('Предпросмотр оформления Panelyra' if self.language == 'ru'
                                       else 'Panelyra theme preview')
        self.queue_draw()

    def _draw(self, widget, cr):
        draw_preview(cr, self.theme_id, self.get_allocated_width(),
                     self.get_allocated_height(), self.language, self.compact)
        return False


class DeviceIllustration(Gtk.DrawingArea):
    def __init__(self, theme_id='classic'):
        super().__init__()
        self.theme_id = get_theme(theme_id).id
        self.variant = 'pair'
        self.set_size_request(128, 90)
        self.connect('draw', self._draw)

    def set_theme(self, theme_id):
        self.theme_id = get_theme(theme_id).id
        self.queue_draw()

    def set_variant(self, variant):
        if variant not in ('pair', 'tablet', 'aurora'):
            raise ValueError('Unknown device illustration variant: ' + str(variant))
        self.variant = variant
        self.queue_draw()

    def _draw(self, widget, cr):
        draw_device_art(cr, get_theme(self.theme_id), 0, 0,
                        self.get_allocated_width(), self.get_allocated_height(), self.variant)
        return False

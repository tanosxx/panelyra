"""Six independent, procedural worlds for the locked remote display.

All artwork is local Cairo geometry. Nothing samples the desktop or requests
network data. Static, expensive artwork is cached; every frame is opaque.
"""

from datetime import datetime
import math
import random
import sys
import time

import cairo

from .language import resolve_language
from .themes import DEFAULT_THEME, get_theme


TAU = math.tau
MINT = (0.43, 0.93, 0.83)
VIOLET = (0.59, 0.53, 1.0)
WHITE = (0.91, 0.94, 1.0)


def rgb(value):
    return tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))


def blend(first, second, amount):
    return tuple(a * (1 - amount) + b * amount for a, b in zip(first, second))


def rounded_rect(ctx, x, y, width, height, radius):
    ctx.new_sub_path()
    for cx, cy, start in ((x + width - radius, y + radius, -math.pi / 2),
                          (x + width - radius, y + height - radius, 0),
                          (x + radius, y + height - radius, math.pi / 2),
                          (x + radius, y + radius, math.pi)):
        ctx.arc(cx, cy, radius, start, start + math.pi / 2)
    ctx.close_path()


def glow(ctx, x, y, radius, color, opacity):
    gradient = cairo.RadialGradient(x, y, 0, x, y, radius)
    gradient.add_color_stop_rgba(0, *color, opacity)
    gradient.add_color_stop_rgba(0.35, *color, opacity * 0.42)
    gradient.add_color_stop_rgba(1, *color, 0)
    ctx.set_source(gradient)
    ctx.new_sub_path()
    ctx.arc(x, y, radius, 0, TAU)
    ctx.fill()


def trailing_arc(ctx, radius, angle, speed, length=0.55):
    """Draw from the point's previous position to its current position."""
    ctx.new_sub_path()
    if speed >= 0:
        ctx.arc(0, 0, radius, angle - length, angle)
    else:
        ctx.arc_negative(0, 0, radius, angle + length, angle)


def polyline(ctx, points):
    for index, (x, y) in enumerate(points):
        ctx.move_to(x, y) if index == 0 else ctx.line_to(x, y)


class LockScene:
    def __init__(self, width, height, language=None, theme=DEFAULT_THEME):
        if width <= 0 or height <= 0:
            raise ValueError('Lock scene dimensions must be positive')
        self.width, self.height = width, height
        self.language = resolve_language(language or 'auto')
        design = get_theme(theme if isinstance(theme, str) else DEFAULT_THEME)
        self.theme = design.id
        self.colors = {key: rgb(value) for key, value in design.colors.items()}
        self.portrait = height > width
        self.scale = min(width / (1000 if self.portrait else 1600),
                         height / (1500 if self.portrait else 1000))
        self.canvas_width, self.canvas_height = width / self.scale, height / self.scale
        self.cx, self.cy = self.canvas_width / 2, self.canvas_height / 2
        self.started = time.monotonic()
        self.draw_error_reported = False
        rng = random.Random(27)
        self.particles = [(rng.random(), rng.random(), rng.uniform(.8, 2.1),
                           rng.uniform(0, TAU), rng.uniform(.10, .27))
                          for _ in range(90)]
        self.background = self._background()
        self.foreground = self._landscape() if self.theme == 'aurora' else None
        self.planet = self._planet() if self.theme == 'classic' else None
        # A private framebuffer also isolates Cairo save/clip stacks. A failed
        # nested drawing helper can never leave the caller's context clipped
        # or partly painted with the previous desktop frame still visible.
        self.frame_surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)

    @staticmethod
    def text(ctx, text, x, y, size, color=WHITE, opacity=1, centered=True,
             family='DejaVu Sans'):
        ctx.select_font_face(family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size)
        xb, _yb, width, _height, _xa, _ya = ctx.text_extents(text)
        ctx.move_to(x - width / 2 - xb if centered else x, y)
        ctx.set_source_rgba(*color, opacity)
        ctx.show_text(text)
        ctx.new_path()

    def _fit(self, ctx, value, x, y, size, width, color=None, centered=False,
             family='DejaVu Sans', opacity=1):
        ctx.select_font_face(family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size)
        extent = ctx.text_extents(value)[2]
        self.text(ctx, value, x, y, size * min(1, width / max(extent, 1)),
                  color or self.colors['text'], opacity, centered, family)

    def date_text(self, now):
        if self.language == 'ru':
            weekdays = ('Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье')
            months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                      'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
            return f'{weekdays[now.weekday()]}, {now.day} {months[now.month - 1]}'
        weekdays = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
        months = ('January', 'February', 'March', 'April', 'May', 'June',
                  'July', 'August', 'September', 'October', 'November', 'December')
        return f'{weekdays[now.weekday()]}, {months[now.month - 1]} {now.day}'

    def _surface(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.width, self.height)
        ctx = cairo.Context(surface)
        ctx.scale(self.scale, self.scale)
        return surface, ctx

    def _paint_cached(self, ctx, surface):
        ctx.save()
        ctx.scale(1 / self.scale, 1 / self.scale)
        ctx.set_source_surface(surface, 0, 0)
        ctx.paint()
        ctx.restore()

    def _background(self):
        surface, ctx = self._surface()
        c, w, h = self.colors, self.canvas_width, self.canvas_height
        gradient = cairo.LinearGradient(0, 0, w, h)
        stops = {
            'classic': ((0, (.028, .026, .072)), (.6, (.021, .034, .071)), (1, (.014, .024, .045))),
            'light': ((0, (.987, .981, .969)), (1, (.940, .934, .966))),
            'midnight': ((0, (.065, .043, .12)), (.6, (.056, .048, .105)), (1, (.025, .030, .07))),
            'aurora': ((0, (.58, .72, .78)), (.4, (.78, .83, .90)), (1, (.91, .95, .93))),
            'editorial': ((0, (.975, .953, .91)), (1, (.945, .913, .847))),
            'graphite': ((0, (.048, .063, .059)), (1, (.084, .105, .088))),
        }[self.theme]
        for position, color in stops:
            gradient.add_color_stop_rgb(position, *color)
        ctx.set_source(gradient)
        ctx.paint()
        if self.theme == 'classic':
            x, y, radius = self._planet_geometry()
            glow(ctx, x, y, radius * 1.9, VIOLET, .23)
            glow(ctx, x - radius * .5, y - radius * .45, radius * 1.2, MINT, .08)
            # Distant nebula filaments, separate from the moving orbital paths.
            for index in range(6):
                ctx.move_to(-100, h * .67 + index * 18)
                ctx.curve_to(w * .3, h * .15, w * .6, h * 1.08, w + 80, h * .34 + index * 15)
                ctx.set_source_rgba(*VIOLET, .018)
                ctx.set_line_width(6 - index * .7)
                ctx.stroke()
        elif self.theme == 'light':
            glow(ctx, w * .8, h * .45, w * .55, (.72, .67, .91), .10)
            ctx.set_source_rgba(*c['accent'], .11)
            ctx.set_line_width(1)
            ctx.move_to(70, h - 106)
            ctx.line_to(w - 70, h - 106)
            ctx.stroke()
        elif self.theme == 'midnight':
            glow(ctx, w * .5, h * .70, w * .55, (.51, .28, .92), .30)
            glow(ctx, w * .9, h * .5, w * .4, (.22, .27, .84), .12)
        elif self.theme == 'aurora':
            glow(ctx, w * .19, h * .28, w * .65, (.59, 1, .83), .37)
            glow(ctx, w * .89, h * .38, w * .65, (.81, .61, .99), .28)
            # A translucent daylight moon belongs to this sky, not the UI.
            ctx.arc(w * .84, h * .17, 28, 0, TAU)
            ctx.set_source_rgba(.98, 1, .96, .55)
            ctx.fill()
        elif self.theme == 'editorial':
            ctx.set_line_width(1)
            ctx.set_source_rgba(*c['accent'], .28)
            for y in (110, h - 104):
                ctx.move_to(70, y)
                ctx.line_to(w - 70, y)
            ctx.stroke()
            # A tiny print-like dot pattern gives the paper a tactile detail.
            for x in range(78, int(w - 60), 12):
                ctx.arc(x, h - 128, .7, 0, TAU)
            ctx.set_source_rgba(*c['accent'], .13)
            ctx.fill()
        elif self.theme == 'graphite':
            ctx.set_line_width(.7)
            ctx.set_source_rgba(*c['accent'], .036)
            for x in range(54, int(w), 54):
                ctx.move_to(x, 110)
                ctx.line_to(x, h - 112)
            for y in range(110, int(h - 100), 54):
                ctx.move_to(54, y)
                ctx.line_to(w - 54, y)
            ctx.stroke()
            for x in (54, w - 54):
                for y in (110, h - 112):
                    ctx.move_to(x - 6, y)
                    ctx.line_to(x + 6, y)
                    ctx.move_to(x, y - 6)
                    ctx.line_to(x, y + 6)
            ctx.set_source_rgba(*c['accent'], .65)
            ctx.stroke()
        return surface

    def _brand(self, ctx):
        c = self.colors
        family = 'DejaVu Sans Mono' if self.theme == 'graphite' else 'DejaVu Sans'
        color = WHITE if self.theme in ('classic', 'midnight') else c['text']
        if self.theme == 'editorial':
            self.text(ctx, 'Panelyra.', 70, 76, 30, c['text'], centered=False, family='DejaVu Serif')
            self.text(ctx, 'Тихая пауза' if self.language == 'ru' else 'A quiet pause',
                      self.canvas_width - 160, 73, 17, c['muted'], family='DejaVu Serif')
            return
        ctx.set_line_width(1.8)
        rounded_rect(ctx, 60, 52, 23, 17, 3)
        ctx.set_source_rgba(*color, .80)
        ctx.stroke()
        rounded_rect(ctx, 77, 60, 12, 18, 2)
        ctx.stroke()
        self.text(ctx, 'P A N E L Y R A', 106, 72, 17, color, .80, False, family)

    def _closed_lock(self, ctx, x, y, color, accent, size=1):
        ctx.save()
        ctx.translate(x, y)
        ctx.scale(size, size)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_width(3.2)
        ctx.arc(0, -8, 14, math.pi, TAU)
        ctx.line_to(14, 3)
        ctx.move_to(-14, -8)
        ctx.line_to(-14, 3)
        ctx.set_source_rgba(*color, .94)
        ctx.stroke()
        rounded_rect(ctx, -22, 0, 44, 32, 5)
        ctx.stroke()
        ctx.arc(0, 13, 3, 0, TAU)
        ctx.set_source_rgb(*accent)
        ctx.fill()
        ctx.move_to(0, 15)
        ctx.line_to(0, 21)
        ctx.set_line_width(2.5)
        ctx.stroke()
        ctx.restore()

    def _locked(self, ctx, x, y, size=24, width=650, color=None, family='DejaVu Sans'):
        c = self.colors
        self._closed_lock(ctx, x + 8, y - 9, color or c['muted'], c['accent'], .38)
        self._fit(ctx, 'Экран заблокирован' if self.language == 'ru' else 'Screen locked',
                  x + 32, y, size, width - 32, color or c['text'], family=family)

    def _instruction(self, ctx, x, y, width=660, centered=False, color=None):
        self._fit(ctx, 'Разблокируйте компьютер, чтобы продолжить' if self.language == 'ru' else
                  'Unlock your computer to pick up where you left off',
                  x, y, 18, width, color or self.colors['muted'], centered)

    def _connected(self, ctx, x, y, elapsed, color=None, family='DejaVu Sans'):
        color = color or self.colors['good']
        ctx.arc(x + 3, y - 5, 2.8, 0, TAU)
        ctx.set_source_rgba(*color, .68 + .20 * math.sin(elapsed * .9))
        ctx.fill()
        self.text(ctx, 'Подключение сохранено' if self.language == 'ru' else
                  'Your display is still connected', x + 18, y, 16, color, .9, False, family)

    # CLASSIC: an illuminated planet and travelling moons in deep space.
    def _planet_geometry(self):
        if self.portrait:
            return self.cx, self.canvas_height * .37, 260
        return self.canvas_width * .29, self.canvas_height * .49, 264

    def _planet(self):
        surface, ctx = self._surface()
        x, y, radius = self._planet_geometry()
        glow(ctx, x, y, radius + 20, (.39, .59, 1), .42)
        ctx.arc(x, y, radius, 0, TAU)
        gradient = cairo.RadialGradient(x - radius * .55, y - radius * .5, 10,
                                        x + radius * .1, y + radius * .1, radius * 1.45)
        for p, color in ((0, (.41, .61, .80)), (.32, (.23, .36, .60)),
                         (.70, (.10, .13, .30)), (1, (.018, .025, .065))):
            gradient.add_color_stop_rgb(p, *color)
        ctx.set_source(gradient)
        ctx.fill()
        ctx.save()
        ctx.arc(x, y, radius - .8, 0, TAU)
        ctx.clip()
        ctx.translate(x, y)
        ctx.rotate(-.27)
        for index in range(21):
            yy = -radius + index * radius * .11
            ctx.move_to(-radius - 20, yy)
            ctx.curve_to(-radius * .4, yy - 52, radius * .4, yy + 45, radius + 20, yy - 7)
            ctx.set_source_rgba(*blend(VIOLET, MINT, (index % 7) / 7), .10 if index % 3 else .20)
            ctx.set_line_width(9 if index % 3 else 17)
            ctx.stroke()
        shade = cairo.LinearGradient(-radius, -radius, radius * .65, radius * .5)
        shade.add_color_stop_rgba(0, .66, .82, 1, .10)
        shade.add_color_stop_rgba(.45, .015, .025, .06, 0)
        shade.add_color_stop_rgba(1, .008, .014, .040, .84)
        ctx.set_source(shade)
        ctx.paint()
        ctx.restore()
        ctx.arc(x, y, radius, -.91 * math.pi, -.16 * math.pi)
        ctx.set_source_rgba(.73, .83, 1, .48)
        ctx.set_line_width(1.7)
        ctx.stroke()
        return surface

    def _classic(self, ctx, elapsed):
        w, h = self.canvas_width, self.canvas_height
        for px, py, radius, phase, speed in self.particles:
            alpha = .12 + .4 * (.5 + .5 * math.sin(phase + elapsed * .55))
            ctx.arc(px * w, (py * h - elapsed * speed * 1.7) % h, radius, 0, TAU)
            ctx.set_source_rgba(*WHITE, alpha)
            ctx.fill()
        x, y, radius = self._planet_geometry()
        # A ring system is drawn in two depth passes around the planet.
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(-.35)
        ctx.scale(1, .37)
        for rr, alpha in ((radius * 1.47, .23), (radius * 1.54, .10), (radius * 1.62, .055)):
            ctx.arc(0, 0, rr, 0, TAU)
            ctx.set_source_rgba(*MINT, alpha)
            ctx.set_line_width(1.8)
            ctx.stroke()
        angle = elapsed * .14 + .4
        trailing_arc(ctx, radius * 1.47, angle, .14, .70)
        ctx.set_source_rgba(*WHITE, .82)
        ctx.set_line_width(3.2)
        ctx.stroke()
        ctx.restore()
        self._paint_cached(ctx, self.planet)
        # Wind bands move across the lit hemisphere while the cached sphere
        # retains its shading. This animates the planet itself, not just stars.
        ctx.save()
        ctx.arc(x, y, radius - 2, 0, TAU)
        ctx.clip()
        ctx.translate(x, y)
        ctx.rotate(-.27)
        atmosphere = cairo.LinearGradient(-radius, -radius, radius * .65, radius * .4)
        atmosphere.add_color_stop_rgba(0, *MINT, .015)
        atmosphere.add_color_stop_rgba(.35, *WHITE, .11)
        atmosphere.add_color_stop_rgba(1, *VIOLET, 0)
        ctx.set_source(atmosphere)
        for index in range(12):
            yy = -radius + index * 47 + 12 * math.sin(elapsed * .23 + index * .32)
            ctx.move_to(-radius - 15, yy)
            ctx.curve_to(-radius * .45, yy - 43 + 13 * math.cos(elapsed * .14 + index),
                         radius * .40, yy + 48, radius + 15, yy - 7)
            ctx.set_line_width(8 + index % 3 * 5)
            ctx.stroke()
        ctx.restore()
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(-.35)
        ctx.scale(1, .37)
        # Only the near half is repainted over the planet. Both the moon and
        # the trailing line therefore disappear behind its far hemisphere.
        ctx.rectangle(-radius * 2, 0, radius * 4, radius * 2)
        ctx.clip()
        ctx.arc(0, 0, radius * 1.47, 0, math.pi)
        ctx.set_source_rgba(*MINT, .50)
        ctx.set_line_width(2.2)
        ctx.stroke()
        trailing_arc(ctx, radius * 1.47, angle, .14, .70)
        ctx.set_source_rgba(*WHITE, .82)
        ctx.set_line_width(3.2)
        ctx.stroke()
        ctx.restore()
        ox, oy = radius * 1.47 * math.cos(angle), radius * 1.47 * .37 * math.sin(angle)
        mx, my = x + ox * math.cos(-.35) - oy * math.sin(-.35), y + ox * math.sin(-.35) + oy * math.cos(-.35)
        # The moon disappears behind the body for the far side of its orbit.
        if math.sin(angle) >= 0 or (mx - x) ** 2 + (my - y) ** 2 > radius ** 2:
            glow(ctx, mx, my, 29, MINT, .22)
            ctx.arc(mx, my, 10, 0, TAU)
            moon = cairo.LinearGradient(mx - 8, my - 8, mx + 8, my + 8)
            moon.add_color_stop_rgb(0, .87, 1, .96)
            moon.add_color_stop_rgb(1, .24, .42, .52)
            ctx.set_source(moon)
            ctx.fill()
        # Slow meteors; their line always ends at the moving leading point.
        for index in range(3):
            phase = (elapsed * .055 + index * .31) % 1
            mx, my = w * (.10 + phase * .73), h * (.12 + index * .19) + phase * 110
            gradient = cairo.LinearGradient(mx - 100, my - 28, mx, my)
            gradient.add_color_stop_rgba(0, *VIOLET, 0)
            gradient.add_color_stop_rgba(1, *WHITE, .25 * math.sin(math.pi * phase))
            ctx.set_source(gradient)
            ctx.move_to(mx - 100, my - 28)
            ctx.line_to(mx, my)
            ctx.set_line_width(1.2)
            ctx.stroke()

    # LIGHT: a gently swaying paper mobile with its own sculptural silhouette.
    def _paper_piece(self, ctx, x, y, width, height, rotation, color, kind):
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(rotation)
        def silhouette():
            if kind == 'oval':
                ctx.save()
                ctx.scale(width / 2, height / 2)
                ctx.arc(0, 0, 1, 0, TAU)
                ctx.restore()
            elif kind == 'fold':
                polyline(ctx, [(-width / 2, -height / 2), (width * .3, -height / 2),
                               (width / 2, height * .1), (width / 2, height / 2), (-width / 2, height / 2)])
                ctx.close_path()
            else:
                ctx.set_line_width(height * .46)
                ctx.arc(0, 0, width * .35, -.9 * math.pi, .12 * math.pi)

        ctx.save()
        ctx.translate(8, 12)
        silhouette()
        ctx.set_source_rgba(.35, .29, .50, .05)
        ctx.stroke() if kind == 'arc' else ctx.fill()
        ctx.restore()
        gradient = cairo.LinearGradient(-width / 2, -height / 2, width / 2, height / 2)
        gradient.add_color_stop_rgb(0, *blend(color, (1, 1, 1), .34))
        gradient.add_color_stop_rgb(1, *color)
        ctx.set_source(gradient)
        silhouette()
        ctx.stroke() if kind == 'arc' else ctx.fill()
        if kind == 'fold':
            polyline(ctx, [(width * .3, -height / 2), (width * .13, height * .02), (width / 2, height * .1)])
            ctx.close_path()
            ctx.set_source_rgba(1, 1, 1, .58)
            ctx.fill()
            ctx.move_to(width * .13, height * .02)
            ctx.line_to(-width * .5, height / 2)
            ctx.set_source_rgba(1, 1, 1, .40)
            ctx.set_line_width(1)
            ctx.stroke()
        ctx.restore()

    def _light(self, ctx, elapsed):
        w, h = self.canvas_width, self.canvas_height
        x, y = (self.cx, h * .66) if self.portrait else (w * .75, h * .50)
        sway = math.sin(elapsed * .25)
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(.035 * sway)
        fold_x, fold_angle = -172 + sway * 8, -.18 + .12 * math.sin(elapsed * .24)
        oval_angle = .27 + .14 * math.sin(elapsed * .21 + 1)
        arc_x, arc_angle = 16 + 18 * math.sin(elapsed * .22 + 2), -.07 + .13 * math.sin(elapsed * .19 + 3)
        # Rods, vertical suspension and three independently moving paper leaves.
        ctx.set_source_rgba(.43, .39, .54, .35)
        ctx.set_line_width(1.25)
        ctx.move_to(0, -310)
        ctx.line_to(0, -225)
        ctx.move_to(-190, -215)
        ctx.line_to(185, -240)
        ctx.move_to(-172, -216)
        ctx.line_to(fold_x + 113 * math.sin(fold_angle), 22 - 113 * math.cos(fold_angle))
        ctx.move_to(164, -238)
        ctx.line_to(164 + 132 * math.sin(oval_angle), -37 - 132 * math.cos(oval_angle))
        ctx.move_to(16, -226)
        ctx.line_to(arc_x + 119 * math.sin(arc_angle), 235 - 119 * math.cos(arc_angle))
        ctx.stroke()
        self._paper_piece(ctx, fold_x, 22, 186, 226,
                          fold_angle, (.77, .72, .91), 'fold')
        self._paper_piece(ctx, 164, -37, 185, 264,
                          oval_angle, (.67, .83, .78), 'oval')
        self._paper_piece(ctx, arc_x, 235, 250, 145,
                          arc_angle, (.89, .76, .65), 'arc')
        ctx.arc(0, -225, 5, 0, TAU)
        ctx.set_source_rgb(.56, .48, .69)
        ctx.fill()
        ctx.restore()
        # A separate small lavender sheet catches the same invisible breeze.
        xx, yy = (w * .82, h * .48) if self.portrait else (w * .54, h * .78)
        self._paper_piece(ctx, xx, yy + 15 * math.sin(elapsed * .31), 69, 92,
                          -.40 + elapsed * .015, (.85, .81, .94), 'fold')

    # MIDNIGHT: a light installation reflected in a flowing synthetic horizon.
    def _midnight(self, ctx, elapsed):
        w, h = self.canvas_width, self.canvas_height
        horizon = h * (.70 if self.portrait else .69)
        c = self.colors
        for index in range(11):
            x = w * (.05 + index * .09)
            pulse = .5 + .5 * math.sin(elapsed * .42 + index * .69)
            height = (110 + 170 * pulse) * (1.4 if self.portrait else 1)
            width = 28 + 7 * (index % 3)
            yy = horizon - height
            color = blend((.37, .27, .88), c['accent'], pulse * .6)
            gradient = cairo.LinearGradient(x, yy, x, horizon)
            gradient.add_color_stop_rgba(0, *color, 0)
            gradient.add_color_stop_rgba(.25, *color, .07)
            gradient.add_color_stop_rgba(1, *color, .45)
            ctx.rectangle(x - width / 2, yy, width, height)
            ctx.set_source(gradient)
            ctx.fill()
            # Bright narrow edge and a softer reflection below the horizon.
            ctx.rectangle(x - 1, yy + 24, 2, height - 24)
            ctx.set_source(gradient)
            ctx.fill()
            reflection = cairo.LinearGradient(x, horizon, x, horizon + height * .44)
            reflection.add_color_stop_rgba(0, *color, .13)
            reflection.add_color_stop_rgba(1, *color, 0)
            ctx.set_source(reflection)
            ctx.rectangle(x - width / 2, horizon, width, height * .44)
            ctx.fill()
        for index in range(23):
            depth = index / 22
            points = []
            for step in range(91):
                xx = w * step / 90
                envelope = math.sin(math.pi * step / 90)
                yy = (horizon + depth ** 1.4 * h * .20
                      + math.sin(xx / 150 + elapsed * .53 - depth * 6) * (13 + depth * 18) * envelope
                      + math.sin(xx / 370 - elapsed * .32 + depth * 4) * 25 * envelope)
                points.append((xx, yy))
            polyline(ctx, points)
            ctx.set_line_width(1.1 + depth * .35)
            ctx.set_source_rgba(*blend(c['accent'], (.37, .53, .99), depth), .31 - depth * .21)
            ctx.stroke()
        gradient = cairo.LinearGradient(0, 0, w, 0)
        gradient.add_color_stop_rgba(0, *c['accent'], 0)
        gradient.add_color_stop_rgba(.5, *c['accent'], .75)
        gradient.add_color_stop_rgba(1, *c['accent'], 0)
        ctx.set_source(gradient)
        ctx.move_to(0, horizon)
        ctx.line_to(w, horizon)
        ctx.set_line_width(1)
        ctx.stroke()

    # AURORA: broad luminous curtains above layered, powder-soft mountains.
    def _landscape(self):
        surface, ctx = self._surface()
        w, h = self.canvas_width, self.canvas_height
        for index, (base, color) in enumerate(((.51, (.71, .76, .82)),
                                              (.60, (.76, .85, .86)),
                                              (.70, (.87, .91, .90)),
                                              (.82, (.94, .96, .94)))):
            points = []
            for step in range(81):
                x = w * step / 80
                y = h * base + math.sin(step / 80 * 5.3 + index * 1.8) * h * .05
                y += math.sin(step / 80 * 12 + index) * h * (.022 if index < 2 else .009)
                points.append((x, y))
            polyline(ctx, points)
            ctx.line_to(w, h)
            ctx.line_to(0, h)
            ctx.close_path()
            gradient = cairo.LinearGradient(0, h * (base - .04), 0, h)
            gradient.add_color_stop_rgb(0, *color)
            gradient.add_color_stop_rgb(1, *blend(color, (.95, .98, .96), .55))
            ctx.set_source(gradient)
            ctx.fill()
            if index < 3:
                polyline(ctx, points)
                ctx.set_source_rgba(.97, 1, .98, .23)
                ctx.set_line_width(2)
                ctx.stroke()
        return surface

    def _aurora(self, ctx, elapsed):
        w, h = self.canvas_width, self.canvas_height
        for ribbon in range(5):
            top, bottom = [], []
            for step in range(101):
                x = w * step / 100
                phase = x / w * 6.1 + elapsed * .13 + ribbon * .45
                y = h * (.16 + ribbon * .040) + math.sin(phase) * h * .09
                y += math.sin(x / w * 11 - elapsed * .16) * h * .025
                top.append((x, y))
                bottom.append((x, y + h * (.15 + .035 * math.sin(phase + 1))))
            polyline(ctx, top + list(reversed(bottom)))
            ctx.close_path()
            color = blend((.49, .99, .80), (.76, .65, .97), ribbon / 4)
            gradient = cairo.LinearGradient(0, h * .1, 0, h * .6)
            gradient.add_color_stop_rgba(0, *color, .04)
            gradient.add_color_stop_rgba(.5, *color, .26 - ribbon * .025)
            gradient.add_color_stop_rgba(1, *color, 0)
            ctx.set_source(gradient)
            ctx.fill()
        # Thin draped filaments move with the curtain, not independent sparkles.
        for index in range(85):
            u = index / 84
            x = u * w
            phase = u * 6.1 + elapsed * .13
            top = h * .17 + math.sin(phase) * h * .09 + math.sin(u * 11 - elapsed * .16) * h * .025
            gradient = cairo.LinearGradient(x, top, x, top + h * .24)
            gradient.add_color_stop_rgba(0, .77, 1, .89, 0)
            gradient.add_color_stop_rgba(.24, .77, 1, .89, .12)
            gradient.add_color_stop_rgba(1, .83, .76, 1, 0)
            ctx.move_to(x, top)
            ctx.curve_to(x - 8, top + h * .07, x + 13, top + h * .14, x + 20, top + h * .24)
            ctx.set_source(gradient)
            ctx.set_line_width(3.5)
            ctx.stroke()
        self._paint_cached(ctx, self.foreground)

    # EDITORIAL: a terracotta kinetic sculpture around a real analogue clock.
    def _editorial_geometry(self):
        return ((self.cx, self.canvas_height * .57, 255) if self.portrait else
                (self.canvas_width * .74, self.canvas_height * .48, 252))

    def _editorial(self, ctx, elapsed):
        x, y, radius = self._editorial_geometry()
        c = self.colors
        ctx.save()
        ctx.translate(x, y)
        ctx.rotate(elapsed * .042 - .7)
        ctx.arc(0, 0, radius + 26, -.4, 2.6)
        ink = cairo.LinearGradient(-radius, -radius, radius, radius)
        ink.add_color_stop_rgba(0, *c['accent'], .30)
        ink.add_color_stop_rgba(.5, *c['accent'], .88)
        ink.add_color_stop_rgba(1, *c['accent'], .48)
        ctx.set_source(ink)
        ctx.set_line_width(27)
        ctx.stroke()
        ctx.arc(0, 0, radius + 68, 2.6, 4.7)
        ctx.set_source_rgba(*c['accent'], .23)
        ctx.set_line_width(1.2)
        ctx.stroke()
        ctx.arc(math.cos(4.7) * (radius + 68), math.sin(4.7) * (radius + 68), 6, 0, TAU)
        ctx.set_source_rgb(*c['accent'])
        ctx.fill()
        ctx.restore()
        ctx.arc(x, y, radius, 0, TAU)
        ctx.set_source_rgb(.993, .976, .932)
        ctx.fill()
        ctx.arc(x, y, radius - 1, 0, TAU)
        ctx.set_source_rgba(*c['accent'], .24)
        ctx.set_line_width(1)
        ctx.stroke()
        for index in range(60):
            angle = index * TAU / 60 - math.pi / 2
            outer = radius - 24
            inner = outer - (19 if index % 5 == 0 else 5)
            polyline(ctx, [(x + inner * math.cos(angle), y + inner * math.sin(angle)),
                           (x + outer * math.cos(angle), y + outer * math.sin(angle))])
            ctx.set_source_rgba(*c['text'], .65 if index % 5 == 0 else .20)
            ctx.set_line_width(2.2 if index % 5 == 0 else 1)
            ctx.stroke()
        for label, xx, yy in (('12', x, y - radius + 81), ('3', x + radius - 65, y + 12),
                               ('6', x, y + radius - 57), ('9', x - radius + 65, y + 12)):
            self.text(ctx, label, xx, yy, 31, c['text'], .80, family='DejaVu Serif')

    def _analogue_hands(self, ctx, now):
        x, y, radius = self._editorial_geometry()
        seconds = now.second + now.microsecond / 1_000_000
        minute = now.minute + seconds / 60
        hour = now.hour % 12 + minute / 60
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        for value, period, length, width in ((hour, 12, radius * .45, 7),
                                             (minute, 60, radius * .68, 4)):
            angle = value * TAU / period - math.pi / 2
            polyline(ctx, [(x, y), (x + length * math.cos(angle), y + length * math.sin(angle))])
            ctx.set_source_rgb(*self.colors['text'])
            ctx.set_line_width(width)
            ctx.stroke()
        angle = seconds * TAU / 60 - math.pi / 2
        polyline(ctx, [(x - 33 * math.cos(angle), y - 33 * math.sin(angle)),
                       (x + radius * .74 * math.cos(angle), y + radius * .74 * math.sin(angle))])
        ctx.set_source_rgb(*self.colors['accent'])
        ctx.set_line_width(1.5)
        ctx.stroke()
        ctx.arc(x, y, 8, 0, TAU)
        ctx.fill()
        ctx.arc(x, y, 3, 0, TAU)
        ctx.set_source_rgb(.99, .97, .93)
        ctx.fill()

    # GRAPHITE: a perspective circuit field, with travelling electrical pulses.
    def _mesh_point(self, column, row, elapsed):
        w, h = self.canvas_width, self.canvas_height
        depth = row / 16
        spread = .13 + .87 * depth
        x = w * .49 + column / 12 * w * .59 * spread
        wave = math.sin(column * .29 + elapsed * .35) * math.sin(depth * math.pi)
        y = h * .28 + depth ** 1.6 * h * .39 - wave * (74 if self.portrait else 63)
        return x, y

    def _graphite(self, ctx, elapsed):
        c, w, h = self.colors, self.canvas_width, self.canvas_height
        # The entire surface flexes slowly; pulse trails follow its actual paths.
        ctx.set_line_width(.9)
        for row in range(17):
            polyline(ctx, [self._mesh_point(column, row, elapsed) for column in range(-12, 13)])
            ctx.set_source_rgba(*c['accent'], .12 + row / 16 * .11)
            ctx.stroke()
        for column in range(-12, 13):
            polyline(ctx, [self._mesh_point(column, row, elapsed) for row in range(17)])
            ctx.set_source_rgba(*c['accent'], .18)
            ctx.stroke()
        for index in range(7):
            row = 3 + index * 2
            position = (elapsed * (.85 + index * .035) + index * 4.7) % 24 - 12
            direction = 1 if index % 2 == 0 else -1
            if direction < 0:
                position = -position
            points = [self._mesh_point(position - direction * .18 * step, row, elapsed)
                      for step in range(13)]
            for step in range(12):
                polyline(ctx, points[step:step + 2])
                ctx.set_source_rgba(*c['accent'], .82 * (1 - step / 12))
                ctx.set_line_width(2)
                ctx.stroke()
            px, py = points[0]
            ctx.rectangle(px - 3, py - 3, 6, 6)
            ctx.set_source_rgb(*c['accent'])
            ctx.fill()
        # Small, deliberate routing marks around the scene.
        ctx.set_source_rgba(*c['accent'], .46)
        ctx.set_line_width(1)
        for sx in (76, w - 76):
            ctx.move_to(sx, h * .21)
            ctx.line_to(sx, h * .25)
            ctx.move_to(sx - 7, h * .25)
            ctx.line_to(sx + 7, h * .25)
        ctx.stroke()

    def _typography(self, ctx, elapsed, now):
        c, w, h = self.colors, self.canvas_width, self.canvas_height
        clock, date = now.strftime('%H:%M'), self.date_text(now)
        ru, p = self.language == 'ru', self.portrait
        if self.theme == 'classic':
            x, y = (self.cx, h * .76) if p else (w * .735, h * .475)
            self.text(ctx, clock, x, y, 128, WHITE)
            self._fit(ctx, date, x, y + 53, 24, 530, WHITE, True, opacity=.63)
            self._locked(ctx, x - 150, y + 135, 25, 400, WHITE)
            self._instruction(ctx, x, y + 176, 560, True, (.61, .67, .78))
            self._connected(ctx, 62, h - 48, elapsed, MINT)
        elif self.theme == 'light':
            x, y = (76, h * .24) if p else (w * .085, h * .405)
            self._locked(ctx, x + 2, y - 166, 22, 610)
            self.text(ctx, clock, x - 9, y, 155, c['text'], centered=False)
            self._fit(ctx, date, x, y + 51, 24, 580, c['muted'])
            ctx.move_to(x, y + 101)
            ctx.line_to(x + 57, y + 101)
            ctx.set_source_rgb(*c['accent'])
            ctx.set_line_width(3)
            ctx.stroke()
            self._instruction(ctx, x, y + 153, 525)
            self._connected(ctx, 76, h - 58, elapsed)
        elif self.theme == 'midnight':
            y = h * (.26 if p else .30)
            self.text(ctx, clock, self.cx, y, 142, c['text'])
            self._fit(ctx, date, self.cx, y + 51, 22, w - 150, c['muted'], True)
            self._locked(ctx, self.cx - 150, y + 126, 24, 400, c['accent'])
            self._instruction(ctx, self.cx, y + 172, min(650, w - 150), True)
            self._connected(ctx, 62, h - 48, elapsed, c['accent'])
        elif self.theme == 'aurora':
            x, y = 82, h * (.79 if p else .775)
            self._locked(ctx, x + 3, y - 135, 23, 520)
            self.text(ctx, clock, x - 5, y, 122, c['text'], centered=False)
            self._fit(ctx, date, x, y + 46, 24, w - 165, c['muted'])
            self._instruction(ctx, x, y + 94, min(680, w - 165))
            self._connected(ctx, x, h - 49, elapsed)
        elif self.theme == 'editorial':
            self._analogue_hands(ctx, now)
            x = 80 if p else w * .078
            y = h * .205 if p else h * .37
            self._locked(ctx, x, y - (100 if p else 138), 20, 570, c['muted'], 'DejaVu Serif')
            lines = ('Время', 'выдохнуть.') if ru else ('Take a', 'breath.')
            size = 76 if p else 92
            for index, line in enumerate(lines):
                self.text(ctx, line, x - 4, y + index * (size * 1.13), size,
                          c['text'], centered=False, family='DejaVu Serif')
            digital_y = h * .83 if p else h * .69
            self.text(ctx, clock, x, digital_y, 52, c['accent'], centered=False, family='DejaVu Serif')
            self._fit(ctx, date, x, digital_y + 40, 22, min(690, w - 160), c['muted'], family='DejaVu Serif')
            self._instruction(ctx, x, digital_y + 85, min(670, w - 160))
            self._connected(ctx, 76, h - 56, elapsed, c['muted'], 'DejaVu Serif')
        elif self.theme == 'graphite':
            x, y = (82, h * .80) if p else (w * .63, h * .81)
            self._locked(ctx, 82, h * .174, 22, 600, c['accent'], 'DejaVu Sans Mono')
            self.text(ctx, clock, x, y, 124, c['accent'], centered=False, family='DejaVu Sans Mono')
            self._fit(ctx, date, x + 4, y + 43, 20, w - x - 60, c['muted'], family='DejaVu Sans Mono')
            self._instruction(ctx, 82, h - 151 if p else h * .80, 610 if p else w * .48)
            self._connected(ctx, 76, h - 55, elapsed, c['accent'], 'DejaVu Sans Mono')

    def render(self, ctx, elapsed, now=None):
        """Draw a complete opaque frame; explicit time supports offline previews."""
        now = datetime.now().astimezone() if now is None else now
        drawing = cairo.Context(self.frame_surface)
        drawing.set_operator(cairo.OPERATOR_SOURCE)
        drawing.set_source_surface(self.background, 0, 0)
        drawing.paint()
        drawing.set_operator(cairo.OPERATOR_OVER)
        drawing.scale(self.scale, self.scale)
        getattr(self, '_' + self.theme)(drawing, elapsed)
        self._brand(drawing)
        self._typography(drawing, elapsed, now)
        ctx.save()
        try:
            ctx.set_operator(cairo.OPERATOR_SOURCE)
            ctx.set_source_surface(self.frame_surface, 0, 0)
            ctx.paint()
        finally:
            ctx.restore()

    def on_draw(self, _overlay, ctx, timestamp, _duration):
        elapsed = timestamp / 1_000_000_000 if timestamp < 2**63 else time.monotonic() - self.started
        try:
            self.render(ctx, elapsed)
        except Exception as error:
            # Never expose a previous desktop frame, even if drawing fails.
            ctx.save()
            ctx.identity_matrix()
            ctx.reset_clip()
            ctx.set_operator(cairo.OPERATOR_SOURCE)
            ctx.set_source_rgb(*self.colors['bg'])
            ctx.paint()
            ctx.restore()
            if not self.draw_error_reported:
                print(f'Panelyra lock animation: {error}', file=sys.stderr, flush=True)
                self.draw_error_reported = True

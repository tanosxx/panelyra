"""A local, procedural lock scene drawn on GStreamer's clocked video frames.

No desktop pixels, network requests, GTK objects or extra timer/thread are used.
The background is cached; only small orbits, particles and text change per frame.
"""

from datetime import datetime
import math
import random
import sys
import time

import cairo

from .language import resolve_language


TAU = math.tau
MINT = (0.43, 0.93, 0.83)
VIOLET = (0.59, 0.53, 1.0)
WHITE = (0.91, 0.94, 1.0)


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
    ctx.arc(x, y, radius, 0, TAU)
    ctx.fill()


class LockScene:
    def __init__(self, width, height, language=None):
        self.width, self.height = width, height
        self.language = resolve_language(language or "auto")
        self.scale = min(width / 1600, height / 1000)
        self.canvas_width, self.canvas_height = width / self.scale, height / self.scale
        self.cx, self.cy = self.canvas_width / 2, self.canvas_height / 2
        self.started = time.monotonic()
        self.draw_error_reported = False
        rng = random.Random(27)
        self.particles = [(rng.random(), rng.random(), rng.uniform(0.8, 2.1),
                           rng.uniform(0, TAU), rng.uniform(0.10, 0.27))
                          for _ in range(58)]
        self.background = self._background()

    def _background(self):
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, self.width, self.height)
        ctx = cairo.Context(surface)
        gradient = cairo.LinearGradient(0, 0, self.width, self.height)
        gradient.add_color_stop_rgb(0, 0.030, 0.039, 0.080)
        gradient.add_color_stop_rgb(0.55, 0.019, 0.029, 0.057)
        gradient.add_color_stop_rgb(1, 0.020, 0.050, 0.065)
        ctx.set_source(gradient)
        ctx.paint()
        glow(ctx, self.width * 0.18, self.height * 0.24,
             self.width * 0.58, VIOLET, 0.16)
        glow(ctx, self.width * 0.83, self.height * 0.83,
             self.width * 0.55, MINT, 0.10)
        return surface

    @staticmethod
    def text(ctx, text, x, y, size, color=WHITE, opacity=1, centered=True):
        ctx.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(size)
        xb, _yb, width, _height, _xa, _ya = ctx.text_extents(text)
        ctx.move_to(x - width / 2 - xb if centered else x, y)
        ctx.set_source_rgba(*color, opacity)
        ctx.show_text(text)
        ctx.new_path()  # Do not connect the next shape to the text's pen position.

    def date_text(self, now):
        if self.language == "ru":
            weekdays = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
            months = ("января", "февраля", "марта", "апреля", "мая", "июня",
                      "июля", "августа", "сентября", "октября", "ноября", "декабря")
            return f"{weekdays[now.weekday()]}, {now.day} {months[now.month - 1]}"
        weekdays = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        months = ("January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December")
        return f"{weekdays[now.weekday()]}, {months[now.month - 1]} {now.day}"

    def _brand(self, ctx):
        ctx.set_line_width(2)
        rounded_rect(ctx, 58, 48, 25, 18, 4)
        ctx.set_source_rgba(*VIOLET, 0.9)
        ctx.stroke()
        rounded_rect(ctx, 76, 57, 13, 19, 3)
        ctx.set_source_rgb(*MINT)
        ctx.stroke()
        self.text(ctx, "P A N E L Y R A", 106, 68, 17, opacity=0.72, centered=False)

    def _stars(self, ctx, elapsed):
        for px, py, radius, phase, speed in self.particles:
            x = px * self.canvas_width + 11 * math.sin(phase + elapsed * speed * 0.3)
            y = (py * self.canvas_height - elapsed * speed * 8) % self.canvas_height
            alpha = 0.12 + 0.22 * (0.5 + 0.5 * math.sin(phase + elapsed * 0.65))
            ctx.set_source_rgba(*WHITE, alpha)
            ctx.arc(x, y, radius, 0, TAU)
            ctx.fill()

    def _ribbons(self, ctx, elapsed):
        # Quiet, slow-moving light trails leave the central typography clear.
        for index in range(4):
            ctx.new_path()
            for step in range(81):
                x = self.canvas_width * step / 80
                y = (self.cy + 320 + index * 20
                     + 37 * math.sin(x / 330 + elapsed * 0.13 + index * 0.28)
                     + 17 * math.sin(x / 180 - elapsed * 0.09))
                if step == 0:
                    ctx.move_to(x, y)
                else:
                    ctx.line_to(x, y)
            gradient = cairo.LinearGradient(0, 0, self.canvas_width, 0)
            gradient.add_color_stop_rgba(0, *VIOLET, 0)
            gradient.add_color_stop_rgba(0.24, *VIOLET, 0.11 - index * 0.017)
            gradient.add_color_stop_rgba(0.76, *MINT, 0.11 - index * 0.017)
            gradient.add_color_stop_rgba(1, *MINT, 0)
            ctx.set_source(gradient)
            ctx.set_line_width(1.3)
            ctx.stroke()

    def _lock(self, ctx, elapsed):
        x, y = self.cx, self.cy - 155
        breath = 0.5 + 0.5 * math.sin(elapsed * TAU / 6)
        glow(ctx, x, y, 185, VIOLET, 0.12 + 0.06 * breath)
        glow(ctx, x + 27, y + 18, 113, MINT, 0.08)

        # Two tilted orbits, each with a small moving point of light.
        for rx, ry, tilt, speed, color in ((150, 58, -0.38, 0.23, MINT),
                                         (136, 69, 0.65, -0.17, VIOLET)):
            ctx.save()
            ctx.translate(x, y)
            ctx.rotate(tilt)
            ctx.scale(1, ry / rx)
            ctx.arc(0, 0, rx, 0, TAU)
            ctx.set_source_rgba(*color, 0.20)
            ctx.set_line_width(1.1)
            ctx.stroke()
            angle = elapsed * speed
            # Follow the orbit's direction from an earlier position to the dot.
            # The counter-rotating orbit needs a decreasing-angle arc too.
            if speed >= 0:
                ctx.arc(0, 0, rx, angle - 0.55, angle)
            else:
                ctx.arc_negative(0, 0, rx, angle + 0.55, angle)
            ctx.set_source_rgba(*color, 0.6)
            ctx.set_line_width(2)
            ctx.stroke()
            ctx.restore()
            ox, oy = rx * math.cos(angle), ry * math.sin(angle)
            dot_x = x + ox * math.cos(tilt) - oy * math.sin(tilt)
            dot_y = y + ox * math.sin(tilt) + oy * math.cos(tilt)
            glow(ctx, dot_x, dot_y, 16, color, 0.48)
            ctx.arc(dot_x, dot_y, 3, 0, TAU)
            ctx.set_source_rgb(*color)
            ctx.fill()

        # A glass-like disc holds a closed lock; it never animates into unlock.
        gradient = cairo.LinearGradient(x - 60, y - 60, x + 60, y + 60)
        gradient.add_color_stop_rgba(0, 0.18, 0.18, 0.32, 0.96)
        gradient.add_color_stop_rgba(1, 0.045, 0.09, 0.13, 0.98)
        ctx.arc(x, y, 64, 0, TAU)
        ctx.set_source(gradient)
        ctx.fill_preserve()
        ctx.set_source_rgba(*WHITE, 0.20 + 0.06 * breath)
        ctx.set_line_width(1.3)
        ctx.stroke()
        ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        ctx.set_line_width(3.6)
        ctx.arc(x, y - 8, 14, math.pi, TAU)
        ctx.line_to(x + 14, y + 3)
        ctx.move_to(x - 14, y - 8)
        ctx.line_to(x - 14, y + 3)
        ctx.set_source_rgba(*WHITE, 0.94)
        ctx.stroke()
        rounded_rect(ctx, x - 22, y, 44, 32, 8)
        ctx.set_source_rgba(*WHITE, 0.92)
        ctx.stroke()
        ctx.arc(x, y + 13, 3, 0, TAU)
        ctx.set_source_rgb(*MINT)
        ctx.fill()
        ctx.move_to(x, y + 15)
        ctx.line_to(x, y + 21)
        ctx.set_line_width(2.5)
        ctx.stroke()

    def render(self, ctx, elapsed, now=None):
        """Draw a complete frame; explicit time also supports offline previews."""
        now = datetime.now().astimezone() if now is None else now
        ctx.save()
        try:
            ctx.set_operator(cairo.OPERATOR_SOURCE)
            ctx.set_source_surface(self.background, 0, 0)
            ctx.paint()
            ctx.set_operator(cairo.OPERATOR_OVER)
            ctx.scale(self.scale, self.scale)
            self._stars(ctx, elapsed)
            self._ribbons(ctx, elapsed)
            self._brand(ctx)
            self._lock(ctx, elapsed)
            self.text(ctx, now.strftime("%H:%M"), self.cx, self.cy + 63, 116)
            self.text(ctx, self.date_text(now), self.cx, self.cy + 113, 22, opacity=0.50)
            ru = self.language == "ru"
            self.text(ctx, "Экран заблокирован" if ru else "Screen locked",
                      self.cx, self.cy + 191, 28, opacity=0.90)
            self.text(ctx, "Разблокируйте компьютер, чтобы продолжить" if ru else
                      "Unlock your computer to pick up where you left off",
                      self.cx, self.cy + 232, 18, opacity=0.43)
            footer = "Подключение сохранено" if ru else "Your display is still connected"
            self.text(ctx, footer, self.cx + 10, self.canvas_height - 51, 15,
                      color=MINT, opacity=0.56)
            ctx.arc(self.cx - (105 if ru else 119), self.canvas_height - 57, 2.6, 0, TAU)
            ctx.set_source_rgba(*MINT, 0.62 + 0.17 * math.sin(elapsed * 1.05))
            ctx.fill()
        finally:
            ctx.restore()

    def on_draw(self, _overlay, ctx, timestamp, _duration):
        elapsed = timestamp / 1_000_000_000 if timestamp < 2**63 else time.monotonic() - self.started
        try:
            self.render(ctx, elapsed)
        except Exception as error:
            # A drawing failure must not expose a previous desktop frame or
            # end the lock/reconnect lifecycle. Keep sending an opaque image.
            ctx.save()
            ctx.set_operator(cairo.OPERATOR_SOURCE)
            ctx.set_source_rgb(0.025, 0.035, 0.065)
            ctx.paint()
            ctx.restore()
            if not self.draw_error_reported:
                print(f"Panelyra lock animation: {error}", file=sys.stderr, flush=True)
                self.draw_error_reported = True

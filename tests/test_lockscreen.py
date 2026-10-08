"""Offline checks for the real video-frame lock renderer (no display server)."""

from datetime import datetime, timedelta
import contextlib
import hashlib
import io
import sys
import unittest
from unittest import mock

import cairo

from usbdisplay.lockscreen import LockScene, trailing_arc
from usbdisplay.themes import THEME_IDS


NOW = datetime(2026, 10, 8, 21, 47)


def frame(scene, elapsed=7):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, scene.width, scene.height)
    ctx = cairo.Context(surface)
    # An unmistakable previous image must be fully replaced by the lock scene.
    ctx.set_source_rgba(1, 0, 0, .4)
    ctx.paint()
    scene.render(ctx, elapsed, NOW)
    surface.flush()
    return surface


def digest(surface):
    return hashlib.sha256(surface.get_data()).digest()


class LockScreenTests(unittest.TestCase):
    def test_every_theme_paints_opaque_landscape_portrait_and_wide_frames(self):
        alpha = 3 if sys.byteorder == 'little' else 0
        for theme in THEME_IDS:
            for width, height in ((640, 400), (360, 640), (640, 360)):
                with self.subTest(theme=theme, resolution=(width, height)):
                    image = frame(LockScene(width, height, 'ru', theme))
                    pixels = memoryview(image.get_data()).cast('B')
                    self.assertTrue(all(value == 255 for value in pixels[alpha::4]))

    def test_themes_are_visually_distinct(self):
        results = [digest(frame(LockScene(640, 400, 'ru', theme))) for theme in THEME_IDS]
        self.assertEqual(len(set(results)), len(THEME_IDS))

    def test_each_world_has_its_own_clock_composition(self):
        # Different palettes alone must not satisfy this requirement: clocks
        # really occupy different parts of the landscape compositions.
        anchors = set()
        for theme in THEME_IDS:
            scene = LockScene(640, 400, 'ru', theme)
            with mock.patch.object(scene, 'text', wraps=scene.text) as text:
                frame(scene)
            clocks = [call.args for call in text.call_args_list if call.args[1] == '21:47']
            self.assertEqual(len(clocks), 1)
            anchors.add((round(clocks[0][2] / scene.canvas_width, 1),
                         round(clocks[0][3] / scene.canvas_height, 1)))
        self.assertEqual(len(anchors), len(THEME_IDS))

    def test_localized_typography_fits_both_orientations(self):
        for theme in THEME_IDS:
            for language in ('ru', 'en'):
                for width, height in ((640, 400), (360, 640), (640, 360)):
                    with self.subTest(theme=theme, language=language, size=(width, height)):
                        scene = LockScene(width, height, language, theme)
                        original = scene.text

                        def checked(ctx, value, x, y, size, color=None, opacity=1,
                                    centered=True, family='DejaVu Sans'):
                            ctx.select_font_face(family, cairo.FONT_SLANT_NORMAL,
                                                 cairo.FONT_WEIGHT_NORMAL)
                            ctx.set_font_size(size)
                            xb, yb, tw, th, _xa, _ya = ctx.text_extents(value)
                            left = x - tw / 2 if centered else x + xb
                            self.assertGreaterEqual(left, 0, value)
                            self.assertLessEqual(left + tw, scene.canvas_width, value)
                            self.assertGreaterEqual(y + yb, 0, value)
                            self.assertLessEqual(y + yb + th, scene.canvas_height, value)
                            original(ctx, value, x, y, size, color, opacity, centered, family)

                        with mock.patch.object(scene, 'text', side_effect=checked):
                            frame(scene)

    def test_every_theme_animates_with_clock_held_constant(self):
        for theme in THEME_IDS:
            with self.subTest(theme=theme):
                scene = LockScene(640, 400, 'en', theme)
                first, second = frame(scene, 2), frame(scene, 7)
                self.assertNotEqual(digest(first), digest(second))
                # More than the footer status dot must move in the main artwork.
                a, b = bytes(first.get_data()), bytes(second.get_data())
                start = int(scene.height * .1) * first.get_stride()
                stop = int(scene.height * .75) * first.get_stride()
                changed = sum(x != y for x, y in zip(a[start:stop], b[start:stop]))
                self.assertGreater(changed, 100)

    def test_localized_date_and_actual_time_text(self):
        expected = {'ru': ('Четверг, 8 октября', 'Экран заблокирован', 'Подключение сохранено'),
                    'en': ('Thursday, October 8', 'Screen locked', 'Your display is still connected')}
        for language, strings in expected.items():
            scene = LockScene(640, 400, language, 'editorial')
            self.assertEqual(scene.date_text(NOW), strings[0])
            with mock.patch.object(scene, 'text', wraps=scene.text) as text:
                frame(scene)
            rendered = [call.args[1] for call in text.call_args_list]
            self.assertIn('21:47', rendered)
            for value in strings:
                self.assertIn(value, rendered)
        self.assertNotEqual(digest(frame(LockScene(640, 400, 'ru'))),
                            digest(frame(LockScene(640, 400, 'en'))))

    def test_unknown_theme_is_classic_and_background_is_cached(self):
        for theme in ('future-theme', None, ['invalid']):
            scene = LockScene(640, 400, 'en', theme)
            self.assertEqual(scene.theme, 'classic')
            self.assertEqual(digest(frame(scene)), digest(frame(LockScene(640, 400, 'en'))))
        scene = LockScene(640, 400, 'en', 'aurora')
        background = scene.background
        with mock.patch.object(scene, '_background', side_effect=AssertionError('Recached per frame')):
            frame(scene, 2)
            frame(scene, 3)
        self.assertIs(scene.background, background)

    def test_editorial_analogue_hands_follow_real_time(self):
        scene = LockScene(640, 400, 'en', 'editorial')
        images = []
        for now in (NOW, NOW + timedelta(hours=2, minutes=13)):
            surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 640, 400)
            scene.render(cairo.Context(surface), 7, now)
            surface.flush()
            images.append(bytes(surface.get_data()))
        # Crop only the interior of the analogue clock, away from all text and
        # decorative arcs. Frozen animation must still show changed hands.
        x, y, radius = scene._editorial_geometry()
        x, y, radius = (round(value * scene.scale) for value in (x, y, radius * .6))
        crops = [b''.join(pixels[(yy * 640 + x - radius) * 4:(yy * 640 + x + radius) * 4]
                          for yy in range(y - radius, y + radius)) for pixels in images]
        self.assertNotEqual(crops[0], crops[1])

    def test_planet_occludes_far_side_orbit_trail(self):
        scene = LockScene(640, 400, 'en', 'classic')
        normal = frame(scene, 30)
        with mock.patch('usbdisplay.lockscreen.trailing_arc'):
            without_trail = frame(scene, 30)
        a, b = bytes(normal.get_data()), bytes(without_trail.get_data())
        self.assertNotEqual(a, b)  # The trail is still visible outside the planet.
        x, y, radius = scene._planet_geometry()
        x, y, radius = x * scene.scale, y * scene.scale, (radius - 4) * scene.scale
        for yy in range(int(y - radius), int(y + radius)):
            for xx in range(int(x - radius), int(x + radius)):
                if (xx - x) ** 2 + (yy - y) ** 2 < radius ** 2:
                    offset = yy * normal.get_stride() + xx * 4
                    self.assertEqual(a[offset:offset + 4], b[offset:offset + 4])

    def test_failed_drawing_replaces_previous_frame_and_logs_once(self):
        alpha = 3 if sys.byteorder == 'little' else 0
        for theme in THEME_IDS:
            with self.subTest(theme=theme):
                scene = LockScene(64, 40, 'en', theme)
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 40)
                ctx = cairo.Context(surface)
                ctx.translate(4, 3)
                ctx.rectangle(0, 0, 17, 11)
                ctx.clip()
                original_matrix = tuple(ctx.get_matrix())
                original_clip = ctx.copy_clip_rectangle_list()

                def broken_draw(context, _elapsed):
                    context.save()
                    context.save()
                    context.translate(23, 18)
                    context.rectangle(0, 0, 3, 2)
                    context.clip()
                    context.set_source_rgba(1, 0, 1, .4)
                    context.paint()
                    raise RuntimeError('intentional failure')

                errors = io.StringIO()
                with mock.patch.object(scene, '_' + theme, side_effect=broken_draw), \
                        contextlib.redirect_stderr(errors):
                    scene.on_draw(None, ctx, 2_000_000_000, 0)
                    scene.on_draw(None, ctx, 3_000_000_000, 0)
                surface.flush()
                pixels = bytes(surface.get_data())
                self.assertTrue(all(value == 255 for value in pixels[alpha::4]))
                self.assertEqual(len({pixels[index:index + 4]
                                      for index in range(0, len(pixels), 4)}), 1)
                self.assertEqual(errors.getvalue().count('intentional failure'), 1)
                self.assertEqual(tuple(ctx.get_matrix()), original_matrix)
                self.assertEqual(ctx.copy_clip_rectangle_list(), original_clip)

    def test_orbit_trails_end_at_point_and_start_behind_both_directions(self):
        for speed in (.23, -.17):
            with self.subTest(speed=speed):
                ctx = mock.Mock()
                angle = 1.7
                trailing_arc(ctx, 150, angle, speed)
                method = ctx.arc if speed > 0 else ctx.arc_negative
                other = ctx.arc_negative if speed > 0 else ctx.arc
                method.assert_called_once()
                other.assert_not_called()
                _x, _y, radius, start, end = method.call_args.args
                self.assertEqual(radius, 150)
                self.assertEqual(end, angle)
                self.assertGreater((end - start) * speed, 0)
                self.assertAlmostEqual(abs(end - start), .55)

    def test_invalid_dimensions_fail_before_any_drawing(self):
        for width, height in ((0, 40), (64, 0), (-1, 40)):
            with self.assertRaises(ValueError):
                LockScene(width, height)


if __name__ == '__main__':
    unittest.main()

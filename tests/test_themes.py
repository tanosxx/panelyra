"""Palette legibility, scoped GTK CSS and deterministic layout previews."""

import hashlib
import re
import unittest

from usbdisplay.themes import CSS, DEFAULT_THEME, THEMES, THEME_IDS, get_theme

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk
except (ImportError, ValueError):
    Gtk = None


def contrast(first, second):
    def luminance(color):
        values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                  for value in values]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    lo, hi = sorted((luminance(first), luminance(second)))
    return (hi + .05) / (lo + .05)


class ThemeCatalogTests(unittest.TestCase):
    def test_stable_ids_and_bilingual_catalog(self):
        self.assertEqual(THEME_IDS, ('classic', 'light', 'midnight', 'aurora', 'editorial', 'graphite'))
        self.assertEqual(get_theme('removed-theme').id, DEFAULT_THEME)
        self.assertEqual(len({theme.layout for theme in THEMES.values()}), 6)
        for theme in THEMES.values():
            self.assertEqual(len(theme.name), 2)
            self.assertEqual(len(theme.description), 2)
            for color in theme.colors.values():
                self.assertRegex(color, r'^#[0-9a-f]{6}$')

    def test_body_and_secondary_text_remain_legible_on_cards(self):
        for theme in THEMES.values():
            with self.subTest(theme=theme.id):
                for foreground in ('text', 'muted'):
                    for background in ('bg', 'surface'):
                        self.assertGreaterEqual(contrast(theme.colors[foreground], theme.colors[background]), 4.5)
                # The original theme deliberately retains its existing violet button.
                if theme.id != 'classic':
                    self.assertGreaterEqual(contrast(theme.colors['accent'], theme.colors['accent_text']), 4.5)

    def test_every_css_selector_is_scoped_to_one_panelyra_theme(self):
        selectors = [selector.strip() for group in re.findall(r'([^{}]+)\{[^{}]*\}', CSS.decode())
                     for selector in group.split(',')]
        self.assertGreater(len(selectors), 100)
        for selector in selectors:
            self.assertRegex(selector, r'^\.panelyra\.theme-(' + '|'.join(THEME_IDS) + r')(?:[ .:]|$)')

    @unittest.skipIf(Gtk is None, 'GTK 3 is not installed')
    def test_all_css_parses_without_errors_or_deprecated_properties(self):
        provider = Gtk.CssProvider()
        errors = []
        provider.connect('parsing-error', lambda provider, section, error: errors.append(str(error)))
        provider.load_from_data(CSS)
        self.assertEqual(errors, [])

    @unittest.skipIf(Gtk is None, 'GTK 3 is not installed')
    def test_each_preview_renders_a_distinct_layout_and_both_languages(self):
        try:
            import cairo
            from usbdisplay.theme_preview import draw_preview
        except ImportError:
            self.skipTest('Cairo GTK bindings are not installed')
        hashes = []
        for theme_id in THEME_IDS:
            for language in ('en', 'ru'):
                for width, height in ((176, 125), (640, 400)):
                    with self.subTest(theme=theme_id, language=language, size=(width, height)):
                        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
                        draw_preview(cairo.Context(surface), theme_id, width, height, language, width < 200)
                        surface.flush()
                        rendered = bytes(surface.get_data())
                        self.assertTrue(any(rendered))
                        hashes.append(hashlib.sha256(rendered).digest())
        self.assertEqual(len(set(hashes)), len(hashes))

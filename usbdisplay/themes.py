"""Panelyra's built-in visual themes, independent of GTK and user preferences.

Every CSS rule is scoped to a themed Panelyra root. Popovers and auxiliary
windows receive the same two classes as the main window from the launcher.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    id: str
    name: tuple[str, str]
    description: tuple[str, str]
    layout: str
    colors: dict[str, str]
    radius: int


DEFAULT_THEME = 'classic'
THEMES = {
    'classic': Theme(
        'classic', ('Panelyra original', 'Классическая Panelyra'),
        ('The familiar midnight blue, soft cards and horizontal navigation.',
         'Знакомый тёмно-синий интерфейс, мягкие карточки и вкладки сверху.'),
        'classic',
        dict(bg='#111927', surface='#1a2435', raised='#29364c', text='#f5f7fc',
             muted='#a5b1c6', accent='#746bff', accent_text='#f5f7fc',
             border='#3b4860', good='#83edd6'), 16),
    'light': Theme(
        'light', ('Light minimal', 'Светлый минимализм'),
        ('Airy white surfaces, lavender accents and a quiet, focused workspace.',
         'Светлые поверхности, лавандовые акценты и спокойная рабочая область.'),
        'stacked',
        dict(bg='#f3f3f9', surface='#ffffff', raised='#e9e7f3', text='#262334',
             muted='#645e73', accent='#6d4dd3', accent_text='#ffffff',
             border='#d7d3e3', good='#21705b'), 20),
    'midnight': Theme(
        'midnight', ('Dark studio', 'Тёмная студия'),
        ('A navy studio with side navigation, violet controls and mint device signals.',
         'Тёмно-синяя студия с боковой навигацией, фиолетовыми кнопками и мятными индикаторами.'),
        'studio',
        dict(bg='#0b1722', surface='#112130', raised='#1d2a40', text='#f3f5ff',
             muted='#a1b3d5', accent='#6554dc', accent_text='#ffffff',
             border='#2a3b54', good='#5ef0ca'), 12),
    'aurora': Theme(
        'aurora', ('Aurora glass', 'Аврора / стекло'),
        ('Teal and lilac gradients, translucent-looking cards and a softer rhythm.',
         'Мятные и сиреневые переливы, лёгкие карточки и мягкий ритм интерфейса.'),
        'glass',
        dict(bg='#bfe3f3', surface='#e9f5fb', raised='#d4e5f5', text='#112442',
             muted='#38516e', accent='#69eccd', accent_text='#102e41',
             border='#95b8d2', good='#12654e'), 24),
    'editorial': Theme(
        'editorial', ('Warm editorial', 'Тёплый редакционный'),
        ('Cream paper, terracotta details and a generous typographic hierarchy.',
         'Кремовый фон, терракотовые детали и выразительная типографика.'),
        'editorial',
        dict(bg='#f5f2eb', surface='#fcfaf5', raised='#eae5db', text='#242726',
             muted='#64605a', accent='#a94730', accent_text='#ffffff',
             border='#bebbb2', good='#466738'), 6),
    'graphite': Theme(
        'graphite', ('Graphite console', 'Графитовая панель'),
        ('Compact geometry, lime accents and precise, monospaced details.',
         'Компактная геометрия, лаймовые акценты и чёткие моноширинные детали.'),
        'console',
        dict(bg='#181f24', surface='#1e282e', raised='#263139', text='#edf2f7',
             muted='#adbac8', accent='#b9e86a', accent_text='#202b11',
             border='#3b4a55', good='#b9e86a'), 3),
}
THEME_IDS = tuple(THEMES)


def get_theme(theme_id):
    """Treat stale or unknown IDs as the familiar default design."""
    return THEMES.get(theme_id, THEMES[DEFAULT_THEME])


def mix(first, second, amount):
    """Mix two catalog hex colors; useful for both CSS and Cairo previews."""
    a = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
    b = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
    return '#' + ''.join(f'{round(x * (1 - amount) + y * amount):02x}'
                         for x, y in zip(a, b))


def _theme_css(theme):
    c = theme.colors
    root = '.panelyra.theme-' + theme.id
    r = theme.radius
    button_radius = min(r, 10)
    hover = mix(c['raised'], c['accent'], .15)
    tint = mix(c['surface'], c['accent'], .13)
    good_tint = mix(c['surface'], c['good'], .15)
    error = '#a33143' if theme.id in ('light', 'aurora', 'editorial') else '#ffb0bf'
    error_tint = mix(c['surface'], error, .13)
    # Explicit foregrounds avoid inheriting incompatible system-theme colors.
    # GTK popovers and menus also receive this root class pair from the launcher.
    rules = f'''
{root} {{ background: {c['bg']}; color: {c['text']}; }}
{root} headerbar {{ background: {c['bg']}; color: {c['text']}; border-bottom: 1px solid {c['border']}; box-shadow: none; }}
{root} label {{ color: {c['text']}; text-shadow: none; }}
{root} .headline {{ font-size: 24px; font-weight: 800; }}
{root} .section-title {{ font-size: 16px; font-weight: 700; }}
{root} .muted, {root} .subtitle {{ color: {c['muted']}; }}
{root} .tiny {{ font-size: 11px; }}
{root} .eyebrow {{ font-size: 10px; font-weight: 700; letter-spacing: 2px; color: {c['good']}; }}
{root} .hero {{ background: linear-gradient(125deg, {tint}, {c['surface']}); border: 1px solid {c['border']}; border-radius: {r + 4}px; }}
{root} .card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r}px; }}
{root} .badge {{ background: {good_tint}; color: {c['good']}; border-radius: {min(r, 12)}px; padding: 5px 12px; font-weight: 600; }}
{root} .badge.error {{ background: {error_tint}; color: {error}; }}
{root} .badge.busy {{ background: {tint}; color: {c['accent']}; }}
{root} button {{ background: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: {button_radius}px; padding: 9px 15px; box-shadow: none; text-shadow: none; }}
{root} button label, {root} button image {{ color: inherit; }}
{root} button:hover {{ background: {hover}; }}
{root} button:active, {root} button:checked {{ background: {tint}; border-color: {c['accent']}; }}
{root} button:disabled {{ opacity: .45; }}
{root} button:focus, {root} entry:focus, {root} spinbutton:focus {{ border-color: {c['accent']}; outline-color: {c['accent']}; outline-offset: 2px; outline-style: dashed; outline-width: 1px; }}
{root} button.primary {{ background: {c['accent']}; border-color: {c['accent']}; color: {c['accent_text']}; font-weight: 700; }}
{root} button.primary:hover {{ background: {mix(c['accent'], c['text'], .1)}; }}
{root} button.stop {{ background: {error_tint}; border-color: {mix(c['border'], error, .4)}; color: {error}; }}
{root} button.link {{ background: transparent; border-color: transparent; color: {c['accent']}; padding: 3px 0; }}
{root} entry, {root} spinbutton {{ background: {c['bg']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: {min(r, 8)}px; box-shadow: none; caret-color: {c['text']}; }}
{root} entry selection, {root} textview text selection {{ background: {c['accent']}; color: {c['accent_text']}; }}
{root} spinbutton entry {{ min-width: 40px; padding: 6px; margin: 0; border: none; box-shadow: none; }}
{root} spinbutton button {{ min-width: 26px; min-height: 30px; padding: 0; margin: 0; border-radius: 0; border-width: 0; border-left-width: 1px; box-shadow: none; }}
{root} spinbutton button.up {{ border-radius: 0 {min(r, 7)}px {min(r, 7)}px 0; }}
{root} combobox button {{ padding: 7px 10px; }}
{root} combobox arrow {{ color: {c['muted']}; }}
{root} checkbutton, {root} radiobutton {{ color: {c['text']}; }}
{root} check, {root} radio {{ background: {c['bg']}; color: {c['text']}; border: 1px solid {c['border']}; box-shadow: none; }}
{root} check:checked, {root} radio:checked {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
{root} switch {{ background: {c['raised']}; color: {c['muted']}; border: 1px solid {c['border']}; }}
{root} switch:checked {{ background: {c['accent']}; color: {c['accent_text']}; }}
{root} switch slider {{ background: {c['surface']}; border-color: {c['border']}; }}
{root} scale trough {{ background: {c['raised']}; border: 1px solid {c['border']}; }}
{root} scale highlight {{ background: {c['accent']}; border-color: {c['accent']}; }}
{root} scale slider {{ background: {c['accent']}; border: 1px solid {c['accent']}; box-shadow: none; }}
{root} scale value {{ color: {c['muted']}; }}
{root} .device-name {{ font-size: 15px; font-weight: 700; }}
{root} .device-detail {{ color: {c['muted']}; font-size: 12px; }}
{root} button.internet-toggle, {root} button.compact {{ padding: 6px 10px; }}
{root} .page-switcher button {{ background: transparent; border-color: transparent; padding: 9px 18px; color: {c['muted']}; }}
{root} .page-switcher button:checked {{ background: {tint}; border-color: {mix(c['border'], c['accent'], .3)}; color: {c['text']}; }}
{root} .theme-sidebar {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r}px; padding: 8px; }}
{root} .theme-sidebar button {{ background: transparent; border-color: transparent; color: {c['muted']}; padding: 10px 12px; }}
{root} .theme-sidebar button:checked {{ background: {tint}; border-color: {mix(c['border'], c['accent'], .3)}; color: {c['accent']}; }}
{root} .theme-sidebar row {{ background: transparent; color: {c['muted']}; border-radius: {button_radius}px; padding: 10px 12px; }}
{root} .theme-sidebar row:hover {{ background: {hover}; }}
{root} .theme-sidebar row:selected {{ background: {tint}; color: {c['accent']}; }}
{root} .theme-sidebar row:selected label {{ color: {c['accent']}; }}
{root} scrollbar {{ background: transparent; }}
{root} scrollbar slider {{ background: {mix(c['raised'], c['muted'], .35)}; border: none; border-radius: 6px; min-width: 6px; min-height: 30px; }}
{root} scrollbar slider:hover {{ background: {c['muted']}; }}
{root} scrolledwindow, {root} viewport, {root} list, {root} flowbox {{ background: transparent; }}
{root} separator {{ background: {c['border']}; min-width: 1px; min-height: 1px; }}
{root} expander arrow {{ color: {c['muted']}; }}
{root} textview, {root} textview text {{ background: {c['bg']}; color: {c['muted']}; font-family: monospace; font-size: 11px; }}
{root} .step-number {{ background: {tint}; color: {c['accent']}; border-radius: {min(r, 10)}px; padding: 5px 10px; font-weight: 800; }}
{root}.notification-popover, {root} popover, {root} menu, {root}.background {{ background: {c['bg']}; color: {c['text']}; }}
{root}.notification-popover {{ border: 1px solid {c['border']}; }}
{root} .notification-count {{ background: {c['accent']}; color: {c['accent_text']}; border-radius: 9px; padding: 1px 5px; font-size: 11px; font-weight: 700; }}
{root} .notification-card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {min(r, 10)}px; padding: 10px; }}
{root} .notification-unread {{ border-left: 3px solid {c['accent']}; }}
{root} menuitem {{ background: transparent; color: {c['text']}; }}
{root} menuitem:hover {{ background: {tint}; }}
{root} .theme-choice {{ background: {c['surface']}; border: 2px solid {c['border']}; border-radius: {r}px; padding: 10px; }}
{root} .theme-choice:hover {{ background: {c['surface']}; border-color: {c['muted']}; }}
{root} .theme-choice:selected, {root} .theme-choice:checked, {root} .theme-choice.active-theme {{ background: {tint}; border-color: {c['accent']}; }}
{root} .theme-choice .theme-name {{ font-size: 13px; font-weight: 700; }}
{root} flowboxchild, {root} flowboxchild:selected {{ background: transparent; border: none; }}
{root} .theme-state {{ color: {c['accent']}; font-size: 11px; font-weight: 600; }}
{root} .appearance-status.error {{ color: {error}; }}
{root} .theme-description {{ color: {c['muted']}; font-size: 12px; }}
{root} .appearance-preview {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: {r}px; }}
{root} .appearance-detail {{ color: {c['muted']}; }}
{root} .compact-hero .headline {{ font-size: 22px; }}
{root} .overview-strip label {{ font-size: 12px; }}
{root} button.appearance-choice-row {{ padding: 6px 10px; }}
{root} .appearance-choice-row label {{ font-size: 12px; }}
{root} .appearance-preview-switcher button {{ padding: 5px 10px; font-size: 12px; }}
{root} .theme-active-badge {{ background: {good_tint}; color: {c['good']}; border-radius: {min(r, 8)}px; padding: 3px 7px; font-size: 11px; }}
'''
    if theme.id != 'classic':
        rules += f'''
{root} .picture-content .muted {{ font-size: 12px; }}
{root} .picture-content combobox button {{ min-height: 24px; padding: 4px 9px; }}
{root} .picture-content spinbutton {{ min-height: 32px; padding: 0; }}
{root} .picture-content spinbutton entry {{ min-height: 28px; padding: 1px 6px; }}
{root} .picture-content spinbutton button {{ min-height: 30px; min-width: 24px; padding: 0; }}
{root} .picture-content scale {{ min-height: 24px; padding: 3px 10px; }}
{root} .picture-content scale trough, {root} .picture-content scale highlight {{ min-height: 4px; }}
{root} .picture-content scale slider {{ min-width: 18px; min-height: 18px; margin: -7px; padding: 0; border-width: 0; }}
{root} .connection-device button.compact {{ min-height: 22px; padding: 3px 8px; }}
'''
    if theme.id == 'classic':
        rules += f'''
{root} .hero {{ background: linear-gradient(125deg, #282946, #1b2c3b); border-color: #3b4263; }}
{root} button.link {{ color: #a9a4ff; }}
{root} .theme-state {{ color: #a9a4ff; }}
{root} .badge.busy {{ color: #c9c0ff; }}
{root} .step-number {{ color: #bfb7ff; }}
'''
    elif theme.id == 'midnight':
        rules += f'''
{root} .theme-sidebar {{ background: #0c1926; border-radius: 12px; padding: 6px; }}
{root} .theme-sidebar-brand {{ color: #f3f5ff; font-weight: 800; font-size: 19px; }}
{root} .theme-sidebar row {{ padding: 11px 8px; }}
{root} .theme-sidebar row:selected {{ background: #2c2856; color: #f3f5ff; }}
{root} .theme-sidebar row:selected label {{ color: #f3f5ff; }}
{root} .card {{ background: linear-gradient(115deg, #10202e, #122130); border-radius: 12px; }}
{root} .connection-device .device-name {{ font-size: 20px; font-weight: 800; }}
{root} .connection-device .section-title, {root} .picture-card .section-title {{ color: #a1b3d5; font-size: 13px; font-weight: 600; }}
{root} .badge {{ background: #12372f; color: #7ef3d8; border: 1px solid #318477; }}
{root} .stream-footer {{ background: #112130; border: 1px solid #2a3b54; border-radius: 12px; padding: 0; }}
{root} button.primary {{ background: linear-gradient(110deg, #6c5be3, #5847cb); }}
{root} .theme-state, {root} button.link {{ color: #b5adff; }}
'''
    elif theme.id == 'aurora':
        rules += f'''
{root}, {root}.background {{ background: linear-gradient(125deg, #91cbe5 0%, #c7f1f1 38%, #becbee 73%, #a4dce7 100%); }}
{root} headerbar {{ background: alpha(#eaf7ff, .53); border-bottom-color: alpha(#ffffff, .65); }}
{root} .hero, {root} .theme-hero {{ background: transparent; border: none; border-radius: 0; }}
{root} .hero .headline {{ font-size: 33px; font-weight: 900; }}
{root} .card {{ background: linear-gradient(115deg, alpha(#ffffff, .78), alpha(#eaf1ff, .69)); border: 1px solid alpha(#ffffff, .86); border-radius: 23px; }}
{root} .page-switcher {{ background: alpha(#eef9ff, .45); border-radius: 24px; padding: 4px; }}
{root} .page-switcher button {{ border-radius: 22px; padding: 7px 14px; color: #324762; }}
{root} .page-switcher button:checked {{ background: alpha(#ffffff, .84); color: #112442; border-color: #f6fdff; }}
{root} .badge {{ background: #cbf2e5; color: #12654e; }}
{root} .eyebrow {{ color: #38516e; }}
{root} button.link, {root} .theme-state {{ color: #214c75; }}
{root} button.primary {{ background: linear-gradient(110deg, #82f3d9, #5de6c5); color: #102e41; border-color: #c3ffed; }}
{root} entry, {root} spinbutton, {root} combobox button {{ background: alpha(#f8fcff, .79); border-color: #a9c4d9; }}
{root} .stream-footer {{ background: alpha(#eff8ff, .47); border: 1px solid alpha(#ffffff, .7); border-radius: 22px; padding: 0; }}
{root} .appearance-preview {{ background: alpha(#f4fbff, .78); border-color: #effcff; }}
'''
    elif theme.id == 'editorial':
        rules += f'''
{root} headerbar {{ background: #282c2b; color: #faf8f2; border-color: #282c2b; }}
{root} headerbar label, {root} headerbar image {{ color: #faf8f2; }}
{root} headerbar .subtitle, {root} headerbar .muted {{ color: #c7ccc8; }}
{root} headerbar button {{ background: #333736; color: #faf8f2; border-color: #4b4e4b; }}
{root} headerbar button:hover {{ background: #444944; }}
{root} .headline {{ font-family: sans-serif; font-size: 42px; font-weight: 900; }}
{root} .hero .headline {{ letter-spacing: -1px; }}
{root}.short-window .hero .headline {{ font-size: 36px; }}
{root} .section-title {{ font-family: sans-serif; font-size: 16px; font-weight: 800; }}
{root} .hero, {root} .theme-hero {{ background: transparent; border: none; border-radius: 0; }}
{root} .hero .muted {{ color: #51545b; font-size: 14px; }}
{root} .card {{ background: #fcfaf5; border: 1px solid #b5b6b1; border-radius: 7px; box-shadow: none; }}
{root} .page-switcher button {{ border-radius: 4px; padding: 8px 18px; }}
{root} .page-switcher button:checked {{ background: #e9e4dc; color: #242726; border-color: transparent; }}
{root} .connection-device .device-name {{ font-size: 20px; font-weight: 800; }}
{root} .badge {{ background: #e2ead8; color: #355c25; border-radius: 8px; }}
{root} .stream-footer {{ background: #282c2b; border: 1px solid #282c2b; border-radius: 8px; padding: 2px; }}
{root} .stream-footer label {{ color: #f8f6ef; }}
{root} .stream-footer .badge {{ background: #e2ead8; color: #355c25; }}
{root} .stream-footer .badge.error {{ background: #f5dce0; color: #a33143; }}
{root} .stream-footer .badge.busy {{ background: #eeded8; color: #88331f; }}
{root} .stream-footer button.stop {{ background: #393d3c; border-color: #5b605d; color: #f0ebe4; }}
{root} .stream-footer button.primary {{ background: linear-gradient(110deg, #b15036, #a3462d); color: #ffffff; }}
'''
    elif theme.id == 'graphite':
        rules += f'''
{root} headerbar {{ background: #1b252c; }}
{root} .headline {{ font-size: 22px; font-weight: 800; }}
{root} .eyebrow, {root} .device-detail, {root} .badge, {root} .mode-value, {root} spinbutton entry {{ font-family: monospace; }}
{root} .hero {{ background: {c['surface']}; border-left: 3px solid {c['accent']}; }}
{root} button {{ padding: 7px 12px; border-radius: 3px; }}
{root} .card {{ border-radius: 3px; }}
{root} .section-title {{ font-size: 15px; font-weight: 800; }}
{root} .connection-device .device-name {{ font-size: 21px; }}
{root} .badge {{ background: transparent; border: none; border-radius: 0; padding: 3px 0; }}
{root} .page-switcher button {{ border-radius: 3px; border-bottom-width: 2px; }}
{root} .page-switcher button:checked {{ background: #293b29; color: #d5f3a3; border-color: transparent; border-bottom-color: #b9e86a; }}
{root} .stream-footer {{ border: 1px solid #3b4a55; background: #1e282e; border-radius: 3px; padding: 0; }}
{root} button.primary {{ background: linear-gradient(110deg, #c2ed83, #b4e362); }}
'''
    elif theme.id == 'light':
        rules += f'''
{root}, {root}.background {{ background: #f7f8fb; }}
{root} headerbar {{ background: #f0f1f5; border-color: #dde0e7; }}
{root} .hero, {root} .theme-hero {{ background: transparent; border: none; box-shadow: none; }}
{root} .hero .headline {{ font-size: 31px; font-weight: 800; }}
{root}.short-window .hero .headline {{ font-size: 27px; }}
{root} .hero .muted {{ color: #606779; }}
{root} .card {{ background: #ffffff; border-color: #dce0ea; border-radius: 12px; box-shadow: 0 2px 3px alpha(#51416b, .03); }}
{root} .page-switcher {{ background: #f0f1f5; border: 1px solid #d6d9e2; border-radius: 12px; }}
{root} .page-switcher button {{ padding: 9px 18px; border-radius: 10px; }}
{root} .page-switcher button:checked {{ background: #ebe6ff; color: #5533bd; border-color: #ccc0f5; }}
{root} .connection-device .device-name {{ font-size: 18px; font-weight: 800; }}
{root} .badge {{ background: #dbf3e5; color: #216345; }}
{root} button.primary {{ background: linear-gradient(110deg, #7558df, #6849cb); }}
{root} entry, {root} spinbutton, {root} combobox button {{ background: #ffffff; border-color: #d4d5df; }}
'''
    return rules


def build_css():
    return '\n'.join(_theme_css(theme) for theme in THEMES.values()).encode('utf-8')


CSS = build_css()

#!/usr/bin/env python3
"""Render Panelyra's editable SVG into legacy and adaptive launcher assets.

Uses system python3-gi, python3-cairo and gir1.2-rsvg-2.0 on Ubuntu.
No downloaded graphics or runtime image libraries are included in the APK.
"""
from pathlib import Path
import xml.etree.ElementTree as ET
import cairo
import gi

gi.require_version("Rsvg", "2.0")
from gi.repository import Rsvg

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "usbdisplay/assets/panelyra.svg"
MAIN = ROOT / "android/app/src/main/res"
MODERN = ROOT / "android/app/src/modern/res"


def render(data, path, size, scale=1.0):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    context = cairo.Context(surface)
    context.translate(size * (1 - scale) / 2, size * (1 - scale) / 2)
    handle = Rsvg.Handle.new_from_data(data)
    viewport = Rsvg.Rectangle()
    viewport.x = viewport.y = 0
    viewport.width = viewport.height = size * scale
    handle.render_document(context, viewport)
    path.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(path))


svg = SOURCE.read_bytes()
for density, size in [("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)]:
    render(svg, MAIN / ("mipmap-" + density) / "ic_launcher.png", size)
# Remove the square background: Android supplies and masks the adaptive background.
# Keep the symbol comfortably inside the circular safe area on launcher masks.
root = ET.fromstring(svg)
for element in list(root):
    if element.get("fill") == "url(#base)":
        root.remove(element)
render(ET.tostring(root), MODERN / "drawable-nodpi/ic_launcher_foreground.png", 432, .80)
print("Rendered Panelyra launcher assets")

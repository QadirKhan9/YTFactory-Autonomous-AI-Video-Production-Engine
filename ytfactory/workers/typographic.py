"""Rung-7 typographic card generator (Pillow) — the guaranteed non-failing
visual fallback (prd AC-11 / risk R-03).

Renders a brand-styled 1920x1080 PNG headline card. Colors are derived
deterministically from the headline hash so the same scene always renders
the same card (cacheable, testable).
"""
from __future__ import annotations

import hashlib
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CARD_SIZE = (1920, 1080)

# (background, accent, text) — contrast pairs checked against WCAG-ish norms.
_THEMES = [
    ((16, 22, 38), (232, 163, 61), (245, 246, 250)),
    ((24, 16, 36), (120, 180, 255), (245, 246, 250)),
    ((10, 30, 28), (90, 210, 170), (240, 250, 246)),
    ((36, 12, 16), (255, 120, 110), (250, 244, 242)),
    ((18, 18, 18), (255, 214, 90), (240, 240, 240)),
    ((12, 26, 46), (255, 140, 190), (238, 244, 250)),
]

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",   # Segoe UI Bold
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "C:/Windows/Fonts/consladb.ttf",
]


def theme_for(headline: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    digest = hashlib.sha1(headline.encode("utf-8")).digest()
    return _THEMES[digest[0] % len(_THEMES)]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1 bitmap fallback
    except TypeError:
        return ImageFont.load_default()


def _wrap(text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
    words = " ".join(text.split())
    lines: list[str] = []
    current = ""
    for word in words.split(" "):
        candidate = f"{current} {word}".strip()
        if font.getbbox(candidate)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    # hard-split any monster word that still overflows
    fixed: list[str] = []
    for line in lines:
        if font.getbbox(line)[2] > max_width:
            fixed.extend(textwrap.wrap(line, width=max(8, len(line) // 2)) or [line])
        else:
            fixed.append(line)
    return fixed[:max_lines]


def render_card(
    headline: str,
    out_path: str | Path,
    kicker: str | None = None,
    size: tuple[int, int] = CARD_SIZE,
) -> Path:
    """Render a headline card PNG and return its path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    bg, accent, text_color = theme_for(headline)

    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)

    # accent bar (left edge) + bottom rule
    bar_w = max(12, width // 120)
    draw.rectangle([0, 0, bar_w, height], fill=accent)
    draw.rectangle([bar_w * 3, height - bar_w * 4, width - bar_w * 3, height - bar_w * 3], fill=accent)

    if kicker:
        kicker_font = _font(max(28, width // 42))
        k = kicker.upper()[:64]
        draw.text((bar_w * 6, height // 12), k, font=kicker_font, fill=accent)

    # headline: scale font so ~3-4 lines fill the middle band
    max_text_width = width - bar_w * 12
    for size_pt in (width // 9, width // 12, width // 15, width // 18, width // 24):
        font = _font(size_pt)
        lines = _wrap(headline, font, max_text_width, max_lines=4)
        line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        if len(lines) * line_h <= height * 0.55 and len(lines) <= 4:
            break
    text_block_h = len(lines) * line_h
    y = (height - text_block_h) // 2
    for line in lines:
        draw.text((bar_w * 6, y), line, font=font, fill=text_color)
        y += line_h

    img.save(out_path, "PNG")
    return out_path

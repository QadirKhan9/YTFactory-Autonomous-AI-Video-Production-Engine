"""1280×720 PNG thumbnail renderer (FR-P-03) for S18.

Primary: Playwright + Chromium renders an HTML/CSS template (deterministic
typography). Fallback: Pillow paints the same layout when Chromium is absent
(user decision: Playwright with Pillow fallback). Both paths are free and
produce exactly 1280×720 PNG ≤ 2 MB.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 720
MAX_BYTES = 2 * 1024 * 1024

PALETTES: dict[str, dict[str, str]] = {
    "midnight": {"bg": "#0b1026", "accent": "#4f8cff", "fg": "#f5f7ff", "sub": "#9fb3e8"},
    "ember": {"bg": "#1c0a06", "accent": "#ff7a29", "fg": "#fff4ec", "sub": "#e8b79f"},
    "ocean": {"bg": "#04202b", "accent": "#2ec4b6", "fg": "#eefcfb", "sub": "#93c9c3"},
    "ivory": {"bg": "#f4efe3", "accent": "#b23a48", "fg": "#22201b", "sub": "#6d675c"},
    "forest": {"bg": "#0d1f14", "accent": "#7dd87d", "fg": "#f0fbf0", "sub": "#a3c9aa"},
}

_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ width: {w}px; height: {h}px; overflow: hidden; }}
  body {{ background: {bg}; font-family: 'Segoe UI', Arial, sans-serif;
          display: flex; flex-direction: column; justify-content: center;
          padding: 72px 80px; position: relative; }}
  .bar {{ position: absolute; left: 0; top: 0; width: 22px; height: 100%;
          background: {accent}; }}
  .kicker {{ color: {accent}; font-size: 30px; font-weight: 700;
             letter-spacing: 6px; text-transform: uppercase; margin-bottom: 26px; }}
  .headline {{ color: {fg}; font-size: 108px; line-height: 1.04; font-weight: 800;
               text-transform: uppercase; }}
  .subhead {{ color: {sub}; font-size: 40px; font-weight: 500; margin-top: 30px; }}
  .dots {{ position: absolute; right: 64px; bottom: 52px; color: {sub};
           font-size: 26px; letter-spacing: 3px; opacity: .75; }}
</style></head>
<body>
  <div class="bar"></div>
  <div class="kicker">{kicker}</div>
  <div class="headline">{headline}</div>
  <div class="subhead">{subhead}</div>
  <div class="dots">AI-ASSISTED EXPLAINER</div>
</body></html>"""


def _palette(name: str) -> dict[str, str]:
    return PALETTES.get(name, PALETTES["midnight"])


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fit(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_thumbnail(
    out_path: Path,
    *,
    headline: str,
    subhead: str = "",
    palette: str = "midnight",
    kicker: str = "",
    force_pillow: bool = False,
) -> Path:
    """Render the thumbnail; returns the PNG path (always 1280×720)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    headline = _fit(headline, 40)
    subhead = _fit(subhead, 48)
    kicker = _fit(kicker, 28)

    if not force_pillow:
        try:
            _render_playwright(out_path, headline, subhead, palette, kicker)
            if _valid(out_path):
                return out_path
        except Exception:  # noqa: BLE001 - Chromium absent/broken -> Pillow rung
            pass
    _render_pillow(out_path, headline, subhead, palette, kicker)
    return out_path


def _render_playwright(
    out_path: Path, headline: str, subhead: str, palette: str, kicker: str
) -> None:
    from playwright.sync_api import sync_playwright

    pal = _palette(palette)
    html = _HTML.format(
        w=WIDTH,
        h=HEIGHT,
        kicker=_escape(kicker) or "EXPLAINED",
        headline=_escape(headline),
        subhead=_escape(subhead),
        **pal,
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
            page.set_content(html, wait_until="load")
            page.screenshot(path=str(out_path), type="png")
        finally:
            browser.close()


# -- Pillow fallback --------------------------------------------------------
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",   # Segoe UI Bold
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\verdanab.ttf",
    r"C:\Windows\Fonts\consolab.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _wrap(draw: "ImageDraw.ImageDraw", text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_w:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _render_pillow(
    out_path: Path, headline: str, subhead: str, palette: str, kicker: str
) -> None:
    pal = _palette(palette)
    img = Image.new("RGB", (WIDTH, HEIGHT), pal["bg"])
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, 22, HEIGHT], fill=pal["accent"])

    kicker_font = _font(30)
    head_font = _font(86)
    sub_font = _font(36)
    foot_font = _font(22)

    y = 84
    draw.text((80, y), (kicker or "EXPLAINED").upper(), font=kicker_font, fill=pal["accent"])
    y += 58

    lines = _wrap(draw, headline.upper(), head_font, WIDTH - 160)
    for line in lines[:4]:
        draw.text((80, y), line, font=head_font, fill=pal["fg"])
        y += 100
    y += 16
    if subhead:
        draw.text((80, y), subhead, font=sub_font, fill=pal["sub"])
        y += 56
    draw.text((80, HEIGHT - 70), "AI-ASSISTED EXPLAINER", font=foot_font, fill=pal["sub"])
    img.save(out_path, format="PNG")


def _valid(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size > MAX_BYTES or path.stat().st_size == 0:
            return False
        with Image.open(path) as im:
            return im.size == (WIDTH, HEIGHT)
    except OSError:
        return False

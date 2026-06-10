"""Brand palette extraction + WCAG contrast guard. Pure color math is testable.

Palette source chain: <meta name="theme-color"> → og:image / largest favicon
dominant color (Pillow) → ACM default. Any failure falls through silently.

CONTRAST RULE (user catch, 2026-06-11): before a brand color is applied to any
text or background, check the WCAG relative-luminance contrast ratio against
what it sits on; failing elements use the ACM palette for that element only.
"""
from __future__ import annotations

import io
import logging
import re

import httpx
from bs4 import BeautifulSoup
from PIL import Image

logger = logging.getLogger(__name__)

# ACM fallback palette (matches the app theme).
DEFAULT_PRIMARY = "#6C5CE7"
DARK_TEXT = "#1A1A2E"
LIGHT_TEXT = "#FFFFFF"
MIN_CONTRAST = 4.5  # WCAG AA for normal text; we apply it to all deck text

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _to_rgb(hex_color: str) -> tuple[int, int, int]:
    match = _HEX_RE.match(hex_color.strip())
    if not match:
        raise ValueError(f"not a hex color: {hex_color!r}")
    value = match.group(1)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance in [0, 1]."""
    def linearize(channel: int) -> float:
        c = channel / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    red, green, blue = (linearize(c) for c in _to_rgb(hex_color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(color_a: str, color_b: str) -> float:
    """WCAG contrast ratio between two colors, in [1, 21]."""
    lum_a, lum_b = relative_luminance(color_a), relative_luminance(color_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def readable_text_color(background: str) -> str:
    """White or dark text — whichever reads better on `background`."""
    if contrast_ratio(LIGHT_TEXT, background) >= contrast_ratio(DARK_TEXT, background):
        return LIGHT_TEXT
    return DARK_TEXT


def ensure_contrast(candidate: str, background: str, fallback: str = DEFAULT_PRIMARY,
                    min_ratio: float = MIN_CONTRAST) -> str:
    """Return `candidate` if it reads on `background`, else the ACM fallback.

    The per-element guard: a near-white brand color never becomes a headline
    on a white slide.
    """
    try:
        if contrast_ratio(candidate, background) >= min_ratio:
            return candidate
    except ValueError:
        pass
    return fallback


def dominant_color(image_bytes: bytes) -> str | None:
    """Dominant non-extreme color of an image as hex, or None."""
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.thumbnail((64, 64))
        colors = image.getcolors(64 * 64) or []
    except Exception:  # noqa: BLE001 — any unreadable image => no color
        return None
    ranked = sorted(colors, key=lambda pair: pair[0], reverse=True)
    for _, (red, green, blue) in ranked:
        # Skip near-white / near-black fills — they're backgrounds, not brand.
        if 30 < (red + green + blue) / 3 < 225:
            return f"#{red:02X}{green:02X}{blue:02X}"
    return None


def extract_palette(website: str | None, timeout: float = 10.0) -> dict[str, str]:
    """Best-effort brand palette from a homepage. Never raises."""
    palette = {"primary": DEFAULT_PRIMARY, "source": "default"}
    if not website:
        return palette
    try:
        client = httpx.Client(timeout=timeout, follow_redirects=True,
                              headers={"User-Agent": "SponsorOS-Pitch/1.0"})
        soup = BeautifulSoup(client.get(website).text, "html.parser")

        meta = soup.find("meta", attrs={"name": "theme-color"})
        if meta and _HEX_RE.match(str(meta.get("content", "")).strip()):
            color = str(meta["content"]).strip()
            palette = {"primary": color if color.startswith("#") else f"#{color}",
                       "source": "brand"}
            return palette

        image_url = None
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            image_url = str(og_image["content"])
        else:
            icon = soup.find("link", rel=lambda r: bool(r) and "icon" in str(r).lower())
            if icon and icon.get("href"):
                image_url = httpx.URL(website).join(str(icon["href"])).human_repr()
        if image_url:
            color = dominant_color(client.get(image_url).content)
            if color:
                palette = {"primary": color, "source": "brand"}
    except Exception as exc:  # noqa: BLE001 — palette is cosmetic, never fatal
        logger.info("Palette extraction failed for %s: %s", website, exc)
    return palette

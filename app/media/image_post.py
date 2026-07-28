"""Deterministic image processing for the Instagram image-post MVP.

Builds a 4:5 carousel from a post's `media_urls` + the LLM-generated
`image_caption`. Only the FIRST image gets the caption overlay + watermark
(it's the one shown before the user taps into the carousel); the remaining
images are only cropped/resized to match.

The visual template (position, font, padding, opacity) is standardized
across all posts -- see LLM_PLAN.md section 3.4 for the design rationale.
All tunable values are named constants below so they can be adjusted later
without touching the drawing logic.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --- Canvas -----------------------------------------------------------
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350  # 4:5 aspect ratio, Instagram's tallest feed format

# --- Text overlay (image_caption, first image only) --------------------
FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "Montserrat-Variable.ttf"
FONT_VARIATION = "Bold"
FONT_SIZE = 58
TEXT_COLOR = (255, 255, 255, 255)
TEXT_LINE_SPACING = 12

# Montserrat has no emoji glyphs, so emoji runs are rendered separately with
# a color emoji font (Google's Noto Color Emoji, a fixed-strike bitmap font)
# and composited inline with the regular text. See LLM_PLAN.md section 4.1.
EMOJI_FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "NotoColorEmoji.ttf"
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # pictographs, emoticons, transport, supplemental symbols
    "\U00002600-\U000027BF"  # misc symbols & dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicator flags
    "\U00002B00-\U00002BFF"  # misc symbols/arrows (e.g. ⭐ ➡️)
    "\U0000FE0F"  # variation selector-16 (force emoji presentation)
    "\U0000200D"  # zero-width joiner (multi-part emoji sequences)
    "]+",
    flags=re.UNICODE,
)

# --- Bottom branding bar (dome-shaped radial gradient boilerplate) -----
# Standardized layout inspired by data/example/*.jpg -- see LLM_PLAN.md
# section 4.2. Instead of a hard-edged flat rectangle, the bar is a soft
# "dome" shaped black gradient (highest/most opaque at the horizontal
# center, curving down toward the sides) -- gives a more stylized look and
# lets a bit of the photo peek through near the corners, while staying
# fully opaque behind the badge/title/CTA content for legibility. Its
# height is computed per-image from the actual wrapped title (see
# apply_caption_text) so multi-line titles are never clipped.
BAR_COLOR = (0, 0, 0)
BAR_MAX_OPACITY = 235  # 0-255, opacity at the fully-covered core of the dome (not 255 -- slightly see-through)
BAR_DOME_HEIGHT_PX = 130  # how much lower (in px) the dome's edges sit vs. its center peak
BAR_DOME_FEATHER_PX = 90  # softness of the dome's edge (larger = softer/more gradual transition)
BAR_SIDE_MARGIN_PX = 40  # horizontal safe margin for badge/title/CTA content
BAR_TOP_MARGIN_PX = 40  # space between the dome's center peak and the badge
BAR_BOTTOM_MARGIN_PX = 40  # space between the CTA text and the bottom edge

CATEGORY_BADGE_COLOR = (14, 82, 148, 255)  # #0E5294
CATEGORY_BADGE_TEXT_COLOR = (255, 255, 255, 255)
CATEGORY_BADGE_FONT_SIZE = 28
CATEGORY_BADGE_PADDING_X = 24
CATEGORY_BADGE_PADDING_Y = 12
CATEGORY_BADGE_RADIUS = 8
CATEGORY_BADGE_MARGIN_BOTTOM_PX = 28  # gap between the badge and the title

CTA_TEXT = "Veja a legenda \u2193"  # plain unicode down-arrow, not a color emoji (see LLM_PLAN.md 4.3)
CTA_FONT_SIZE = 30
CTA_TEXT_COLOR = (190, 190, 190, 255)
CTA_MARGIN_TOP_PX = 28  # gap between the title and the CTA text

# --- Watermark (every image) -------------------------------------------
WATERMARK_PADDING_PX = 5
WATERMARK_WIDTH_RATIO = 0.14  # ~10-15% of canvas width
WATERMARK_OPACITY = 0.7

DEFAULT_WATERMARK_PATH = (
    Path(__file__).parent / "assets" / "watermark" / "placeholder_watermark.png"
)


def _load_image(source: str | Path) -> Image.Image:
    """Load an image from an http(s) URL or a local path."""
    if isinstance(source, str) and source.startswith(("http://", "https://")):
        response = requests.get(source, timeout=30)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGB")
    return Image.open(source).convert("RGB")


def _edge_energy_map(img: Image.Image) -> np.ndarray:
    """Grayscale edge-detection map used as a cheap "visual interest" proxy.

    No object/face detection dependency needed: regions with more edges/
    texture (a subject, text, product) score higher than flat background
    (walls, sky, floor), which is enough to avoid blindly cropping through
    the interesting part of the image.
    """
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    return np.asarray(edges, dtype=np.float32)


def _best_window_offset(weights: np.ndarray, window: int) -> int:
    """Offset (0..len(weights)-window) of the `window`-sized slice with the highest sum."""
    total = len(weights)
    if window >= total:
        return 0
    cumsum = np.cumsum(weights, dtype=np.float64)
    cumsum = np.insert(cumsum, 0, 0.0)
    window_sums = cumsum[window:] - cumsum[:-window]
    return int(np.argmax(window_sums))


def crop_to_canvas(img: Image.Image) -> Image.Image:
    """Crop + resize an image to the standard 4:5 canvas size.

    Instead of always cropping around the geometric center, this picks the
    crop offset (horizontal or vertical, whichever axis needs trimming) that
    keeps the most visually "busy" region of the image, based on an edge
    energy map -- see `_edge_energy_map`.
    """
    target_ratio = CANVAS_WIDTH / CANVAS_HEIGHT
    width, height = img.size
    current_ratio = width / height

    if current_ratio > target_ratio:
        # Image is wider than target: crop the sides.
        new_width = int(height * target_ratio)
        energy = _edge_energy_map(img)
        left = _best_window_offset(energy.sum(axis=0), new_width)
        img = img.crop((left, 0, left + new_width, height))
    elif current_ratio < target_ratio:
        # Image is taller than target: crop top/bottom.
        new_height = int(width / target_ratio)
        energy = _edge_energy_map(img)
        top = _best_window_offset(energy.sum(axis=1), new_height)
        img = img.crop((0, top, width, top + new_height))

    return img.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.LANCZOS)


# NotoColorEmoji ships a single fixed bitmap "strike" (Pillow raises
# "invalid pixel size" for any other size), so we always load it at its
# native size and downscale the rendered glyph bitmaps afterwards.
EMOJI_NATIVE_FONT_SIZE = 109


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_PATH), size)
    try:
        font.set_variation_by_name(FONT_VARIATION)
    except Exception:
        pass  # non-variable font fallback: use as-is
    return font


def _load_emoji_font() -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(EMOJI_FONT_PATH), EMOJI_NATIVE_FONT_SIZE)


def _contains_emoji(text: str) -> bool:
    return bool(_EMOJI_PATTERN.search(text))


def _split_runs(text: str) -> list[tuple[str, bool]]:
    """Split text into consecutive (segment, is_emoji) runs, in order."""
    runs: list[tuple[str, bool]] = []
    last_end = 0
    for match in _EMOJI_PATTERN.finditer(text):
        if match.start() > last_end:
            runs.append((text[last_end : match.start()], False))
        runs.append((text[match.start() : match.end()], True))
        last_end = match.end()
    if last_end < len(text):
        runs.append((text[last_end:], False))
    return runs


_emoji_glyph_cache: dict[tuple[str, int], Image.Image] = {}


def _render_emoji_glyphs(segment: str, emoji_font: ImageFont.FreeTypeFont, target_size: int) -> Image.Image:
    """Render an emoji run (one or more chars) to an RGBA bitmap `target_size` px tall.

    NotoColorEmoji only ships one bitmap strike (EMOJI_NATIVE_FONT_SIZE), so we
    always render at that native size, crop to the real ink, then downscale to
    match the caption font's visual size.
    """
    cache_key = (segment, target_size)
    cached = _emoji_glyph_cache.get(cache_key)
    if cached is not None:
        return cached

    pad = EMOJI_NATIVE_FONT_SIZE
    canvas_size = (EMOJI_NATIVE_FONT_SIZE * max(1, len(segment)) + pad, EMOJI_NATIVE_FONT_SIZE + pad)
    tmp = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((0, 0), segment, font=emoji_font, embedded_color=True)

    bbox = tmp.getbbox()
    if bbox is None:
        result = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    else:
        cropped = tmp.crop(bbox)
        scale = target_size / cropped.height
        new_size = (max(1, round(cropped.width * scale)), max(1, round(cropped.height * scale)))
        result = cropped.resize(new_size, Image.LANCZOS)

    _emoji_glyph_cache[cache_key] = result
    return result


def _measure_mixed_text(
    text: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    emoji_font: ImageFont.FreeTypeFont,
    emoji_target_size: int,
) -> float:
    """Width of `text`, rendering emoji runs as bitmaps and text runs with `font`."""
    total = 0.0
    for segment, is_emoji in _split_runs(text):
        if not segment:
            continue
        if is_emoji:
            total += _render_emoji_glyphs(segment, emoji_font, emoji_target_size).width
        else:
            total += draw.textlength(segment, font=font)
    return total


def _wrap_by_pixel_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    emoji_font: ImageFont.FreeTypeFont | None = None,
    emoji_target_size: int = 0,
) -> list[str]:
    """Word-wrap text so no line exceeds max_width pixels at the given font.

    More robust than a fixed character count: guarantees lines never bleed
    past the safe margin regardless of font/size changes. When `emoji_font`
    is given, emoji runs are measured via their rendered bitmap width instead
    of `font` (Montserrat has no emoji glyphs, so its width for them isn't
    meaningful).
    """
    words = text.split()
    if not words:
        return [text]

    def width(candidate: str) -> float:
        if emoji_font is not None:
            return _measure_mixed_text(candidate, draw, font, emoji_font, emoji_target_size)
        return draw.textlength(candidate, font=font)

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if width(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_mixed_line(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    line: str,
    font: ImageFont.FreeTypeFont,
    emoji_font: ImageFont.FreeTypeFont,
    emoji_target_size: int,
    emoji_baseline_offset: int,
) -> None:
    """Draw one line left-to-right from (x, y), switching fonts per run.

    `y` is the ascender-line position (anchor="la": left, ascender) for text
    runs; emoji bitmaps are pasted so their vertical center roughly lines up
    with the text's cap-height band (`emoji_baseline_offset` nudges this).
    """
    cursor_x = x
    for segment, is_emoji in _split_runs(line):
        if not segment:
            continue
        if is_emoji:
            glyph = _render_emoji_glyphs(segment, emoji_font, emoji_target_size)
            img.paste(glyph, (round(cursor_x), round(y + emoji_baseline_offset)), glyph)
            cursor_x += glyph.width
        else:
            draw.text((cursor_x, y), segment, font=font, fill=TEXT_COLOR, anchor="la")
            cursor_x += draw.textlength(segment, font=font)


def _dome_gradient_bar(peak_y: float) -> Image.Image:
    """Build a soft "dome" shaped black gradient layer for the branding bar.

    Fully opaque (BAR_MAX_OPACITY) at the horizontal center from `peak_y`
    down to the bottom of the canvas; the opaque region's top edge curves
    downward (further from the top) toward the left/right sides, tracing
    the upper-left quarter of an ellipse, with a soft feathered transition
    instead of a hard line. This lets a bit of the underlying photo peek
    through near the top corners, giving a more stylized look than a flat
    rectangle -- see LLM_PLAN.md section 4.3.
    """
    cx = CANVAS_WIDTH / 2
    rx = CANVAS_WIDTH / 2
    ry = BAR_DOME_HEIGHT_PX

    xs = np.arange(CANVAS_WIDTH, dtype=np.float64)
    normalized_dx = np.clip((xs - cx) / rx, -1.0, 1.0)
    # Ellipse centered below `peak_y`; its upper boundary is peak_y at the
    # center (dx=0) and peak_y + ry at the edges (dx=+-rx).
    transition_y = peak_y + ry - ry * np.sqrt(1.0 - normalized_dx**2)

    ys = np.arange(CANVAS_HEIGHT, dtype=np.float64).reshape(-1, 1)
    diff = ys - transition_y.reshape(1, -1)

    # Smoothstep feather across BAR_DOME_FEATHER_PX centered on the transition.
    t = np.clip(diff / BAR_DOME_FEATHER_PX + 0.5, 0.0, 1.0)
    smooth = t * t * (3.0 - 2.0 * t)
    alpha = (smooth * BAR_MAX_OPACITY).astype(np.uint8)

    layer = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), BAR_COLOR + (0,))
    layer.putalpha(Image.fromarray(alpha, mode="L"))
    return layer


def _draw_category_badge(
    draw: ImageDraw.ImageDraw, category: str, top_y: float, badge_font: ImageFont.FreeTypeFont
) -> float:
    """Draw the centered category badge (rounded rect + uppercase label) at `top_y`.

    Returns the badge's height so the caller can position the title below it.
    """
    text = category.strip().upper()
    text_width = draw.textlength(text, font=badge_font)
    ascent, descent = badge_font.getmetrics()

    badge_width = text_width + 2 * CATEGORY_BADGE_PADDING_X
    badge_height = ascent + descent + 2 * CATEGORY_BADGE_PADDING_Y

    x0 = (CANVAS_WIDTH - badge_width) / 2
    y0 = top_y
    x1 = x0 + badge_width
    y1 = y0 + badge_height

    draw.rounded_rectangle((x0, y0, x1, y1), radius=CATEGORY_BADGE_RADIUS, fill=CATEGORY_BADGE_COLOR)
    draw.text(
        (x0 + CATEGORY_BADGE_PADDING_X, y0 + CATEGORY_BADGE_PADDING_Y),
        text,
        font=badge_font,
        fill=CATEGORY_BADGE_TEXT_COLOR,
        anchor="la",
    )
    return badge_height


def apply_caption_text(img: Image.Image, text: str, category: str) -> Image.Image:
    """Draw the standardized bottom branding block: dome-gradient bar + category
    badge + uppercase title + "Veja a legenda ↓" CTA.

    Layout mirrors the reference boilerplate in data/example/*.jpg (see
    LLM_PLAN.md section 4.2/4.3): a soft dome-shaped black gradient (not a
    flat rectangle) so the badge/title/CTA have strong contrast while still
    letting a bit of the photo peek through near the top corners. The bar's
    height is computed bottom-up from the actual wrapped title, so
    multi-line titles are never clipped. Emoji (Montserrat has no emoji
    glyphs) are rendered with a separate color emoji font and composited
    inline with the regular text.
    """
    img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)

    title_upper = text.upper()
    title_font = _load_font(FONT_SIZE)
    badge_font = _load_font(CATEGORY_BADGE_FONT_SIZE)
    cta_font = _load_font(CTA_FONT_SIZE)

    has_emoji = _contains_emoji(title_upper)
    emoji_font = _load_emoji_font() if has_emoji else None

    max_line_width = CANVAS_WIDTH - 2 * BAR_SIDE_MARGIN_PX
    ascent, descent = title_font.getmetrics()
    # Emoji visually read as roughly cap-height sized; sizing them to the
    # font's ascent (a bit larger than cap-height) reads well next to bold text.
    emoji_target_size = ascent
    # Nudge emoji bitmaps down slightly so their visual center aligns with
    # the text's cap-height band instead of sitting flush with the ascent line.
    emoji_baseline_offset = round(ascent * 0.12)

    lines = _wrap_by_pixel_width(
        draw, title_upper, title_font, max_line_width, emoji_font=emoji_font, emoji_target_size=emoji_target_size
    )
    pitch = ascent + descent + TEXT_LINE_SPACING
    title_block_height = pitch * (len(lines) - 1) + (ascent + descent)

    badge_ascent, badge_descent = badge_font.getmetrics()
    badge_height = badge_ascent + badge_descent + 2 * CATEGORY_BADGE_PADDING_Y

    cta_ascent, cta_descent = cta_font.getmetrics()
    cta_height = cta_ascent + cta_descent

    bar_height = round(
        BAR_TOP_MARGIN_PX
        + badge_height
        + CATEGORY_BADGE_MARGIN_BOTTOM_PX
        + title_block_height
        + CTA_MARGIN_TOP_PX
        + cta_height
        + BAR_BOTTOM_MARGIN_PX
    )
    bar_height = min(bar_height, CANVAS_HEIGHT)  # safety clamp; not expected in practice

    bar_top = CANVAS_HEIGHT - bar_height
    dome_layer = _dome_gradient_bar(bar_top)
    img.alpha_composite(dome_layer)
    draw = ImageDraw.Draw(img)  # re-bind: alpha_composite may invalidate the previous draw context

    badge_top = bar_top + BAR_TOP_MARGIN_PX
    _draw_category_badge(draw, category, badge_top, badge_font)

    title_top = badge_top + badge_height + CATEGORY_BADGE_MARGIN_BOTTOM_PX
    for i, line in enumerate(lines):
        if emoji_font is not None:
            line_width = _measure_mixed_text(line, draw, title_font, emoji_font, emoji_target_size)
        else:
            line_width = draw.textlength(line, font=title_font)
        line_x = (CANVAS_WIDTH - line_width) / 2
        line_y = title_top + i * pitch
        if emoji_font is not None:
            _draw_mixed_line(
                img, draw, line_x, line_y, line, title_font, emoji_font, emoji_target_size, emoji_baseline_offset
            )
        else:
            draw.text((line_x, line_y), line, font=title_font, fill=TEXT_COLOR, anchor="la")

    cta_top = title_top + title_block_height + CTA_MARGIN_TOP_PX
    cta_width = draw.textlength(CTA_TEXT, font=cta_font)
    cta_x = (CANVAS_WIDTH - cta_width) / 2
    draw.text((cta_x, cta_top), CTA_TEXT, font=cta_font, fill=CTA_TEXT_COLOR, anchor="la")

    return img


def apply_watermark(img: Image.Image, watermark_path: Path | str = DEFAULT_WATERMARK_PATH) -> Image.Image:
    """Paste the watermark in the top-right corner at the standard opacity/padding."""
    img = img.convert("RGBA")
    watermark = Image.open(watermark_path).convert("RGBA")

    target_width = int(CANVAS_WIDTH * WATERMARK_WIDTH_RATIO)
    scale = target_width / watermark.width
    target_height = int(watermark.height * scale)
    watermark = watermark.resize((target_width, target_height), Image.LANCZOS)

    # Scale down the alpha channel to apply WATERMARK_OPACITY without touching RGB.
    alpha = watermark.getchannel("A").point(lambda a: int(a * WATERMARK_OPACITY))
    watermark.putalpha(alpha)

    x = CANVAS_WIDTH - target_width - WATERMARK_PADDING_PX
    y = WATERMARK_PADDING_PX
    img.paste(watermark, (x, y), watermark)
    return img

def build_carousel(
    media_urls: list[str],
    image_caption: str,
    category: str,
    output_dir: Path | str,
    watermark_path: Path | str = DEFAULT_WATERMARK_PATH,
    visual_prompt: str | None = None,
) -> list[Path]:
    """Process every image in media_urls into the standard 4:5 carousel format.

    Only the first image gets the boilerplate branding block (solid bottom
    bar + category badge + image_caption title + CTA) + watermark; the rest
    are cropped/resized only (per LLM_PLAN.md section 3.3: image selection/
    ordering is deterministic, not LLM-driven).

    If `media_urls` is empty, `visual_prompt` (the LLM-generated scene
    description, see `ImagePost.visual_prompt`) is used to generate a single
    fallback image via Pollinations.ai -- see app/media/ai_image.py and
    LLM_PLAN.md section 4.1. Raises ValueError if both are missing/empty.

    Returns the list of saved file paths, in carousel order.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not media_urls:
        if not visual_prompt or not visual_prompt.strip():
            raise ValueError(
                "media_urls is empty and no visual_prompt was given to generate a fallback image"
            )
        from app.media.ai_image import AIImageError, generate_ai_image

        generated_path = output_dir / "_ai_generated_source.jpg"
        try:
            generate_ai_image(visual_prompt, generated_path, CANVAS_WIDTH, CANVAS_HEIGHT)
        except AIImageError as exc:
            raise ValueError(f"failed to generate fallback AI image: {exc}") from exc
        media_urls = [str(generated_path)]
    else:
        generated_path = None

    saved_paths: list[Path] = []
    for index, url in enumerate(media_urls):
        img = _load_image(url)
        img = crop_to_canvas(img)

        if index == 0:
            img = apply_caption_text(img, image_caption, category)

        img = apply_watermark(img, watermark_path)

        out_path = output_dir / f"slide_{index + 1}.jpg"
        img.convert("RGB").save(out_path, "JPEG", quality=90)
        saved_paths.append(out_path)

    if generated_path is not None:
        generated_path.unlink(missing_ok=True)  # temp source, not part of the final carousel

    return saved_paths


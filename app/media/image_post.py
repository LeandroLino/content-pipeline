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
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# --- Canvas -----------------------------------------------------------
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350  # 4:5 aspect ratio, Instagram's tallest feed format

# --- Text overlay (image_caption, first image only) --------------------
FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "Montserrat-Variable.ttf"
FONT_VARIATION = "Bold"
FONT_SIZE = 58
TEXT_PADDING_PX = 5
TEXT_COLOR = (255, 255, 255, 255)
TEXT_LINE_SPACING = 12
TEXT_MAX_CHARS_PER_LINE = 28  # wrap width; the LLM already keeps captions short

# Dark gradient behind the text, bottom third of the image, for legibility.
GRADIENT_HEIGHT_RATIO = 1 / 3
GRADIENT_MAX_OPACITY = 190  # 0-255, at the very bottom of the gradient

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

## Ajustar funçao futuramente para se adaptar a diferentes tamanhos de imagens, mantendo a proporção e evitando distorções.
def crop_to_canvas(img: Image.Image) -> Image.Image:
    """Center-crop + resize an image to the standard 4:5 canvas size."""
    target_ratio = CANVAS_WIDTH / CANVAS_HEIGHT
    width, height = img.size
    current_ratio = width / height

    if current_ratio > target_ratio:
        # Image is wider than target: crop the sides.
        new_width = int(height * target_ratio)
        left = (width - new_width) // 2
        img = img.crop((left, 0, left + new_width, height))
    elif current_ratio < target_ratio:
        # Image is taller than target: crop top/bottom.
        new_height = int(width / target_ratio)
        top = (height - new_height) // 2
        img = img.crop((0, top, width, top + new_height))

    return img.resize((CANVAS_WIDTH, CANVAS_HEIGHT), Image.LANCZOS)

## Aprimorar gradiente na imagem para se destacar melhor, está muito sutil, talvez aumentar a opacidade.
def apply_gradient_overlay(img: Image.Image) -> Image.Image:
    """Draw a dark gradient over the bottom third of the image for text legibility."""
    img = img.convert("RGBA")
    gradient_height = int(CANVAS_HEIGHT * GRADIENT_HEIGHT_RATIO)
    gradient = Image.new("RGBA", (CANVAS_WIDTH, gradient_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(gradient)

    for y in range(gradient_height):
        alpha = int(GRADIENT_MAX_OPACITY * (y / gradient_height))
        draw.line([(0, y), (CANVAS_WIDTH, y)], fill=(0, 0, 0, alpha))

    img.paste(gradient, (0, CANVAS_HEIGHT - gradient_height), gradient)
    return img


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONT_PATH), size)
    try:
        font.set_variation_by_name(FONT_VARIATION)
    except Exception:
        pass  # non-variable font fallback: use as-is
    return font

## Ajustar para que o texto possa ter emojis, atualmente não está renderizando corretamente, talvez seja necessário usar uma fonte que suporte emojis.
## Fazer com que o texto fique alinhado apartir do centro da imagem, atualmente está alinhado a esquerda, talvez seja interessante centralizar para melhor estética.
## Adicionar uma margem de segurança para o texto não ultrapassar a borda da imagem, atualmente o texto está muito próximo da borda inferior, talvez seja interessante aumentar a margem inferior.
def apply_caption_text(img: Image.Image, text: str) -> Image.Image:
    """Overlay the short image_caption, bottom-left, over the gradient."""
    img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)
    font = _load_font(FONT_SIZE)

    lines = textwrap.wrap(text, width=TEXT_MAX_CHARS_PER_LINE) or [text]

    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])
    total_text_height = sum(line_heights) + TEXT_LINE_SPACING * (len(lines) - 1)

    y = CANVAS_HEIGHT - TEXT_PADDING_PX - total_text_height
    for line, line_height in zip(lines, line_heights):
        draw.text((TEXT_PADDING_PX, y), line, font=font, fill=TEXT_COLOR)
        y += line_height + TEXT_LINE_SPACING

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

## Futuramente ajustar funcao para caso nao exista imagens, criar uma imagem com IA.
def build_carousel(
    media_urls: list[str],
    image_caption: str,
    output_dir: Path | str,
    watermark_path: Path | str = DEFAULT_WATERMARK_PATH,
) -> list[Path]:
    """Process every image in media_urls into the standard 4:5 carousel format.

    Only the first image gets the image_caption overlay + watermark; the
    rest are cropped/resized only (per LLM_PLAN.md section 3.3: image
    selection/ordering is deterministic, not LLM-driven).

    Returns the list of saved file paths, in carousel order.
    """
    if not media_urls:
        raise ValueError("media_urls must contain at least one image")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[Path] = []
    for index, url in enumerate(media_urls):
        img = _load_image(url)
        img = crop_to_canvas(img)

        if index == 0:
            img = apply_gradient_overlay(img)
            img = apply_caption_text(img, image_caption)

        img = apply_watermark(img, watermark_path)

        out_path = output_dir / f"slide_{index + 1}.jpg"
        img.convert("RGB").save(out_path, "JPEG", quality=90)
        saved_paths.append(out_path)

    return saved_paths

from pathlib import Path

import pytest
from PIL import Image

from app.media.image_post import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    TEXT_PADDING_PX,
    WATERMARK_PADDING_PX,
    apply_caption_text,
    apply_gradient_overlay,
    apply_watermark,
    build_carousel,
    crop_to_canvas,
)


def _make_test_image(tmp_path: Path, name: str, size: tuple[int, int], color=(120, 160, 200)) -> Path:
    path = tmp_path / name
    Image.new("RGB", size, color).save(path)
    return path


def test_crop_to_canvas_produces_standard_size_for_wide_image(tmp_path):
    src = Image.new("RGB", (2000, 1000), (10, 20, 30))
    result = crop_to_canvas(src)
    assert result.size == (CANVAS_WIDTH, CANVAS_HEIGHT)


def test_crop_to_canvas_produces_standard_size_for_tall_image(tmp_path):
    src = Image.new("RGB", (1000, 2000), (10, 20, 30))
    result = crop_to_canvas(src)
    assert result.size == (CANVAS_WIDTH, CANVAS_HEIGHT)


def test_crop_to_canvas_produces_standard_size_for_square_image(tmp_path):
    src = Image.new("RGB", (1500, 1500), (10, 20, 30))
    result = crop_to_canvas(src)
    assert result.size == (CANVAS_WIDTH, CANVAS_HEIGHT)


def test_apply_gradient_overlay_darkens_bottom_and_keeps_top_untouched():
    base = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (200, 200, 200))
    result = apply_gradient_overlay(base)

    top_pixel = result.convert("RGB").getpixel((CANVAS_WIDTH // 2, 5))
    bottom_pixel = result.convert("RGB").getpixel((CANVAS_WIDTH // 2, CANVAS_HEIGHT - 5))

    assert top_pixel == (200, 200, 200)  # untouched, above the gradient band
    assert bottom_pixel != (200, 200, 200)  # darkened by the gradient
    assert sum(bottom_pixel) < sum(top_pixel)


def test_apply_caption_text_draws_white_pixels_near_bottom_left():
    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    result = apply_caption_text(base, "Texto de teste curto")

    region = result.crop((0, CANVAS_HEIGHT - 100, 400, CANVAS_HEIGHT)).convert("RGB")
    has_white_pixel = any(
        region.getpixel((x, y)) == (255, 255, 255)
        for x in range(0, region.width, 4)
        for y in range(0, region.height, 4)
    )
    assert has_white_pixel


def test_apply_watermark_respects_padding_and_opacity(tmp_path):
    watermark_path = tmp_path / "watermark.png"
    Image.new("RGBA", (200, 60), (255, 255, 255, 255)).save(watermark_path)

    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    result = apply_watermark(base, watermark_path)

    # Pixel just inside the padding, near the top-right corner, should be affected.
    probe_x = CANVAS_WIDTH - WATERMARK_PADDING_PX - 5
    probe_y = WATERMARK_PADDING_PX + 5
    r, g, b, a = result.getpixel((probe_x, probe_y))
    assert (r, g, b) != (0, 0, 0)  # watermark pixel blended in, not the black base


def test_build_carousel_applies_overlay_only_to_first_image(tmp_path):
    img1 = _make_test_image(tmp_path, "one.jpg", (2000, 2000), (250, 250, 250))
    img2 = _make_test_image(tmp_path, "two.jpg", (2000, 2000), (250, 250, 250))
    output_dir = tmp_path / "out"

    paths = build_carousel([str(img1), str(img2)], "Legenda de teste", output_dir)

    assert len(paths) == 2
    assert all(p.exists() for p in paths)

    slide1 = Image.open(paths[0]).convert("RGB")
    slide2 = Image.open(paths[1]).convert("RGB")

    # Bottom-right corner (gradient present, but past the short left-aligned
    # caption text) should be darker on slide 1 than on slide 2, which has no
    # overlay at all.
    probe = (CANVAS_WIDTH - 10, CANVAS_HEIGHT - 10)
    bottom_right_1 = slide1.getpixel(probe)
    bottom_right_2 = slide2.getpixel(probe)
    assert sum(bottom_right_1) < sum(bottom_right_2)


def test_build_carousel_requires_at_least_one_image(tmp_path):
    with pytest.raises(ValueError):
        build_carousel([], "Legenda", tmp_path / "out")

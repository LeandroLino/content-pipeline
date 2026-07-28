from pathlib import Path

import pytest
from PIL import Image

from app.media.image_post import (
    BAR_SIDE_MARGIN_PX,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    CATEGORY_BADGE_COLOR,
    WATERMARK_PADDING_PX,
    apply_caption_text,
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


def test_crop_to_canvas_shifts_toward_visual_detail_horizontally():
    # Flat background everywhere, except a high-contrast "subject" block near
    # the right edge -- a naive center-crop on this wide image would cut it
    # off entirely, since the target 4:5 ratio only keeps a narrow vertical
    # slice of this canvas.
    width, height = 2000, 1000
    src = Image.new("RGB", (width, height), (30, 30, 30))
    subject = Image.new("RGB", (120, height), (250, 10, 10))
    src.paste(subject, (width - 150, 0))

    result = crop_to_canvas(src)

    assert result.size == (CANVAS_WIDTH, CANVAS_HEIGHT)
    # The subject's red hue should still be detectable in the cropped output
    # (sampled along the right portion, where the smart crop should have
    # shifted toward it) instead of being cropped away like a blind center-crop would.
    right_strip = result.crop((CANVAS_WIDTH - 150, 0, CANVAS_WIDTH, CANVAS_HEIGHT)).convert("RGB")
    has_reddish_pixel = any(
        right_strip.getpixel((x, y))[0] > 150 and right_strip.getpixel((x, y))[1] < 80
        for x in range(0, right_strip.width, 5)
        for y in range(0, right_strip.height, 20)
    )
    assert has_reddish_pixel


def test_crop_to_canvas_stays_centered_for_uniform_image():
    # No edges anywhere -> smart crop should fall back to the same offset a
    # blind center-crop would use (no regression for flat/simple images).
    src = Image.new("RGB", (2000, 1000), (100, 100, 100))
    result = crop_to_canvas(src)
    assert result.size == (CANVAS_WIDTH, CANVAS_HEIGHT)
    # Uniform color throughout -- crop position doesn't matter, but this
    # guards against crashes/exceptions on a zero-energy image.
    assert result.convert("RGB").getpixel((CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2)) == (100, 100, 100)


def test_apply_caption_text_draws_dome_gradient_bar_and_keeps_top_untouched():
    base = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), (200, 200, 200))
    result = apply_caption_text(base, "Texto de teste curto", "NOTÍCIA")

    top_pixel = result.convert("RGB").getpixel((CANVAS_WIDTH // 2, 5))
    # Sample near the very bottom-center: at the dome's horizontal center the
    # gradient reaches full core opacity almost immediately below its peak.
    bottom_pixel = result.convert("RGB").getpixel((CANVAS_WIDTH // 2, CANVAS_HEIGHT - 5))
    # Sample near the top corner of the bar area: the dome shape lets some of
    # the underlying photo peek through there, so it should be lighter than
    # the fully-covered bottom-center pixel.
    corner_pixel = result.convert("RGB").getpixel((5, CANVAS_HEIGHT - 220))

    assert top_pixel == (200, 200, 200)  # untouched, above the bar
    assert sum(bottom_pixel) < 60  # near-black core of the dome (not full 255 opacity anymore)
    assert sum(corner_pixel) > sum(bottom_pixel)  # dome lets the photo show through near the corners


def test_apply_caption_text_draws_category_badge():
    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    result = apply_caption_text(base, "Texto de teste curto", "NOTÍCIA").convert("RGB")

    # Badge sits near the top of the bar, horizontally centered -- scan a
    # generous band for the brand badge color.
    region = result.crop((0, CANVAS_HEIGHT - 300, CANVAS_WIDTH, CANVAS_HEIGHT - 100))
    has_badge_pixel = any(
        region.getpixel((x, y)) == CATEGORY_BADGE_COLOR[:3]
        for x in range(0, region.width, 3)
        for y in range(0, region.height, 3)
    )
    assert has_badge_pixel


def test_apply_caption_text_draws_white_pixels_near_bottom_center():
    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    result = apply_caption_text(base, "Texto de teste curto", "NOTÍCIA")

    region = result.crop(
        (CANVAS_WIDTH // 4, CANVAS_HEIGHT - 150, 3 * CANVAS_WIDTH // 4, CANVAS_HEIGHT)
    ).convert("RGB")
    has_white_pixel = any(
        region.getpixel((x, y)) == (255, 255, 255)
        for x in range(0, region.width, 4)
        for y in range(0, region.height, 4)
    )
    assert has_white_pixel


def test_apply_caption_text_never_touches_side_edges():
    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    result = apply_caption_text(
        base, "Texto suficientemente longo para quebrar em mais de uma linha", "NOTÍCIA"
    ).convert("RGB")

    def is_dark(pixel, threshold=40):
        # Allow minor anti-aliasing bleed from font rendering; only fail if a
        # pixel is clearly part of the white text/badge itself.
        return sum(pixel) < threshold

    # Left/right margins: no bright (text/badge) pixel should appear inside the safe zone columns.
    for x in list(range(0, BAR_SIDE_MARGIN_PX)) + list(range(CANVAS_WIDTH - BAR_SIDE_MARGIN_PX, CANVAS_WIDTH)):
        for y in range(CANVAS_HEIGHT - 250, CANVAS_HEIGHT, 5):
            assert is_dark(result.getpixel((x, y)))


def test_apply_caption_text_draws_cta_text():
    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    result = apply_caption_text(base, "Texto de teste curto", "NOTÍCIA").convert("RGB")

    # CTA text sits near the bottom of the bar -- just confirm non-black
    # (lighter gray) pixels are present in that band.
    region = result.crop((0, CANVAS_HEIGHT - 100, CANVAS_WIDTH, CANVAS_HEIGHT))
    has_cta_pixel = any(
        sum(region.getpixel((x, y))) > 200
        for x in range(0, region.width, 3)
        for y in range(0, region.height, 3)
    )
    assert has_cta_pixel


def test_apply_caption_text_renders_colored_emoji():
    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    result = apply_caption_text(base, "Isso foi incrível 🔥🎉", "NOTÍCIA").convert("RGB")

    region = result.crop((0, CANVAS_HEIGHT - 250, CANVAS_WIDTH, CANVAS_HEIGHT))
    # A colored (non-grayscale) pixel proves an emoji bitmap was actually
    # composited -- plain white/black text pixels always have r == g == b
    # (the badge is a solid known color, filtered out here).
    badge_rgb = CATEGORY_BADGE_COLOR[:3]
    has_colored_pixel = any(
        len({r, g, b}) > 1 and (r, g, b) != badge_rgb
        for x in range(0, region.width, 3)
        for y in range(0, region.height, 3)
        for r, g, b in [region.getpixel((x, y))]
    )
    assert has_colored_pixel


def test_apply_caption_text_with_emoji_never_touches_side_edges():
    base = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 255))
    result = apply_caption_text(
        base, "Texto com emoji suficientemente longo pra quebrar 🔥🎉😀 em mais de uma linha", "NOTÍCIA"
    ).convert("RGB")

    def is_dark(pixel, threshold=40):
        return sum(pixel) < threshold

    for x in list(range(0, BAR_SIDE_MARGIN_PX)) + list(range(CANVAS_WIDTH - BAR_SIDE_MARGIN_PX, CANVAS_WIDTH)):
        for y in range(CANVAS_HEIGHT - 250, CANVAS_HEIGHT, 5):
            assert is_dark(result.getpixel((x, y)))



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

    paths = build_carousel([str(img1), str(img2)], "Legenda de teste", "NOTÍCIA", output_dir)

    assert len(paths) == 2
    assert all(p.exists() for p in paths)

    slide1 = Image.open(paths[0]).convert("RGB")
    slide2 = Image.open(paths[1]).convert("RGB")

    # Bottom-left corner (inside the solid black bar) should be darker on
    # slide 1 than on slide 2, which has no overlay at all.
    probe = (10, CANVAS_HEIGHT - 10)
    bottom_left_1 = slide1.getpixel(probe)
    bottom_left_2 = slide2.getpixel(probe)
    assert sum(bottom_left_1) < sum(bottom_left_2)


def test_build_carousel_requires_at_least_one_image(tmp_path):
    with pytest.raises(ValueError):
        build_carousel([], "Legenda", "NOTÍCIA", tmp_path / "out")


def test_build_carousel_generates_ai_fallback_when_no_media_urls(tmp_path, monkeypatch):
    def _fake_generate_ai_image(prompt, output_path, width, height):
        Image.new("RGB", (width, height), (80, 120, 160)).save(output_path)
        return Path(output_path)

    monkeypatch.setattr("app.media.ai_image.generate_ai_image", _fake_generate_ai_image)

    output_dir = tmp_path / "out"
    paths = build_carousel(
        [], "Legenda de teste", "NOTÍCIA", output_dir, visual_prompt="a cozy home server rack, photorealistic"
    )

    assert len(paths) == 1
    assert paths[0].exists()
    assert paths[0].name == "slide_1.jpg"
    # The temp AI-generated source shouldn't leak into the final output dir.
    assert not (output_dir / "_ai_generated_source.jpg").exists()


def test_build_carousel_raises_when_no_media_urls_and_no_visual_prompt(tmp_path):
    with pytest.raises(ValueError, match="visual_prompt"):
        build_carousel([], "Legenda", "NOTÍCIA", tmp_path / "out", visual_prompt=None)


def test_build_carousel_wraps_ai_image_failure_as_value_error(tmp_path, monkeypatch):
    from app.media.ai_image import AIImageError

    def _fake_generate_ai_image(prompt, output_path, width, height):
        raise AIImageError("network boom")

    monkeypatch.setattr("app.media.ai_image.generate_ai_image", _fake_generate_ai_image)

    with pytest.raises(ValueError, match="failed to generate fallback AI image"):
        build_carousel([], "Legenda", "NOTÍCIA", tmp_path / "out", visual_prompt="qualquer coisa")

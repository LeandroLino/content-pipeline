"""Deterministic stub provider -- no network call, no API key required.

Used as the default (`LLM_PROVIDER=stub`) so the pipeline stays runnable
end-to-end in dev/CI without any LLM credentials. Output is intentionally
plain (no attempt to mimic the hook/CTA/hashtags style real providers aim
for) -- it only needs to be a valid, deterministic `ImagePost`.
"""

from app.schemas import ImagePost, IngestPayload

IMAGE_CAPTION_MAX_CHARS = 80


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def generate_image_post(payload: IngestPayload) -> ImagePost:
    title = payload.raw_title.strip() or "Untitled post"
    body = payload.raw_content.strip()
    excerpt = body[:280].strip() if body else ""

    post_caption_parts = [title]
    if excerpt:
        post_caption_parts.append(excerpt)
    post_caption_parts.append(f"Fonte: {payload.original_url}")
    post_caption = "\n\n".join(post_caption_parts)

    image_caption = _truncate(title, IMAGE_CAPTION_MAX_CHARS)
    visual_prompt = (
        "A blurred, moody background photo with soft bokeh, dark ambient "
        "lighting, out of focus, cinematic, shallow depth of field."
    )

    return ImagePost(
        post_caption=post_caption,
        image_caption=image_caption,
        visual_prompt=visual_prompt,
        category="CURIOSIDADE",
    )

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

Source = Literal["reddit", "twitter", "medium", "youtube", "instagram"]

# Fixed category label set for the boilerplate badge overlay (see
# LLM_PLAN.md section 4.2) -- kept as a Literal so both Gemini's
# response_schema and OpenAI's json_schema structured output enforce the
# LLM to pick exactly one of these, instead of freeform text that could
# break the badge's short/uppercase design.
ImageCategory = Literal["CURIOSIDADE", "TECNOLOGIA", "TUTORIAL", "HISTÓRIA", "DESABAFO", "NOTÍCIA"]

# NOTE: `target_platforms` (tiktok/youtube_shorts/instagram_reels/youtube) was
# part of the original video-pipeline design (see PLAN.md section 4) but is
# unused by the current image-post MVP. Removed for now (2026-07-24) to keep
# the schema honest about what's actually implemented; reintroduce when
# Fase 2 (video) picks the field back up.


class IngestPayload(BaseModel):
    source: Source
    original_url: HttpUrl
    raw_title: str
    raw_content: str
    media_urls: list[HttpUrl] = Field(default_factory=list)
    top_comments: list[str] = Field(
        default_factory=list,
        description="Top-voted comments on the original post (plain text), "
        "given to the LLM as extra context for caption generation.",
    )
    metadata: dict = Field(default_factory=dict)


class ImagePost(BaseModel):
    """LLM output for the image-post MVP (Instagram carousel).

    See LLM_PLAN.md section 3 for the design decisions behind this shape:
    the LLM only produces text (two distinct captions); image selection and
    the visual overlay template are deterministic code, not LLM output.
    """

    post_caption: str = Field(
        description="Long-form caption published with the post (hook, context, CTA, hashtags)."
    )
    image_caption: str = Field(
        description="Short hook text overlaid on the first carousel image (~60-80 chars)."
    )
    visual_prompt: str = Field(
        description="Vivid, detailed English scene description for AI image generation -- "
        "used ONLY as a fallback when the original post has no media_urls."
    )
    category: ImageCategory = Field(
        description="Content category label shown in the small badge overlaid on the first "
        "carousel image (see LLM_PLAN.md section 4.2)."
    )

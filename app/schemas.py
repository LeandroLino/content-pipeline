from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

Source = Literal["reddit", "twitter", "medium", "youtube", "instagram"]

# NOTE: `target_platforms` (tiktok/youtube_shorts/instagram_reels/youtube) was
# part of the original video-pipeline design (see PLAN.md section 4) but is
# unused by the current image-post MVP. Removed for now (2026-07-24) to keep
# the schema honest about what's actually implemented; reintroduce when
# Fase 2 (video) picks the field back up.


## Adicionar seçao com alguns comentários da postagem para auxiliar a IA.
class IngestPayload(BaseModel):
    source: Source
    original_url: HttpUrl
    raw_title: str
    raw_content: str
    media_urls: list[HttpUrl] = Field(default_factory=list)
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

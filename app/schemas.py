from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

Source = Literal["reddit", "twitter", "medium", "youtube", "instagram"]
TargetPlatform = Literal["tiktok", "youtube_shorts", "instagram_reels", "youtube"]


class IngestPayload(BaseModel):
    source: Source
    original_url: HttpUrl
    raw_title: str
    raw_content: str
    media_urls: list[HttpUrl] = Field(default_factory=list)
    target_platforms: list[TargetPlatform] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

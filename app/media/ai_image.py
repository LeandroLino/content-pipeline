"""AI image generation fallback -- used only when a post has no media_urls.

Uses Pollinations.ai (https://pollinations.ai): a free, no-API-key image
generation service, simple enough to call with a plain HTTP GET. See
LLM_PLAN.md section 4.1 for why this was chosen over Leonardo.ai/Stability
(both require a paid API plan; Pollinations doesn't).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import requests

POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"
REQUEST_TIMEOUT_S = 60


class AIImageError(Exception):
    pass


def generate_ai_image(
    prompt: str,
    output_path: Path | str,
    width: int,
    height: int,
) -> Path:
    """Generate an image from `prompt` and save it to `output_path`.

    Raises AIImageError on any network/HTTP failure so callers (build_carousel)
    can decide how to handle it, consistent with the rest of the codebase's
    error-handling style (LLMError, RedditIngestError, etc.).
    """
    if not prompt.strip():
        raise AIImageError("prompt must not be empty")

    encoded_prompt = quote(prompt.strip())
    url = f"{POLLINATIONS_BASE_URL}/{encoded_prompt}"
    params = {
        "width": width,
        "height": height,
        "nologo": "true",
        # Fixed model so the default can't silently change under us; "flux"
        # gives noticeably better photorealism than the faster "turbo".
        "model": "flux",
    }

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AIImageError(f"Pollinations.ai request failed: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/"):
        raise AIImageError(f"Pollinations.ai did not return an image (content-type: {content_type!r})")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return output_path

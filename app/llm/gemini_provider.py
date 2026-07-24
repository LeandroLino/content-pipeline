"""Gemini provider -- generates ImagePost via Google's google-genai SDK.

Uses structured output (`response_schema`) so Gemini returns JSON that
already validates against the `ImagePost` Pydantic model -- no fragile
string parsing.
"""

from app.llm.base import LLMError
from app.llm.prompts import build_prompt
from app.schemas import ImagePost, IngestPayload

DEFAULT_MODEL = "gemini-2.5-flash"


def generate_image_post(payload: IngestPayload, api_key: str, model: str = DEFAULT_MODEL) -> ImagePost:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise LLMError(f"google-genai not installed: {exc}") from exc

    client = genai.Client(api_key=api_key)
    prompt = build_prompt(payload)

    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ImagePost,
            ),
        )
    except Exception as exc:  # network/auth/rate-limit errors from the SDK
        raise LLMError(f"Gemini request failed: {exc}") from exc

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ImagePost):
        return parsed

    text = getattr(response, "text", None)
    if not text:
        raise LLMError("Gemini response had no parsed output or text")
    try:
        return ImagePost.model_validate_json(text)
    except Exception as exc:
        raise LLMError(f"Gemini response failed schema validation: {exc}") from exc

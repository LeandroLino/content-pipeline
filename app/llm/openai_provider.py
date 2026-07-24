"""OpenAI provider -- generates ImagePost via Chat Completions structured output.

Mirrors app/llm/gemini_provider.py's prompt and interface. Untested against
the live API in this environment (no OPENAI_API_KEY configured yet), but
kept behind the same `factory.py` selection so it's a drop-in alternative
once a key is available.
"""

from app.llm.base import LLMError
from app.llm.prompts import build_prompt
from app.schemas import ImagePost, IngestPayload

DEFAULT_MODEL = "gpt-4o-mini"


def generate_image_post(payload: IngestPayload, api_key: str, model: str = DEFAULT_MODEL) -> ImagePost:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMError(f"openai package not installed: {exc}") from exc

    client = OpenAI(api_key=api_key)
    prompt = build_prompt(payload)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "ImagePost",
                    "schema": ImagePost.model_json_schema(),
                    "strict": True,
                },
            },
        )
    except Exception as exc:  # network/auth/rate-limit errors from the SDK
        raise LLMError(f"OpenAI request failed: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise LLMError("OpenAI response had empty content")
    try:
        return ImagePost.model_validate_json(content)
    except Exception as exc:
        raise LLMError(f"OpenAI response failed schema validation: {exc}") from exc

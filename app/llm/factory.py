"""Picks the LLM provider to use based on config, without callers needing
to know which module/SDK backs it.
"""

from app.config import LLMConfig, load_llm_config
from app.llm.base import LLMError
from app.schemas import ImagePost, IngestPayload

SUPPORTED_PROVIDERS = ("stub", "gemini", "openai")

## Futuramente adicionar fallback.
def generate_image_post(payload: IngestPayload, config: LLMConfig | None = None) -> ImagePost:
    """Generate an ImagePost using the provider selected by `LLM_PROVIDER`."""
    cfg = config or load_llm_config()

    if cfg.provider == "stub":
        from app.llm.stub import generate_image_post as stub_generate

        return stub_generate(payload)

    if cfg.provider == "gemini":
        if not cfg.gemini_api_key:
            raise LLMError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set")
        from app.llm.gemini_provider import generate_image_post as gemini_generate

        return gemini_generate(payload, api_key=cfg.gemini_api_key)

    if cfg.provider == "openai":
        if not cfg.openai_api_key:
            raise LLMError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
        from app.llm.openai_provider import generate_image_post as openai_generate

        return openai_generate(payload, api_key=cfg.openai_api_key)

    raise LLMError(
        f"unknown LLM_PROVIDER: {cfg.provider!r} (expected one of {SUPPORTED_PROVIDERS})"
    )

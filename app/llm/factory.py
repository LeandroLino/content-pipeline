"""Picks the LLM provider to use based on config, without callers needing
to know which module/SDK backs it.
"""

from app.config import LLMConfig, load_llm_config
from app.llm.base import LLMError
from app.schemas import ImagePost, IngestPayload

SUPPORTED_PROVIDERS = ("stub", "gemini", "openai")

# When a real provider is selected, automatically retry with the other real
# provider if it's configured -- covers both a missing API key and a runtime
# failure (network/auth/rate-limit) from the primary provider's SDK call.
# Deliberately does NOT include "stub" as a fallback target: silently
# swapping to the deterministic stub could ship low-quality captions to
# production without anyone noticing.
_FALLBACK_ORDER = {
    "gemini": ("gemini", "openai"),
    "openai": ("openai", "gemini"),
}


def _generate_with_provider(name: str, payload: IngestPayload, cfg: LLMConfig) -> ImagePost:
    if name == "gemini":
        if not cfg.gemini_api_key:
            raise LLMError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set")
        from app.llm.gemini_provider import generate_image_post as gemini_generate

        return gemini_generate(payload, api_key=cfg.gemini_api_key)

    if name == "openai":
        if not cfg.openai_api_key:
            raise LLMError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
        from app.llm.openai_provider import generate_image_post as openai_generate

        return openai_generate(payload, api_key=cfg.openai_api_key)

    raise LLMError(f"unknown provider: {name!r}")


def generate_image_post(payload: IngestPayload, config: LLMConfig | None = None) -> ImagePost:
    """Generate an ImagePost using the provider selected by `LLM_PROVIDER`.

    If the selected provider is a real AI provider (gemini/openai) and it
    fails -- missing API key or a runtime error from the SDK call -- this
    automatically retries with the other real provider, if configured. Only
    raises once every real provider candidate has failed.
    """
    cfg = config or load_llm_config()

    if cfg.provider == "stub":
        from app.llm.stub import generate_image_post as stub_generate

        return stub_generate(payload)

    if cfg.provider not in _FALLBACK_ORDER:
        raise LLMError(
            f"unknown LLM_PROVIDER: {cfg.provider!r} (expected one of {SUPPORTED_PROVIDERS})"
        )

    errors: list[str] = []
    for name in _FALLBACK_ORDER[cfg.provider]:
        try:
            result = _generate_with_provider(name, payload, cfg)
        except LLMError as exc:
            errors.append(f"{name}: {exc}")
            continue

        if name != cfg.provider:
            print(f"[llm] {cfg.provider} failed, fell back to {name}")
        return result

    raise LLMError("all LLM providers failed -- " + "; ".join(errors))

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    pass


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"missing required env var: {name}")
    return value


@dataclass(frozen=True)
class RedditConfig:
    client_id: str
    client_secret: str
    user_agent: str


def load_reddit_config() -> RedditConfig:
    return RedditConfig(
        client_id=_require("REDDIT_CLIENT_ID"),
        client_secret=_require("REDDIT_CLIENT_SECRET"),
        user_agent=_require("REDDIT_USER_AGENT"),
    )


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    openai_api_key: str | None
    gemini_api_key: str | None


def load_llm_config() -> LLMConfig:
    """Read LLM provider selection + API keys from env.

    `LLM_PROVIDER` defaults to "stub" (no network, no key needed) so the
    pipeline stays runnable without any LLM credentials configured.
    """
    return LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "stub").strip().lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
    )

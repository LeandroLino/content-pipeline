from app.config import LLMConfig
from app.llm.base import LLMError
from app.llm.factory import generate_image_post
from app.llm.stub import IMAGE_CAPTION_MAX_CHARS, generate_image_post as stub_generate
from app.schemas import ImagePost, IngestPayload

SAMPLE_PAYLOAD = IngestPayload(
    source="reddit",
    original_url="https://www.reddit.com/r/test/comments/abc123/example_post/",
    raw_title="Meu projeto de homelab está pronto",
    raw_content="Depois de meses construindo, finalmente terminei meu servidor de backup. "
    "Ele tem uma capacidade enorme e roda totalmente automatizado.",
    media_urls=["https://preview.redd.it/one.jpg"],
)


def test_image_post_schema_valid():
    post = ImagePost(post_caption="Legenda longa", image_caption="Legenda curta")
    assert post.post_caption == "Legenda longa"
    assert post.image_caption == "Legenda curta"


def test_stub_generates_deterministic_image_post():
    result = stub_generate(SAMPLE_PAYLOAD)

    assert isinstance(result, ImagePost)
    assert SAMPLE_PAYLOAD.raw_title in result.post_caption
    assert str(SAMPLE_PAYLOAD.original_url) in result.post_caption
    assert result.image_caption == SAMPLE_PAYLOAD.raw_title


def test_stub_truncates_long_titles_for_image_caption():
    payload = SAMPLE_PAYLOAD.model_copy(
        update={"raw_title": "T" * (IMAGE_CAPTION_MAX_CHARS + 20)}
    )
    result = stub_generate(payload)

    assert len(result.image_caption) <= IMAGE_CAPTION_MAX_CHARS
    assert result.image_caption.endswith("…")


def test_factory_dispatches_to_stub_by_default():
    config = LLMConfig(provider="stub", openai_api_key=None, gemini_api_key=None)
    result = generate_image_post(SAMPLE_PAYLOAD, config=config)
    assert isinstance(result, ImagePost)


def test_factory_raises_when_gemini_key_missing():
    config = LLMConfig(provider="gemini", openai_api_key=None, gemini_api_key=None)
    try:
        generate_image_post(SAMPLE_PAYLOAD, config=config)
        assert False, "expected LLMError"
    except LLMError as exc:
        assert "GEMINI_API_KEY" in str(exc)


def test_factory_raises_when_openai_key_missing():
    config = LLMConfig(provider="openai", openai_api_key=None, gemini_api_key=None)
    try:
        generate_image_post(SAMPLE_PAYLOAD, config=config)
        assert False, "expected LLMError"
    except LLMError as exc:
        assert "OPENAI_API_KEY" in str(exc)


def test_factory_raises_on_unknown_provider():
    config = LLMConfig(provider="unknown", openai_api_key=None, gemini_api_key=None)
    try:
        generate_image_post(SAMPLE_PAYLOAD, config=config)
        assert False, "expected LLMError"
    except LLMError as exc:
        assert "unknown" in str(exc)

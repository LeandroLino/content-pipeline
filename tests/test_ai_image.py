from unittest.mock import MagicMock, patch

import pytest
import requests

from app.media.ai_image import AIImageError, generate_ai_image


def _fake_response(content_type: str, content: bytes, status_ok: bool = True):
    response = MagicMock()
    response.headers = {"content-type": content_type}
    response.content = content
    if status_ok:
        response.raise_for_status = MagicMock()
    else:
        response.raise_for_status = MagicMock(side_effect=requests.HTTPError("500 error"))
    return response


def test_generate_ai_image_saves_response_bytes(tmp_path):
    fake_bytes = b"\xff\xd8\xff\xe0fakejpegbytes"
    output_path = tmp_path / "generated.jpg"

    with patch("app.media.ai_image.requests.get", return_value=_fake_response("image/jpeg", fake_bytes)) as mock_get:
        result = generate_ai_image("a cozy scene", output_path, width=1080, height=1350)

    assert result == output_path
    assert output_path.read_bytes() == fake_bytes
    mock_get.assert_called_once()
    called_url = mock_get.call_args[0][0]
    assert "a%20cozy%20scene" in called_url or "a+cozy+scene" in called_url or "cozy" in called_url


def test_generate_ai_image_rejects_empty_prompt(tmp_path):
    with pytest.raises(AIImageError, match="prompt must not be empty"):
        generate_ai_image("   ", tmp_path / "out.jpg", width=1080, height=1350)


def test_generate_ai_image_raises_on_request_exception(tmp_path):
    with patch("app.media.ai_image.requests.get", side_effect=requests.ConnectionError("boom")):
        with pytest.raises(AIImageError, match="Pollinations.ai request failed"):
            generate_ai_image("cena qualquer", tmp_path / "out.jpg", width=1080, height=1350)


def test_generate_ai_image_raises_on_http_error(tmp_path):
    response = _fake_response("image/jpeg", b"", status_ok=False)
    with patch("app.media.ai_image.requests.get", return_value=response):
        with pytest.raises(AIImageError, match="Pollinations.ai request failed"):
            generate_ai_image("cena qualquer", tmp_path / "out.jpg", width=1080, height=1350)


def test_generate_ai_image_raises_when_response_is_not_an_image(tmp_path):
    response = _fake_response("text/html", b"<html>rate limited</html>")
    with patch("app.media.ai_image.requests.get", return_value=response):
        with pytest.raises(AIImageError, match="did not return an image"):
            generate_ai_image("cena qualquer", tmp_path / "out.jpg", width=1080, height=1350)

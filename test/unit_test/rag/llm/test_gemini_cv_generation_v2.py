import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from rag.llm.cv_model import GeminiCV
from rag.llm.musemind_gemini import FIGURE_PROMPT_SHA256, PAGE_PROMPT_SHA256, REQUEST_TIMEOUT_MS


class FakeModels:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(text="synthetic-description", usage_metadata=SimpleNamespace(total_token_count=4))


def _model(fake_models):
    with patch("google.genai.Client") as client:
        client.return_value = SimpleNamespace(models=fake_models)
        model = GeminiCV("g" * 40, "gemini-3.5-flash", lang="Italian")
    return model, client.call_args.kwargs


def test_gemini_cv_pins_deterministic_config_prompt_identity_and_image_mime():
    models = FakeModels()
    model, client_kwargs = _model(models)

    text, tokens = model.describe(b"\x89PNG\r\n\x1a\nsynthetic")

    assert text == "synthetic-description"
    assert tokens == 4
    call = models.calls[0]
    assert call["model"] == "gemini-3.5-flash"
    assert call["contents"][0].parts[1].inline_data.mime_type == "image/png"
    config = call["config"]
    assert config.candidate_count == 1
    assert config.max_output_tokens == 2048
    assert config.response_mime_type == "text/plain"
    assert config.seed == 0
    assert config.temperature == 0.0
    assert config.top_p == 1.0
    assert config.thinking_config.thinking_budget == 0
    assert config.tools is None
    assert client_kwargs["http_options"].timeout == REQUEST_TIMEOUT_MS


def test_gemini_cv_runtime_prompt_bytes_match_adr_0078_checksums():
    prompt_dir = Path(__file__).resolve().parents[4] / "rag" / "prompts"

    assert hashlib.sha256((prompt_dir / "vision_llm_figure_describe_prompt.md").read_bytes()).hexdigest() == FIGURE_PROMPT_SHA256
    assert hashlib.sha256((prompt_dir / "vision_llm_describe_prompt.md").read_bytes()).hexdigest() == PAGE_PROMPT_SHA256


def test_gemini_cv_rejects_unsupported_image_without_provider_call():
    models = FakeModels()
    model, _ = _model(models)

    with pytest.raises(ValueError, match="JPEG or PNG"):
        model.describe(b"raw-content")
    assert models.calls == []

    with pytest.raises(ValueError, match="prompt is not pinned"):
        model.describe_with_prompt(b"\xff\xd8\xffsynthetic", "arbitrary prompt")
    assert models.calls == []


def test_gemini_cv_provider_error_is_content_free(caplog):
    secret = "provider-echoed-secret-content"
    model, _ = _model(FakeModels(RuntimeError(secret)))

    with pytest.raises(RuntimeError, match="image-description request failed") as error:
        model.describe(b"\xff\xd8\xffsynthetic")

    assert secret not in str(error.value)
    assert secret not in caplog.text

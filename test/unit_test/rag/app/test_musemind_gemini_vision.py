import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from common.constants import LLMType
from rag.app.musemind_vision import vision_llm_chunk
from rag.llm.musemind_gemini import MUSEMIND_GEMINI_IMAGE_ID

_figure_parser_spec = importlib.util.spec_from_file_location(
    "test_musemind_figure_parser_module",
    Path(__file__).resolve().parents[4] / "deepdoc" / "parser" / "figure_parser.py",
)
figure_parser = importlib.util.module_from_spec(_figure_parser_spec)
_figure_parser_spec.loader.exec_module(figure_parser)


def test_figure_parser_resolves_dataset_pinned_vision_coordinate(monkeypatch):
    exact = MagicMock(return_value={"llm_factory": "Gemini", "llm_name": "gemini-3.5-flash"})
    default = MagicMock()
    monkeypatch.setattr(figure_parser, "get_model_config_from_provider_instance", exact)
    monkeypatch.setattr(figure_parser, "get_tenant_default_model_by_type", default)

    config = figure_parser._vision_model_config(
        {"tenant_id": "tenant-1", "parser_config": {"img2txt_id": MUSEMIND_GEMINI_IMAGE_ID}}
    )

    assert config["llm_name"] == "gemini-3.5-flash"
    exact.assert_called_once_with("tenant-1", LLMType.IMAGE2TEXT, MUSEMIND_GEMINI_IMAGE_ID)
    default.assert_not_called()


def test_generation_v2_vision_failure_is_content_free_and_fail_closed():
    callbacks = []
    model = SimpleNamespace(
        mdl=SimpleNamespace(content_free_observability=True),
        describe_with_prompt=lambda _image, _prompt: (_ for _ in ()).throw(RuntimeError("secret-provider-body")),
    )

    with pytest.raises(RuntimeError, match="Pinned image-description request failed") as exc:
        vision_llm_chunk(Image.new("RGB", (16, 16)), model, prompt="not-observed", callback=lambda progress, message: callbacks.append((progress, message)))

    assert callbacks == [(-1, "CV LLM request failed.")]
    assert "secret" not in str(exc.value)
    assert "secret" not in repr(callbacks)


def test_legacy_vision_failure_preserves_best_effort_empty_result():
    model = SimpleNamespace(
        mdl=SimpleNamespace(content_free_observability=False),
        describe_with_prompt=lambda _image, _prompt: (_ for _ in ()).throw(RuntimeError("legacy failure")),
    )

    assert vision_llm_chunk(Image.new("RGB", (16, 16)), model, prompt="legacy") == ""

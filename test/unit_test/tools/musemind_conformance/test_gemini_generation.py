import json
from types import SimpleNamespace

import numpy as np
import pytest

from tools.musemind_conformance.gemini_generation import run_probe


class FakeEmbedding:
    def __init__(self, key, model):
        assert key == "AIza" + "x" * 60
        assert model == "gemini-embedding-2"
        self.encode_calls = 0

    def encode(self, inputs):
        assert len(inputs) == 1
        self.encode_calls += 1
        if self.encode_calls == 1:
            assert inputs[0].title == "synthetic"
            assert inputs[0].content == "synthetic"
        else:
            assert inputs[0].startswith(b"\x89PNG\r\n\x1a\n")
        return np.ones((1, 3072), dtype=np.float32), 1

    def encode_queries(self, query):
        assert query == "synthetic"
        return np.ones(3072, dtype=np.float32), 1


class FakeModels:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if error := self.failures.get(kwargs["model"]):
            raise error
        return SimpleNamespace(text="ok")


def test_gemini_probe_emits_only_strict_content_free_result():
    models = FakeModels()

    def client_factory(**kwargs):
        assert kwargs["api_key"] == "AIza" + "x" * 60
        return SimpleNamespace(models=models)

    result = run_probe(
        "AIza" + "x" * 60,
        embedding_factory=FakeEmbedding,
        client_factory=client_factory,
    )

    assert result == {
        "schema_version": "musemind.gemini-generation-probe/v1",
        "outcome": "PASSED",
        "endpoint_hostname": "generativelanguage.googleapis.com",
        "embedding_model": "gemini-embedding-2",
        "chat_model": "gemini-3.1-flash-lite",
        "image_to_text_model": "gemini-3.5-flash",
        "dimension": 3072,
    }
    serialized = json.dumps(result)
    assert "synthetic" not in serialized
    assert "AIza" not in serialized
    assert [call["model"] for call in models.calls] == ["gemini-3.1-flash-lite", "gemini-3.5-flash"]


@pytest.mark.parametrize(
    ("model", "stage", "call_count"),
    [
        ("gemini-3.1-flash-lite", "CHAT", 1),
        ("gemini-3.5-flash", "IMAGE_TO_TEXT", 2),
    ],
)
def test_gemini_probe_logs_content_free_failure_stage(model, stage, call_count, caplog):
    secret = "provider-echoed-secret-content"
    error = RuntimeError(secret)
    error.status_code = 429
    models = FakeModels({model: error})

    with caplog.at_level("ERROR", logger="musemind.ragflow.gemini"), pytest.raises(RuntimeError, match=f"Gemini {stage} probe failed") as raised:
        run_probe(
            "AIza" + "x" * 60,
            embedding_factory=FakeEmbedding,
            client_factory=lambda **_: SimpleNamespace(models=models),
        )

    assert len(models.calls) == call_count
    assert f"operation={stage} failure_class=HTTP_429 attempts=3" in caplog.text
    assert secret not in caplog.text
    assert secret not in str(raised.value)

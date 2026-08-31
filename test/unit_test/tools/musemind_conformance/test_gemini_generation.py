import json
from types import SimpleNamespace

import numpy as np

from tools.musemind_conformance.gemini_generation import run_probe


class FakeEmbedding:
    def __init__(self, key, model):
        assert key == "AIza" + "x" * 60
        assert model == "gemini-embedding-2"

    def encode(self, passages):
        assert len(passages) == 1
        return np.ones((1, 3072), dtype=np.float32), 1

    def encode_queries(self, query):
        assert query == "synthetic"
        return np.ones(3072, dtype=np.float32), 1


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
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

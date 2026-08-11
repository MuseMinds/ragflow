from __future__ import annotations

import json

import numpy as np

from tools.musemind_conformance.jina_generation import DIMENSION, main, run_probe


class FakeJinaClient:
    def __init__(self, key, model_name, base_url):
        assert key == "jina_" + "x" * 59
        assert model_name == "jina-embeddings-v3"
        assert base_url == "https://api.jina.ai/v1/embeddings"

    def encode(self, texts):
        assert len(texts) == 3
        vectors = np.zeros((3, DIMENSION), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors, 12

    def encode_queries(self, text):
        assert text
        vector = np.zeros(DIMENSION, dtype=np.float32)
        vector[0] = 1.0
        return vector, 4


def test_probe_is_content_free_and_deterministic():
    key = "jina_" + "x" * 59
    result = run_probe(key, client_factory=FakeJinaClient)
    serialized = json.dumps(result, sort_keys=True)

    assert result["outcome"] == "PASSED"
    assert result["dimension"] == 1024
    assert result["reported_tokens"] == 16
    assert result["query_passage_cosine"] == [1.0, 1.0, 1.0]
    assert key not in serialized
    assert "statua" not in serialized


def test_cli_failure_is_content_free(tmp_path, capsys):
    key_file = tmp_path / "jina-key"
    key_file.write_text("short", encoding="ascii")

    assert main(["--api-key-file", str(key_file)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "outcome": "FAILED",
        "reason_code": "SECRET_FILE_INVALID",
        "schema_version": "musemind.jina-generation-probe/v1",
    }

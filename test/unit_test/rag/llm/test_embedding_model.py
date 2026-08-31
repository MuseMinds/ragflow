#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

"""Tests for the embedding-provider fixes in ``rag.llm.embedding_model``:

* a failing embedding call raises a single deterministic, informative
  ``EmbeddingError`` (and the previous unreachable ``raise Exception(f"Error: {res}")``
  can no longer mask it, regardless of whether the SDK response exposes ``.text``);
* token counts reflect real usage, or an honest local fallback — never the old
  fabricated ``1024`` / ``+= 128`` constants;
* inputs at the truncation boundary are not pushed past the model token limit
  (the old ``8196`` overshoot is gone);
* ``ZhipuEmbed`` / ``OllamaEmbed`` now batch — ``ceil(n / batch_size)`` requests
  with input order and output shape preserved.
"""

import json
import base64
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from rag.llm.embedding_model import (
    DEFAULT_MAX_TOKENS,
    BedrockEmbed,
    EmbeddingError,
    GeminiEmbed,
    JinaMultiVecEmbed,
    LocalAIEmbed,
    MistralEmbed,
    NvidiaEmbed,
    OllamaEmbed,
    OpenAIEmbed,
    ZhipuEmbed,
)
from rag.llm.musemind_gemini import GeminiPassage, RETRYABLE_HTTP_STATUSES
from common.exceptions import ModelException
from common.token_utils import num_tokens_from_string


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _OpenAIResp:
    """Minimal stand-in for an OpenAI embeddings response.

    Unlike ``MagicMock`` it does NOT auto-create a ``usage`` attribute, so
    ``total_token_count_from_response`` correctly returns 0 when ``total_tokens``
    is not supplied (exercising the local-count fallback paths).
    """

    def __init__(self, vectors, total_tokens=None):
        self.data = [SimpleNamespace(embedding=list(v)) for v in vectors]
        if total_tokens is not None:
            self.usage = SimpleNamespace(total_tokens=total_tokens)


class _GeminiModels:
    def __init__(self, dimension=3072):
        self.calls = []
        self.dimension = dimension

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        vector = [0.0] * self.dimension
        vector[0] = 2.0
        return SimpleNamespace(embeddings=[SimpleNamespace(values=vector) for _ in kwargs["contents"]])


def _make_gemini(dimension=3072):
    models = _GeminiModels(dimension)
    with patch("google.genai.Client") as client:
        client.return_value = SimpleNamespace(models=models)
        embed = GeminiEmbed("g" * 40, "gemini-embedding-2")
    return embed, models, client.call_args.kwargs


def test_gemini_generation_v2_uses_typed_exact_text_and_image_contents():
    embed, models, client_kwargs = _make_gemini()
    png = b"\x89PNG\r\n\x1a\n" + b"synthetic"

    vectors, _ = embed.encode([GeminiPassage("Titolo", "Corpo"), png])
    query, _ = embed.encode_queries("cerca")

    assert vectors.shape == (2, 3072)
    assert query.shape == (3072,)
    assert vectors.dtype == np.float32
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    document_parts = models.calls[0]["contents"]
    assert len(document_parts) == 2
    assert document_parts[0].parts[0].text == "title: Titolo | text: Corpo"
    assert document_parts[1].parts[0].inline_data.mime_type == "image/png"
    assert models.calls[1]["contents"][0].parts[0].text == "task: search result | query: cerca"
    for call in models.calls:
        assert call["config"].task_type is None
        assert call["config"].auto_truncate is False
        assert call["config"].output_dimensionality == 3072
    retry = client_kwargs["http_options"].retry_options
    assert client_kwargs["http_options"].timeout == 10_000
    assert retry.attempts == 3
    assert tuple(retry.http_status_codes) == RETRYABLE_HTTP_STATUSES


def test_gemini_generation_v2_rejects_invalid_image_and_dimension():
    embed, _, _ = _make_gemini()
    with pytest.raises(ValueError, match="JPEG or PNG"):
        embed.encode([b"not-an-image"])
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode("ascii")
    vectors, _ = embed.encode([f"data:image/png;base64,{encoded}"])
    assert vectors.shape == (1, 3072)
    with pytest.raises(ValueError, match="data URL is invalid"):
        embed.encode(["data:image/png;base64,not-base64!"])

    wrong, _, _ = _make_gemini(dimension=3)
    with pytest.raises(EmbeddingError, match="dimension"):
        wrong.encode([GeminiPassage("title", "content")])


def _openai_create(total_tokens=None, dim=3):
    """Build a side_effect that returns one vector per input text."""

    def _create(input, model, **kwargs):
        return _OpenAIResp([[float(i)] * dim for i in range(len(input))], total_tokens=total_tokens)

    return _create


def _make_openai(cls=OpenAIEmbed, total_tokens=None):
    embed = cls("key", "text-embedding-3-small", base_url="https://example.invalid/v1")
    embed.client = MagicMock()
    embed.client.embeddings.create = MagicMock(side_effect=_openai_create(total_tokens=total_tokens))
    return embed


# --------------------------------------------------------------------------- #
# 1. Deterministic, informative error handling (the masked-error bug)
# --------------------------------------------------------------------------- #
class _BadRespWithText:
    """Parsing this raises; it also exposes ``.text`` — which the old
    ``log_exception(_e, res)`` path would have re-raised as a bare
    ``Exception(text)``, masking the intended error non-deterministically."""

    text = "Internal Server Error"

    @property
    def data(self):
        raise ValueError("malformed response payload")


class _BadRespNoText:
    @property
    def data(self):
        raise ValueError("malformed response payload")


@pytest.mark.p1
class TestDeterministicErrors:
    def test_api_error_raises_embedding_error(self):
        embed = _make_openai()
        embed.client.embeddings.create = MagicMock(side_effect=RuntimeError("503 upstream down"))
        with pytest.raises(EmbeddingError) as exc:
            embed.encode(["hello"])
        # Informative: surfaces the underlying detail and contains "Error".
        assert "503 upstream down" in str(exc.value)
        assert "Error" in str(exc.value)
        assert "OpenAIEmbed" in str(exc.value)

    def test_same_exception_type_with_and_without_text_attr(self):
        """The surfaced exception must NOT depend on whether the response object
        exposes ``.text`` (the old non-determinism). Both variants -> EmbeddingError."""
        with_text = _make_openai()
        with_text.client.embeddings.create = MagicMock(return_value=_BadRespWithText())
        without_text = _make_openai()
        without_text.client.embeddings.create = MagicMock(return_value=_BadRespNoText())

        with pytest.raises(EmbeddingError) as e1:
            with_text.encode(["x"])
        with pytest.raises(EmbeddingError) as e2:
            without_text.encode(["x"])

        # Deterministic: same type, and the response's ``.text`` did not hijack it.
        assert type(e1.value) is type(e2.value) is EmbeddingError
        assert "Internal Server Error" not in str(e1.value)
        assert "malformed response payload" in str(e1.value)

    def test_query_path_also_deterministic(self):
        embed = _make_openai()
        embed.client.embeddings.create = MagicMock(side_effect=RuntimeError("nope"))
        with pytest.raises(EmbeddingError):
            embed.encode_queries("hi")

    def test_http_bad_status_raises_model_exception_with_body(self):
        """A bad HTTP status surfaces the response body via a retryable-aware
        ModelException, which the API error handler understands."""
        embed = NvidiaEmbed("key", "nvidia/nv-embed-v1")
        bad = MagicMock()
        bad.status_code = 400
        bad.text = '{"error": "bad request: empty input"}'
        with patch("rag.llm.embedding_model.requests.post", return_value=bad):
            with pytest.raises(ModelException) as exc:
                embed.encode(["hello"])
        assert "bad request: empty input" in str(exc.value)

    def test_http_malformed_ok_response_raises_embedding_error(self):
        """A 200 response with an unexpected body still yields a deterministic
        EmbeddingError carrying the payload detail."""
        embed = NvidiaEmbed("key", "nvidia/nv-embed-v1")
        bad = MagicMock()
        bad.status_code = 200
        bad.json.return_value = {"unexpected": "shape"}
        with patch("rag.llm.embedding_model.requests.post", return_value=bad):
            with pytest.raises(EmbeddingError) as exc:
                embed.encode(["hello"])
        assert "unexpected" in str(exc.value)


# --------------------------------------------------------------------------- #
# 2. Token accounting (no fabricated 1024 / += 128)
# --------------------------------------------------------------------------- #
@pytest.mark.p1
class TestTokenAccounting:
    def test_openai_uses_reported_usage(self):
        embed = _make_openai(total_tokens=42)
        _, tokens = embed.encode(["a", "b"])
        assert tokens == 42

    def test_localai_falls_back_to_local_count_not_1024(self):
        embed = _make_openai(cls=LocalAIEmbed)  # no usage in response
        texts = ["hello world", "second chunk of text"]
        _, tokens = embed.encode(texts)
        expected = sum(num_tokens_from_string(t) for t in texts)
        assert tokens == expected
        assert tokens != 1024  # the old fabricated constant

    def test_ollama_uses_prompt_eval_count_not_128(self):
        embed = OllamaEmbed("x", "nomic-embed-text", base_url="http://localhost:11434")
        embed.client = MagicMock()
        embed.client.embed = MagicMock(return_value={"embeddings": [[0.1, 0.2], [0.3, 0.4]], "prompt_eval_count": 33})
        _, tokens = embed.encode(["aaa", "bbb"])
        assert tokens == 33
        assert tokens != 128 * 2  # the old fabricated per-text constant

    def test_ollama_token_fallback_when_server_omits_count(self):
        embed = OllamaEmbed("x", "nomic-embed-text", base_url="http://localhost:11434")
        embed.client = MagicMock()
        # No prompt_eval_count reported -> honest local count, not a fixed number.
        embed.client.embed = MagicMock(return_value={"embeddings": [[0.1, 0.2]]})
        texts = ["some text to embed"]
        _, tokens = embed.encode(texts)
        assert tokens == sum(num_tokens_from_string(t) for t in texts)


# --------------------------------------------------------------------------- #
# 3. Truncation boundary (no 8196 overshoot)
# --------------------------------------------------------------------------- #
@pytest.mark.p2
class TestTruncationBoundary:
    def test_default_limit_is_8192(self):
        assert DEFAULT_MAX_TOKENS == 8192

    def test_openai_input_truncated_below_model_limit(self):
        embed = _make_openai(total_tokens=1)
        # An input far above the 8K ceiling.
        huge = "word " * 12000
        embed.encode([huge])
        sent = embed.client.embeddings.create.call_args.kwargs["input"][0]
        # Truncated to the documented 8191 ceiling, never above the 8192 model limit.
        assert num_tokens_from_string(sent) <= 8191
        assert num_tokens_from_string(sent) <= DEFAULT_MAX_TOKENS

    def test_mistral_truncates_to_8192_not_8196(self):
        embed = MistralEmbed.__new__(MistralEmbed)
        embed.model_name = "mistral-embed"
        captured = {}

        def _embeddings(inputs, model):
            captured["inputs"] = inputs
            return _OpenAIResp([[0.0, 0.0]], total_tokens=1)

        embed.client = MagicMock()
        embed.client.embeddings.create = MagicMock(side_effect=_embeddings)
        huge = "word " * 12000
        embed.encode([huge])
        assert num_tokens_from_string(captured["inputs"][0]) <= DEFAULT_MAX_TOKENS


# --------------------------------------------------------------------------- #
# 4. Batching for Zhipu and Ollama (ceil(n / batch_size) requests)
# --------------------------------------------------------------------------- #
@pytest.mark.p1
class TestBatching:
    def test_zhipu_batches_instead_of_per_text(self):
        embed = ZhipuEmbed("key", "embedding-3")
        embed.client = MagicMock()
        embed.client.embeddings.create = MagicMock(side_effect=_openai_create(total_tokens=5))
        texts = [f"t{i}" for i in range(3)]
        vectors, _ = embed.encode(texts)
        # One request for 3 texts (batch_size 16) — NOT three per-text requests.
        assert embed.client.embeddings.create.call_count == 1
        assert vectors.shape[0] == 3

    def test_zhipu_issues_ceil_n_over_batch_calls(self):
        embed = ZhipuEmbed("key", "embedding-3")
        embed.client = MagicMock()
        embed.client.embeddings.create = MagicMock(side_effect=_openai_create(total_tokens=5))
        texts = [f"t{i}" for i in range(20)]  # batch_size 16 -> ceil(20/16) == 2
        vectors, _ = embed.encode(texts)
        assert embed.client.embeddings.create.call_count == 2
        assert vectors.shape[0] == 20

    def test_ollama_batches_and_preserves_order(self):
        embed = OllamaEmbed("x", "nomic-embed-text", base_url="http://localhost:11434")
        embed.client = MagicMock()

        def _embed(model, input, **kwargs):
            # Echo a recognisable vector per input so order can be checked.
            return {"embeddings": [[float(len(t))] for t in input], "prompt_eval_count": 1}

        embed.client.embed = MagicMock(side_effect=_embed)
        texts = ["a", "bb", "ccc"]
        vectors, _ = embed.encode(texts)

        # One batched request, not one per text.
        assert embed.client.embed.call_count == 1
        assert vectors.shape == (3, 1)
        # Order preserved: vector value equals input length.
        np.testing.assert_array_equal(vectors[:, 0], np.array([1.0, 2.0, 3.0]))

    def test_zhipu_realigns_out_of_order_response(self):
        """If the provider returns embeddings out of order, the per-item `index`
        must realign them with the input — otherwise chunks get wrong vectors."""
        embed = ZhipuEmbed("key", "embedding-3")
        embed.client = MagicMock()

        def _create(input, model, **kwargs):
            data = [SimpleNamespace(embedding=[float(i)], index=i) for i in range(len(input))]
            return SimpleNamespace(data=list(reversed(data)), usage=SimpleNamespace(total_tokens=1))

        embed.client.embeddings.create = MagicMock(side_effect=_create)
        vectors, _ = embed.encode(["t0", "t1", "t2"])
        np.testing.assert_array_equal(vectors[:, 0], np.array([0.0, 1.0, 2.0]))

    def test_nvidia_http_realigns_out_of_order_response(self):
        embed = NvidiaEmbed("key", "nvidia/nv-embed-v1")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "data": [
                {"index": 2, "embedding": [2.0]},
                {"index": 0, "embedding": [0.0]},
                {"index": 1, "embedding": [1.0]},
            ],
            "usage": {"total_tokens": 3},
        }
        with patch("rag.llm.embedding_model.requests.post", return_value=resp):
            vectors, _ = embed.encode(["a", "b", "c"])
        np.testing.assert_array_equal(vectors[:, 0], np.array([0.0, 1.0, 2.0]))

    def test_ollama_issues_ceil_n_over_batch_calls(self):
        embed = OllamaEmbed("x", "nomic-embed-text", base_url="http://localhost:11434")
        embed.client = MagicMock()
        embed.client.embed = MagicMock(side_effect=lambda model, input, **kw: {"embeddings": [[0.0] for _ in input], "prompt_eval_count": 1})
        texts = [f"t{i}" for i in range(20)]  # batch_size 16 -> 2 calls
        vectors, _ = embed.encode(texts)
        assert embed.client.embed.call_count == 2
        assert vectors.shape[0] == 20


# --------------------------------------------------------------------------- #
# 5. Provider-specific request/response shapes
# --------------------------------------------------------------------------- #
@pytest.mark.p2
class TestNvidiaInputType:
    """NVIDIA NIM expects input_type=passage for documents and =query for queries;
    using "query" for documents degrades retrieval (asymmetric embeddings)."""

    def _mock_resp(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": [{"index": 0, "embedding": [1.0]}], "usage": {"total_tokens": 1}}
        return resp

    def test_documents_use_passage(self):
        embed = NvidiaEmbed("key", "nvidia/nv-embed-v1")
        with patch("rag.llm.embedding_model.requests.post", return_value=self._mock_resp()) as post:
            embed.encode(["a document"])
        assert post.call_args.kwargs["json"]["input_type"] == "passage"

    def test_queries_use_query(self):
        embed = NvidiaEmbed("key", "nvidia/nv-embed-v1")
        with patch("rag.llm.embedding_model.requests.post", return_value=self._mock_resp()) as post:
            embed.encode_queries("a query")
        assert post.call_args.kwargs["json"]["input_type"] == "query"


@pytest.mark.p1
class TestMuseMindJinaV3Contract:
    @staticmethod
    def _response(indexes=(0,)):
        response = MagicMock()
        response.status_code = 200
        vector = [0.0] * 1024
        vector[0] = 1.0
        response.json.return_value = {
            "data": [
                {"index": index, "embedding": vector}
                for index in indexes
            ],
            "usage": {"total_tokens": len(indexes)},
        }
        return response

    def test_passage_request_sends_every_output_affecting_v3_default(self):
        embed = JinaMultiVecEmbed("jina_secret", "jina-embeddings-v3")
        with patch("rag.llm.embedding_model.requests.post", return_value=self._response()) as post:
            vectors, tokens = embed.encode(["Una sala del museo"])

        assert vectors.shape == (1, 1024)
        assert tokens == 1
        assert post.call_args.kwargs["timeout"] == 30
        assert post.call_args.kwargs["json"] == {
            "model": "jina-embeddings-v3",
            "input": ["Una sala del museo"],
            "task": "retrieval.passage",
            "truncate": True,
            "normalized": True,
            "embedding_type": "float",
            "dimensions": 1024,
            "late_chunking": False,
        }

    def test_query_uses_query_adapter_without_fallback(self):
        embed = JinaMultiVecEmbed("jina_secret", "jina-embeddings-v3")
        with patch("rag.llm.embedding_model.requests.post", return_value=self._response()) as post:
            vector, _ = embed.encode_queries("Dove si trova il dipinto?")

        assert vector.shape == (1024,)
        assert post.call_args.kwargs["json"]["task"] == "retrieval.query"

    @pytest.mark.parametrize(
        "endpoint",
        ["https://example.invalid/v1/embeddings", "http://api.jina.ai/v1/embeddings"],
    )
    def test_v3_endpoint_drift_fails_before_network(self, endpoint):
        embed = JinaMultiVecEmbed("jina_secret", "jina-embeddings-v3", base_url=endpoint)
        with patch("rag.llm.embedding_model.requests.post") as post:
            with pytest.raises(EmbeddingError, match="endpoint differs"):
                embed.encode(["testo"])
        post.assert_not_called()

    def test_binary_input_fails_closed_before_network(self):
        embed = JinaMultiVecEmbed("jina_secret", "jina-embeddings-v3")
        with patch("rag.llm.embedding_model.requests.post") as post:
            with pytest.raises(EmbeddingError, match="text inputs only"):
                embed.encode([b"not-text"])
        post.assert_not_called()

    def test_dimension_or_normalization_drift_fails_closed(self):
        embed = JinaMultiVecEmbed("jina_secret", "jina-embeddings-v3")
        bad_dimension = self._response()
        bad_dimension.json.return_value["data"][0]["embedding"] = [1.0, 0.0]
        with patch("rag.llm.embedding_model.requests.post", return_value=bad_dimension):
            with pytest.raises(EmbeddingError, match="dimension"):
                embed.encode(["testo"])

        bad_norm = self._response()
        bad_norm.json.return_value["data"][0]["embedding"][0] = 2.0
        with patch("rag.llm.embedding_model.requests.post", return_value=bad_norm):
            with pytest.raises(EmbeddingError, match="normalization"):
                embed.encode(["testo"])

    def test_http_failure_propagates_without_calling_another_embedder(self):
        embed = JinaMultiVecEmbed("jina_secret", "jina-embeddings-v3")
        response = MagicMock()
        response.status_code = 429
        response.text = '{"detail":"rate limited"}'
        with patch("rag.llm.embedding_model.requests.post", return_value=response) as post:
            with pytest.raises(ModelException, match="rate limited"):
                embed.encode(["testo"])
        assert post.call_count == 1


@pytest.mark.p2
class TestBedrockResponseParsing:
    """Bedrock Titan returns {"embedding": [...]}; Cohere returns
    {"embeddings": [[...]]}. Both must parse without KeyError."""

    @staticmethod
    def _make(model_prefix):
        embed = BedrockEmbed.__new__(BedrockEmbed)
        embed.model_name = f"{model_prefix}.embed-model"
        embed.is_amazon = model_prefix == "amazon"
        embed.is_cohere = model_prefix == "cohere"
        embed.client = MagicMock()
        return embed

    @staticmethod
    def _body(payload):
        body = MagicMock()
        body.read.return_value = json.dumps(payload).encode()
        return {"body": body}

    def test_cohere_reads_embeddings_plural(self):
        embed = self._make("cohere")
        embed.client.invoke_model.return_value = self._body({"embeddings": [[1.0, 2.0]]})
        vectors, _ = embed.encode(["hello"])
        assert vectors.shape == (1, 2)
        np.testing.assert_array_equal(vectors[0], np.array([1.0, 2.0]))

    def test_amazon_reads_embedding_singular(self):
        embed = self._make("amazon")
        embed.client.invoke_model.return_value = self._body({"embedding": [3.0, 4.0]})
        vectors, _ = embed.encode(["hello"])
        np.testing.assert_array_equal(vectors[0], np.array([3.0, 4.0]))

    def test_cohere_query_reads_embeddings_plural(self):
        embed = self._make("cohere")
        embed.client.invoke_model.return_value = self._body({"embeddings": [[5.0, 6.0]]})
        vector, _ = embed.encode_queries("q")
        np.testing.assert_array_equal(vector, np.array([5.0, 6.0]))

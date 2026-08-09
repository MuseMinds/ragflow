#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
import pytest
from ragflow_sdk.modules.dataset import DataSet
from ragflow_sdk.ragflow import RAGFlow


@pytest.mark.p2
@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("post", lambda client: client.post("/resource", json={})),
        ("get", lambda client: client.get("/resource")),
        ("delete", lambda client: client.delete("/resource", {})),
        ("put", lambda client: client.put("/resource", {})),
        ("patch", lambda client: client.patch("/resource", {})),
    ],
)
def test_all_sdk_requests_use_explicit_configured_timeout(monkeypatch, method, call):
    captured = {}

    def request_stub(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(f"ragflow_sdk.ragflow.requests.{method}", request_stub)
    client = RAGFlow("secret", "https://ragflow.invalid", request_timeout=(2, 7))

    call(client)

    assert captured["timeout"] == (2, 7)


def test_sdk_rejects_unbounded_timeout():
    with pytest.raises(ValueError, match="bounded"):
        RAGFlow("secret", "https://ragflow.invalid", request_timeout=None)


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_exact_retrieval_sends_only_pair_scope(monkeypatch):
    client = RAGFlow("secret", "https://ragflow.invalid")
    captured = {}

    def post_stub(path, json=None, **_kwargs):
        captured["path"] = path
        captured["json"] = json
        return _JsonResponse(
            {
                "code": 0,
                "data": {
                    "chunks": [
                        {
                            "id": "chunk-1",
                            "dataset_id": "dataset-1",
                            "document_id": "document-1",
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(client, "post", post_stub)
    chunks = client.retrieve_exact(
        [{"dataset_id": "dataset-1", "document_id": "document-1"}],
        "question",
    )

    assert captured["path"] == "/retrieval"
    assert captured["json"]["exact_mode"] is True
    assert captured["json"]["document_scope"] == [{"dataset_id": "dataset-1", "document_id": "document-1"}]
    assert "dataset_ids" not in captured["json"]
    assert "document_ids" not in captured["json"]
    assert chunks[0].dataset_id == "dataset-1"
    with pytest.raises(ValueError, match="non-empty"):
        client.retrieve_exact([], "question")
    with pytest.raises(ValueError, match="non-empty string"):
        client.retrieve_exact([{"dataset_id": "dataset-1", "document_id": ""}], "question")


def test_exact_create_uses_opaque_filename_and_form_id(monkeypatch):
    client = RAGFlow("secret", "https://ragflow.invalid")
    dataset = DataSet(client, {"id": "dataset-1"})
    document_id = "0123456789abcdef0123456789abcdef"
    captured = {}

    def post_stub(path, json=None, **kwargs):
        captured["path"] = path
        captured["json"] = json
        captured.update(kwargs)
        return _JsonResponse({"code": 0, "data": [{"id": document_id, "dataset_id": "dataset-1"}]})

    monkeypatch.setattr(client, "post", post_stub)
    document = dataset.create_document_exact(document_id, "sensitive-original-name.PDF", b"bytes")

    assert captured["path"] == "/datasets/dataset-1/documents"
    assert captured["data"] == {"document_id": document_id}
    assert captured["files"] == [("file", (f"{document_id}.pdf", b"bytes"))]
    assert document.id == document_id
    with pytest.raises(ValueError, match="32 lowercase hexadecimal"):
        dataset.create_document_exact("bad", "document.pdf", b"bytes")

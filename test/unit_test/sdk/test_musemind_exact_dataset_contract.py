#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
from types import SimpleNamespace

import pytest
from ragflow_sdk import RAGFlow

DATASET_ID = "0123456789abcdef0123456789abcdef"


def _projection():
    return {
        "language": "Italian",
        "embd_id": "jina-embeddings-v3@musemind@Jina",
        "parser_id": "naive",
        "parser_config": {},
        "similarity_threshold": 0.2,
        "vector_similarity_weight": 0.3,
        "pagerank": 0,
        "pipeline_id": None,
    }


def test_sdk_sends_dedicated_exact_dataset_contract(monkeypatch):
    client = object.__new__(RAGFlow)
    calls = []
    response = SimpleNamespace(json=lambda: {"code": 0, "data": {"outcome": "CREATED", "dataset_id": DATASET_ID, "provider_projection": {}}})
    monkeypatch.setattr(client, "post", lambda path, payload: calls.append((path, payload)) or response)

    result = client.create_or_adopt_dataset_exact(DATASET_ID, _projection())

    assert result["outcome"] == "CREATED"
    assert calls == [
        (
            "/datasets/musemind/exact",
            {
                "schema": "musemind.ragflow-dataset-create-or-adopt/v1",
                "dataset_id": DATASET_ID,
                "provider_projection": _projection(),
            },
        )
    ]


def test_sdk_sends_generation_v2_exact_dataset_contract(monkeypatch):
    client = object.__new__(RAGFlow)
    calls = []
    response = SimpleNamespace(json=lambda: {"code": 0, "data": {"outcome": "ADOPTED", "dataset_id": DATASET_ID}})
    monkeypatch.setattr(client, "post", lambda path, payload: calls.append((path, payload)) or response)
    projection = {
        **_projection(),
        "embd_id": "gemini-embedding-2@musemind@Gemini",
        "llm_id": "gemini-3.1-flash-lite@musemind@Gemini",
        "img2txt_id": "gemini-3.5-flash@musemind@Gemini",
    }

    client.create_or_adopt_dataset_exact(DATASET_ID, projection)

    assert calls[0][1]["schema"] == "musemind.ragflow-dataset-create-or-adopt/v2"


@pytest.mark.parametrize(
    ("dataset_id", "projection"),
    [
        ("ABC", _projection()),
        (DATASET_ID, {**_projection(), "unknown": True}),
        (DATASET_ID, {**_projection(), "pipeline_id": "0" * 32}),
    ],
)
def test_sdk_rejects_invalid_contract_before_http(monkeypatch, dataset_id, projection):
    client = object.__new__(RAGFlow)
    monkeypatch.setattr(client, "post", lambda *_args, **_kwargs: pytest.fail("HTTP must not be called"))

    with pytest.raises(ValueError):
        client.create_or_adopt_dataset_exact(dataset_id, projection)

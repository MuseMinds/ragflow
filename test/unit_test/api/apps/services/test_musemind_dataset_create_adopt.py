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
from peewee import IntegrityError

from api.utils.musemind_provider_contract import (
    MUSEMIND_DATASET_COLLISION,
    MUSEMIND_DATASET_PROJECTION_SCHEMA,
    create_or_adopt_dataset_identity,
)

DATASET_ID = "0123456789abcdef0123456789abcdef"


def _dataset(**overrides):
    values = {
        "id": DATASET_ID,
        "name": f"mm-{DATASET_ID}",
        "language": "Italian",
        "embd_id": "jina-embeddings-v3@musemind@Jina",
        "tenant_embd_id": 42,
        "parser_id": "naive",
        "parser_config": {"chunk_token_num": 512, "llm_id": "chat-model"},
        "similarity_threshold": 0.2,
        "vector_similarity_weight": 0.3,
        "pagerank": 0,
        "pipeline_id": None,
        "permission": "team",
        "status": "1",
        "description": "",
        "avatar": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _expected():
    dataset = _dataset()
    return {
        "schema": MUSEMIND_DATASET_PROJECTION_SCHEMA,
        "dataset_id": dataset.id,
        "name": dataset.name,
        "language": dataset.language,
        "embd_id": dataset.embd_id,
        "parser_id": dataset.parser_id,
        "parser_config": dataset.parser_config,
        "similarity_threshold": dataset.similarity_threshold,
        "vector_similarity_weight": dataset.vector_similarity_weight,
        "pagerank": dataset.pagerank,
        "pipeline_id": dataset.pipeline_id,
        "permission": dataset.permission,
        "status": dataset.status,
        "description": dataset.description,
        "avatar": dataset.avatar,
    }


def _execute(insert, read):
    return create_or_adopt_dataset_identity(
        dataset_id=DATASET_ID,
        payload={"id": DATASET_ID},
        expected_projection=_expected(),
        expected_tenant_embd_id=42,
        insert=insert,
        read_for_authenticated_tenant=read,
    )


def test_exact_dataset_create_returns_only_allowlisted_projection():
    saved = []

    success, result = _execute(saved.append, lambda _dataset_id: _dataset())

    assert success is True
    assert result["outcome"] == "CREATED"
    assert result["dataset_id"] == DATASET_ID
    assert result["provider_projection"]["name"] == f"mm-{DATASET_ID}"
    assert "tenant_id" not in result["provider_projection"]
    assert saved == [{"id": DATASET_ID}]


def test_exact_dataset_primary_key_race_adopts_same_projection():
    def duplicate(_payload):
        raise IntegrityError(1062, "Duplicate entry 'id' for key 'PRIMARY'")

    success, result = _execute(duplicate, lambda _dataset_id: _dataset())

    assert success is True
    assert result["outcome"] == "ADOPTED"


def test_exact_dataset_current_registry_adopts_with_null_legacy_surrogate():
    def duplicate(_payload):
        raise IntegrityError(1062, "Duplicate entry 'id' for key 'PRIMARY'")

    success, result = create_or_adopt_dataset_identity(
        dataset_id=DATASET_ID,
        payload={"id": DATASET_ID, "tenant_embd_id": None},
        expected_projection=_expected(),
        expected_tenant_embd_id=None,
        insert=duplicate,
        read_for_authenticated_tenant=lambda _dataset_id: _dataset(tenant_embd_id=None),
    )

    assert success is True
    assert result["outcome"] == "ADOPTED"


@pytest.mark.parametrize("existing", [None, _dataset(parser_id="book"), _dataset(tenant_embd_id=99)])
def test_exact_dataset_collision_is_indistinguishable(existing):
    def duplicate(_payload):
        raise IntegrityError(1062, "Duplicate entry 'id' for key 'PRIMARY'")

    assert _execute(duplicate, lambda _dataset_id: existing) == (False, MUSEMIND_DATASET_COLLISION)


def test_non_primary_insert_failure_is_not_reclassified_as_adoption():
    def duplicate_name(_payload):
        raise IntegrityError(1062, "Duplicate entry 'name' for key 'idx_name'")

    with pytest.raises(IntegrityError):
        _execute(duplicate_name, lambda _dataset_id: pytest.fail("readback must not run"))

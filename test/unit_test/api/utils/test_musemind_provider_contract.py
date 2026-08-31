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
from types import SimpleNamespace

import pytest
from peewee import IntegrityError
from pydantic import ValidationError

from api.utils.musemind_provider_contract import (
    MuseMindDatasetCreateOrAdoptV1,
    MuseMindDatasetCreateOrAdoptV2,
    canonical_dataset_projection_bytes,
    is_knowledgebase_primary_key_conflict,
    prepare_exact_document_upload,
)


def test_prepare_exact_document_upload_makes_identity_opaque_and_create_only():
    upload = SimpleNamespace(filename="sensitive-original-name.PDF")
    document_id = "0123456789abcdef0123456789abcdef"

    create_only = prepare_exact_document_upload([upload], document_id)

    assert create_only is True
    assert upload.id == document_id
    assert upload.filename == f"{document_id}.pdf"


@pytest.mark.parametrize(
    ("uploads", "document_id", "message"),
    [
        ([SimpleNamespace(filename="document.txt")], "not-hex", "32 lowercase hexadecimal"),
        ([], "0123456789abcdef0123456789abcdef", "exactly one local file"),
        (
            [SimpleNamespace(filename="a.txt"), SimpleNamespace(filename="b.txt")],
            "0123456789abcdef0123456789abcdef",
            "exactly one local file",
        ),
    ],
)
def test_prepare_exact_document_upload_rejects_invalid_identity(uploads, document_id, message):
    with pytest.raises(ValueError, match=message):
        prepare_exact_document_upload(uploads, document_id)


def test_prepare_exact_document_upload_preserves_legacy_uploads():
    upload = SimpleNamespace(filename="original.txt")

    assert prepare_exact_document_upload([upload], "") is False
    assert upload.filename == "original.txt"
    assert not hasattr(upload, "id")


def _dataset_request(**overrides):
    request = {
        "schema": "musemind.ragflow-dataset-create-or-adopt/v1",
        "dataset_id": "0123456789abcdef0123456789abcdef",
        "provider_projection": {
            "language": "Italian",
            "embd_id": "jina-embeddings-v3@musemind@Jina",
            "parser_id": "naive",
            "parser_config": {},
            "similarity_threshold": 0.2,
            "vector_similarity_weight": 0.3,
            "pagerank": 0,
            "pipeline_id": None,
        },
    }
    request.update(overrides)
    return request


def test_exact_dataset_request_is_strict_and_expands_parser_defaults():
    model = MuseMindDatasetCreateOrAdoptV1(**_dataset_request())

    assert model.dataset_id == "0123456789abcdef0123456789abcdef"
    assert model.provider_projection.parser_config.chunk_token_num == 512


def test_generation_v2_request_pins_all_three_models_and_multimodal_parser_fields():
    request = {
        "schema": "musemind.ragflow-dataset-create-or-adopt/v2",
        "dataset_id": "0123456789abcdef0123456789abcdef",
        "provider_projection": {
            "language": "Italian",
            "embd_id": "gemini-embedding-2@musemind@Gemini",
            "llm_id": "gemini-3.1-flash-lite@musemind@Gemini",
            "img2txt_id": "gemini-3.5-flash@musemind@Gemini",
            "parser_id": "naive",
            "parser_config": {
                "llm_id": "gemini-3.1-flash-lite@musemind@Gemini",
                "img2txt_id": "gemini-3.5-flash@musemind@Gemini",
                "overlapped_percent": 0,
                "image_context_size": 0,
            },
            "similarity_threshold": 0.2,
            "vector_similarity_weight": 0.3,
            "pagerank": 0,
            "pipeline_id": None,
        },
    }

    model = MuseMindDatasetCreateOrAdoptV2(**request)

    assert model.provider_projection.parser_config.layout_recognize == "DeepDOC"
    assert model.provider_projection.parser_config.overlapped_percent == 0
    assert model.provider_projection.parser_config.image_context_size == 0
    assert model.provider_projection.parser_config.delimiter == "\n"
    assert model.provider_projection.parser_config.raptor.prompt == "Summarize {cluster_content}"

    request["provider_projection"]["parser_config"]["img2txt_id"] = "wrong"
    with pytest.raises(ValidationError):
        MuseMindDatasetCreateOrAdoptV2(**request)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request.update(dataset_id="0123456789ABCDEF0123456789ABCDEF"),
        lambda request: request.update(extra="forbidden"),
        lambda request: request["provider_projection"].pop("pipeline_id"),
        lambda request: request["provider_projection"].update(pipeline_id="0" * 32),
        lambda request: request["provider_projection"].update(similarity_threshold=float("nan")),
        lambda request: request["provider_projection"].update(parser_config={"llm_id": "caller-controlled"}),
    ],
)
def test_exact_dataset_request_rejects_invalid_or_ambiguous_fields(mutate):
    request = _dataset_request()
    mutate(request)

    with pytest.raises(ValidationError):
        MuseMindDatasetCreateOrAdoptV1(**request)


def test_dataset_projection_uses_canonical_key_order():
    left = {"schema": "v1", "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "schema": "v1"}

    assert canonical_dataset_projection_bytes(left) == canonical_dataset_projection_bytes(right)


def test_only_mysql_primary_key_duplicate_is_adoptable():
    assert is_knowledgebase_primary_key_conflict(IntegrityError(1062, "Duplicate entry 'id' for key 'PRIMARY'"))
    assert not is_knowledgebase_primary_key_conflict(IntegrityError(1062, "Duplicate entry 'name' for key 'idx_name'"))
    assert not is_knowledgebase_primary_key_conflict(IntegrityError(1205, "Lock wait timeout"))

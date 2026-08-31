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
import re
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

import rfc8785
from peewee import IntegrityError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from api.utils.validation_utils import ParentChildConfig, ParserConfig, RaptorConfig

MUSEMIND_DATASET_PROJECTION_SCHEMA = "musemind.ragflow-dataset-provider-projection/v1"
MUSEMIND_DATASET_PROJECTION_SCHEMA_V2 = "musemind.ragflow-dataset-provider-projection/v2"
MUSEMIND_DATASET_COLLISION = "PROVIDER_DATASET_IDENTITY_COLLISION"


class MuseMindDatasetProviderProjectionV1(BaseModel):
    """Strict caller-controlled part of the MuseMind dataset projection."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    language: Annotated[str, StringConstraints(min_length=1, max_length=32), Field(...)]
    embd_id: Annotated[str, StringConstraints(min_length=3, max_length=128), Field(...)]
    parser_id: Annotated[
        Literal["naive", "book", "email", "laws", "manual", "one", "paper", "picture", "presentation", "qa", "table", "tag", "resume"],
        Field(...),
    ]
    parser_config: Annotated[ParserConfig, Field(...)]
    similarity_threshold: Annotated[float, Field(..., ge=0.0, le=1.0)]
    vector_similarity_weight: Annotated[float, Field(..., ge=0.0, le=1.0)]
    pagerank: Annotated[int, Field(..., ge=0, le=9_007_199_254_740_991)]
    pipeline_id: None

    @field_validator("parser_config", mode="before")
    @classmethod
    def reject_server_derived_parser_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "llm_id" in value:
            raise ValueError("parser_config.llm_id is server-derived")
        return value


class MuseMindDatasetCreateOrAdoptV1(BaseModel):
    """Dedicated exact-ID create-or-adopt wire contract."""

    model_config = ConfigDict(extra="forbid", strict=True)

    contract_schema: Literal["musemind.ragflow-dataset-create-or-adopt/v1"] = Field(alias="schema", serialization_alias="schema")
    dataset_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$"), Field(...)]
    provider_projection: MuseMindDatasetProviderProjectionV1


class MuseMindParserConfigV2(ParserConfig):
    """Generation-v2 request fields absent from the generic dataset API."""

    delimiter: Literal["\n"] = "\n"
    parent_child: ParentChildConfig = Field(default_factory=lambda: ParentChildConfig(use_parent_child=False, children_delimiter="\n"))
    raptor: RaptorConfig = Field(default_factory=lambda: RaptorConfig(use_raptor=False, prompt="Summarize {cluster_content}"))
    overlapped_percent: Annotated[int, Field(default=0, ge=0, le=100)]
    image_context_size: Annotated[int, Field(default=0, ge=0, le=32)]
    llm_id: Annotated[str, Field(min_length=3, max_length=128)]
    img2txt_id: Annotated[str, Field(min_length=3, max_length=128)]


class MuseMindDatasetProviderProjectionV2(BaseModel):
    """Pinned Gemini generation-v2 dataset projection."""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    language: Literal["Italian"]
    embd_id: Literal["gemini-embedding-2@musemind@Gemini"]
    llm_id: Literal["gemini-3.1-flash-lite@musemind@Gemini"]
    img2txt_id: Literal["gemini-3.5-flash@musemind@Gemini"]
    parser_id: Literal["naive"]
    parser_config: MuseMindParserConfigV2
    similarity_threshold: Annotated[float, Field(ge=0.0, le=1.0)]
    vector_similarity_weight: Annotated[float, Field(ge=0.0, le=1.0)]
    pagerank: Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
    pipeline_id: None

    @model_validator(mode="after")
    def validate_pinned_parser_models(self):
        if self.parser_config.llm_id != self.llm_id or self.parser_config.img2txt_id != self.img2txt_id:
            raise ValueError("parser model IDs must match the pinned projection")
        expected_parser = MuseMindParserConfigV2(llm_id=self.llm_id, img2txt_id=self.img2txt_id)
        if self.parser_config.model_dump() != expected_parser.model_dump():
            raise ValueError("parser_config must match the pinned generation-v2 profile")
        if self.similarity_threshold != 0.2 or self.vector_similarity_weight != 0.3 or self.pagerank != 0:
            raise ValueError("retrieval fields must match the pinned generation-v2 profile")
        return self


class MuseMindDatasetCreateOrAdoptV2(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    contract_schema: Literal["musemind.ragflow-dataset-create-or-adopt/v2"] = Field(alias="schema", serialization_alias="schema")
    dataset_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$"), Field(...)]
    provider_projection: MuseMindDatasetProviderProjectionV2


def canonical_dataset_projection_bytes(projection: dict[str, Any]) -> bytes:
    """Serialize a normalized provider projection using RFC 8785/JCS."""
    return rfc8785.dumps(projection)


def is_knowledgebase_primary_key_conflict(exc: BaseException) -> bool:
    """Recognize only MySQL duplicate-key errors for knowledgebase.id/PRIMARY."""
    if not exc.args or exc.args[0] != 1062:
        return False
    message = str(exc)
    return bool(re.search(r"(?:for key|key)\s+['`\"]?(?:knowledgebase\.)?PRIMARY['`\"]?", message, re.IGNORECASE))


def normalized_dataset_projection(dataset: Any, schema: str = MUSEMIND_DATASET_PROJECTION_SCHEMA) -> dict[str, Any]:
    """Return only the allowlisted dataset projection used for identity equality."""
    return {
        "schema": schema,
        "dataset_id": dataset.id,
        "name": dataset.name,
        "language": dataset.language,
        "embd_id": dataset.embd_id,
        **({"llm_id": dataset.parser_config.get("llm_id"), "img2txt_id": dataset.parser_config.get("img2txt_id")} if schema == MUSEMIND_DATASET_PROJECTION_SCHEMA_V2 else {}),
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


def create_or_adopt_dataset_identity(
    *,
    dataset_id: str,
    payload: dict[str, Any],
    expected_projection: dict[str, Any],
    expected_tenant_embd_id: int | None,
    insert: Callable[[dict[str, Any]], Any],
    read_for_authenticated_tenant: Callable[[str], Any | None],
) -> tuple[bool, dict[str, Any] | str]:
    """Execute the exact insert/PK-race/readback state machine."""
    try:
        insert(payload)
        outcome = "CREATED"
    except IntegrityError as exc:
        if not is_knowledgebase_primary_key_conflict(exc):
            raise
        outcome = "ADOPTED"

    dataset = read_for_authenticated_tenant(dataset_id)
    if dataset is None:
        return False, MUSEMIND_DATASET_COLLISION

    actual = normalized_dataset_projection(dataset, expected_projection.get("schema", MUSEMIND_DATASET_PROJECTION_SCHEMA))
    if dataset.tenant_embd_id != expected_tenant_embd_id or canonical_dataset_projection_bytes(actual) != canonical_dataset_projection_bytes(expected_projection):
        return False, MUSEMIND_DATASET_COLLISION

    return True, {"outcome": outcome, "dataset_id": dataset_id, "provider_projection": actual}


def prepare_exact_document_upload(file_objs, requested_document_id: str) -> bool:
    """Apply the MuseMind single-file create-only identity contract in place."""
    if not requested_document_id:
        return False
    if re.fullmatch(r"[0-9a-f]{32}", requested_document_id) is None:
        raise ValueError("`document_id` must be 32 lowercase hexadecimal characters")
    if len(file_objs) != 1:
        raise ValueError("A caller-supplied `document_id` requires exactly one local file")

    suffix = Path(file_objs[0].filename or "").suffix.lower()
    file_objs[0].id = requested_document_id
    file_objs[0].filename = f"{requested_document_id}{suffix}"
    return True

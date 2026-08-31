#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
"""MM-RF-0016 authorization against the ADR-0037 current model registry."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

from peewee import IntegrityError

from api.db.musemind_provider_identity import reconcile_provider_identity
from api.utils.musemind_provider_contract import MuseMindDatasetCreateOrAdoptV2
from common.constants import LLMType
from test.unit_test.api.db.test_musemind_provider_identity import MemoryStore, make_spec

DATASET_ID = "0123456789abcdef0123456789abcdef"
EMBEDDING_ID = "jina-embeddings-v3@musemind@Jina"


def _stub(monkeypatch, name, **attrs):
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        parent_module = sys.modules.get(parent_name)
        if parent_module is not None:
            monkeypatch.setattr(parent_module, child_name, module, raising=False)
    return module


def _request():
    return {
        "dataset_id": DATASET_ID,
        "provider_projection": {
            "language": "Italian",
            "embd_id": EMBEDDING_ID,
            "parser_id": "naive",
            "parser_config": {"chunk_token_num": 512},
            "similarity_threshold": 0.2,
            "vector_similarity_weight": 0.3,
            "pagerank": 0,
            "pipeline_id": None,
        },
    }


def _request_v2():
    request = _request()
    request["provider_projection"].update(
        embd_id="gemini-embedding-2@musemind@Gemini",
        llm_id="gemini-3.1-flash-lite@musemind@Gemini",
        img2txt_id="gemini-3.5-flash@musemind@Gemini",
        parser_config={
            "chunk_token_num": 512,
            "llm_id": "gemini-3.1-flash-lite@musemind@Gemini",
            "img2txt_id": "gemini-3.5-flash@musemind@Gemini",
            "image_context_size": 0,
            "overlapped_percent": 0,
        },
    )
    request["schema"] = "musemind.ragflow-dataset-create-or-adopt/v2"
    return MuseMindDatasetCreateOrAdoptV2(**request).model_dump(by_alias=True)


def _load_service(monkeypatch, *, resolver, save, tenant=None, expected_tenant_id="tenant-1"):
    tenant = tenant or SimpleNamespace(
        embd_id=EMBEDDING_ID, tenant_embd_id=None, llm_id="chat-model"
    )
    saved = {}

    def persist(**payload):
        result = save(payload)
        saved.update(payload)
        return result

    def readback(id, tenant_id):
        if id != DATASET_ID or tenant_id != expected_tenant_id or not saved:
            return None
        return SimpleNamespace(**saved)

    _stub(
        monkeypatch,
        "api.db.joint_services.tenant_model_service",
        get_model_config_from_provider_instance=resolver,
    )
    _stub(
        monkeypatch,
        "api.db.services.document_service",
        DocumentService=SimpleNamespace(),
        queue_raptor_o_graphrag_tasks=MagicMock(),
    )
    _stub(monkeypatch, "api.db.services.file2document_service", File2DocumentService=SimpleNamespace())
    _stub(monkeypatch, "api.db.services.file_service", FileService=SimpleNamespace())
    _stub(
        monkeypatch,
        "api.db.services.knowledgebase_service",
        KnowledgebaseService=SimpleNamespace(save=persist, get_or_none=readback),
    )
    _stub(monkeypatch, "api.db.services.connector_service", Connector2KbService=SimpleNamespace())
    _stub(
        monkeypatch,
        "api.db.services.task_service",
        GRAPH_RAPTOR_FAKE_DOC_ID="fake-doc",
        TaskService=SimpleNamespace(),
    )
    _stub(
        monkeypatch,
        "api.db.services.user_service",
        TenantService=SimpleNamespace(get_by_id=lambda _tenant_id: (True, tenant)),
        UserService=SimpleNamespace(),
        UserTenantService=SimpleNamespace(),
    )
    _stub(monkeypatch, "api.db.db_models", File=SimpleNamespace())
    _stub(
        monkeypatch,
        "api.utils.api_utils",
        deep_merge=MagicMock(),
        get_parser_config=lambda _parser_id, parser_config: dict(parser_config),
        remap_dictionary_keys=MagicMock(),
        verify_embedding_availability=MagicMock(),
    )
    _stub(monkeypatch, "common.settings")
    repo_root = Path(__file__).resolve().parents[5]
    module_path = repo_root / "api" / "apps" / "services" / "dataset_api_service.py"
    spec = importlib.util.spec_from_file_location("test_musemind_dataset_current_registry_module", module_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, saved


def test_current_registry_authorization_creates_with_null_legacy_surrogate(monkeypatch):
    resolver = MagicMock(return_value={"llm_factory": "Jina", "llm_name": "jina-embeddings-v3"})
    module, saved = _load_service(monkeypatch, resolver=resolver, save=lambda _payload: 1)

    success, result = module.create_or_adopt_musemind_dataset("tenant-1", _request())

    assert success is True
    assert result["outcome"] == "CREATED"
    assert saved["tenant_embd_id"] is None
    resolver.assert_called_once_with("tenant-1", LLMType.EMBEDDING, EMBEDDING_ID)


def test_missing_current_registry_authorization_rejects_before_insert(monkeypatch):
    resolver = MagicMock(side_effect=LookupError("not authorized"))
    save = MagicMock()
    module, _saved = _load_service(monkeypatch, resolver=resolver, save=save)

    assert module.create_or_adopt_musemind_dataset("tenant-1", _request()) == (
        False,
        "PROVIDER_DATASET_CONFIG_INVALID",
    )
    save.assert_not_called()


def test_current_registry_retry_adopts_null_legacy_surrogate(monkeypatch):
    resolver = MagicMock(return_value={"llm_factory": "Jina", "llm_name": "jina-embeddings-v3"})
    calls = 0

    def insert_once(payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise IntegrityError(1062, "Duplicate entry for key 'PRIMARY'")
        return 1

    module, _saved = _load_service(monkeypatch, resolver=resolver, save=insert_once)

    assert module.create_or_adopt_musemind_dataset("tenant-1", _request())[1]["outcome"] == "CREATED"
    assert module.create_or_adopt_musemind_dataset("tenant-1", _request())[1]["outcome"] == "ADOPTED"


def test_reconciled_one_shot_state_allows_exact_route_with_null_legacy_field(monkeypatch):
    store = MemoryStore()
    spec = make_spec()
    assert reconcile_provider_identity(store, spec).outcome == "CREATED"
    reconciled_tenant = store.tenants[spec.principal_id]
    tenant = SimpleNamespace(
        embd_id=reconciled_tenant.embd_id,
        tenant_embd_id=None,
        llm_id="chat-model",
    )

    def resolve(tenant_id, model_type, model_name):
        authorization = store.embedding_authorizations[tenant_id]
        assert model_type == LLMType.EMBEDDING
        assert model_name == EMBEDDING_ID
        return {
            "llm_factory": authorization.llm_factory,
            "llm_name": authorization.llm_name,
        }

    module, saved = _load_service(
        monkeypatch,
        resolver=resolve,
        save=lambda _payload: 1,
        tenant=tenant,
        expected_tenant_id=spec.principal_id,
    )

    success, result = module.create_or_adopt_musemind_dataset(spec.principal_id, _request())

    assert success is True
    assert result["outcome"] == "CREATED"
    assert saved["tenant_embd_id"] is None


def test_generation_v2_validates_three_registry_rows_and_exact_readback(monkeypatch):
    tenant = SimpleNamespace(
        embd_id="gemini-embedding-2@musemind@Gemini",
        tenant_embd_id=None,
        llm_id="gemini-3.1-flash-lite@musemind@Gemini",
        img2txt_id="gemini-3.5-flash@musemind@Gemini",
    )
    resolver = MagicMock(return_value={"llm_factory": "Gemini"})
    module, saved = _load_service(monkeypatch, resolver=resolver, save=lambda _payload: 1, tenant=tenant)

    success, result = module.create_or_adopt_musemind_dataset("tenant-1", _request_v2())

    assert success is True
    assert result["provider_projection"]["schema"] == "musemind.ragflow-dataset-provider-projection/v2"
    assert result["provider_projection"]["llm_id"] == tenant.llm_id
    assert result["provider_projection"]["img2txt_id"] == tenant.img2txt_id
    assert saved["parser_config"]["table_context_size"] == 0
    assert saved["parser_config"]["children_delimiter"] == ""
    assert [call.args[1] for call in resolver.call_args_list] == [LLMType.EMBEDDING, LLMType.CHAT, LLMType.IMAGE2TEXT]

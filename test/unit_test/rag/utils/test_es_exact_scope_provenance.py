"""Exercise the actual exact retrieval route's pair guards after candidate expansion."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def route(monkeypatch):
    # Reuse the existing isolated route loader; monkeypatch restores its modules.
    path = ROOT / "test/testcases/test_http_api/test_file_management_within_dataset/test_doc_sdk_routes_unit.py"
    spec = importlib.util.spec_from_file_location("mmd541_route_test_helpers", path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    module = helpers._load_doc_module(monkeypatch)
    pairs = [("dataset-a", "doc-a"), ("dataset-b", "doc-b")]
    request = {"exact_mode": True, "document_scope": [{"dataset_id": dataset, "document_id": document} for dataset, document in pairs], "question": "synthetic multilingual question"}
    calls = []
    result = {"chunks": [], "total": 0}

    class Retriever:
        async def retrieval(self, *args, **kwargs):
            calls.append((args, kwargs))
            return result

        def retrieval_by_children(self, *_args, **_kwargs):
            raise AssertionError("Exact mode must not expand parent/child chunks")

    monkeypatch.setattr(module.settings, "retriever", Retriever())
    monkeypatch.setattr(module, "get_request_json", lambda: helpers._AwaitableValue(request))
    monkeypatch.setattr(module.KnowledgebaseService, "accessible", lambda kb_id, user_id: kb_id in {pair[0] for pair in pairs})
    monkeypatch.setattr(module.KnowledgebaseService, "list_documents_by_ids", lambda ids: [doc for dataset, doc in pairs if dataset in ids])
    kb = SimpleNamespace(embd_id="embedding", tenant_id="tenant-a")
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_ids", lambda ids: [kb for _ in ids])
    monkeypatch.setattr(module.KnowledgebaseService, "get_by_id", lambda _id: (True, kb))
    monkeypatch.setattr(module, "split_model_name", lambda _name: ("embedding", "factory"))
    monkeypatch.setattr(module, "get_model_config_from_provider_instance", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(module, "LLMBundle", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(module, "label_question", lambda *_args: {})
    return SimpleNamespace(request=request, result=result, calls=calls, run=lambda: asyncio.run(helpers._route_core(module.retrieval_test)("tenant-a")))


def chunk(dataset, document):
    return {"chunk_id": "synthetic-chunk", "kb_id": dataset, "doc_id": document}


def test_exact_pair_scope_passes_to_retriever_and_preserves_output_provenance(route):
    route.result.update(chunks=[chunk("dataset-a", "doc-a"), chunk("dataset-b", "doc-b")], total=2)
    result = route.run()
    assert result["code"] == 0
    assert {(item["dataset_id"], item["document_id"]) for item in result["data"]["chunks"]} == {("dataset-a", "doc-a"), ("dataset-b", "doc-b")}
    assert len(route.calls) == 1
    args, _ = route.calls[0]
    assert args[3] == ["dataset-a", "dataset-b"]
    assert args[9] == ["doc-a", "doc-b"]


@pytest.mark.parametrize(
    "dataset,document",
    [
        ("dataset-a", "doc-b"),
        ("dataset-b", "doc-a"),
        ("foreign-museum", "doc-a"),
        ("dataset-a", "unselected-document"),
        (None, "doc-a"),
        ("dataset-a", None),
    ],
)
def test_one_invalid_pair_rejects_the_whole_result_including_cartesian_cross_pairs(route, dataset, document):
    route.result.update(chunks=[chunk("dataset-a", "doc-a"), chunk(dataset, document)], total=2)
    result = route.run()
    assert result["code"] != 0
    assert "invalid or missing provenance" in result["message"]
    assert not result.get("data")
    assert len(route.calls) == 1


@pytest.mark.parametrize(
    "scope",
    [
        [],
        [{"dataset_id": "dataset-a", "document_id": "doc-b"}],
        [{"dataset_id": "foreign-museum", "document_id": "doc-a"}],
    ],
)
def test_empty_wrong_pair_or_foreign_scope_performs_zero_retrievals(route, scope):
    route.request["document_scope"] = scope
    assert route.run()["code"] != 0
    assert route.calls == []


@pytest.mark.parametrize("feature", ["toc_enhance", "use_kg"])
def test_exact_scope_expansion_is_rejected_before_retrieval(route, feature):
    route.request[feature] = True
    assert route.run()["code"] != 0
    assert route.calls == []

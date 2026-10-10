"""Actual ES search method + locked DSL, without storage startup or network.

The small predicate evaluator below exercises only the synthetic query-string
and scope clauses used here; it is not an Elasticsearch ranking simulator.
Live candidate/ranking qualification still belongs to the exact provider bundle.
"""

import ast
import copy
import json
import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from elastic_transport import ConnectionTimeout
from elasticsearch_dsl import Q, Search

from common.constants import PAGERANK_FLD, TAG_FLD
from common.doc_store.doc_store_base import FusionExpr, MatchDenseExpr, MatchExpr, MatchTextExpr, OrderByExpr
from common.float_utils import get_float

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def connection():
    """Compile unchanged source method bodies, bypassing the network constructor."""
    path = ROOT / "rag/utils/es_conn.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ESConnection")
    names = {"search", "_es_search_once", "_search_with_search_after"}
    methods = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name in names]
    constants = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in {"ATTEMPT_TIME", "MAX_RESULT_WINDOW", "SEARCH_AFTER_BATCH_SIZE"} for target in node.targets)
    ]
    scope = {
        "copy": copy,
        "json": json,
        "Q": Q,
        "Search": Search,
        "ConnectionTimeout": ConnectionTimeout,
        "MatchExpr": MatchExpr,
        "MatchTextExpr": MatchTextExpr,
        "MatchDenseExpr": MatchDenseExpr,
        "FusionExpr": FusionExpr,
        "OrderByExpr": OrderByExpr,
        "get_float": get_float,
        "PAGERANK_FLD": PAGERANK_FLD,
        "TAG_FLD": TAG_FLD,
    }
    cls = ast.ClassDef(name="ESConnection", bases=[], keywords=[], body=methods, decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=constants + [cls], type_ignores=[])), str(path), "exec"), scope)  # noqa: S102 -- repository source only
    conn = scope["ESConnection"]()
    conn.logger = MagicMock()
    conn.es = MagicMock()
    conn.es.search.return_value = {"hits": {"total": {"value": 0}, "hits": []}, "timed_out": False}
    return conn


def text_expr():
    return MatchTextExpr(["content_ltks"], "alpha beta gamma delta", 8, {"minimum_should_match": 0.75})


def dense_expr():
    return MatchDenseExpr("q_2_vec", [1.0, 0.0], "float", "cosine", 8, {"similarity": 0.6})


def search_body(connection, expressions, *, condition=None, rank_feature=None):
    connection.search(
        ["content_ltks", "q_2_vec"],
        ["content_ltks"],
        copy.deepcopy(condition if condition is not None else {"doc_id": ["doc-a", "doc-b"], "available_int": 1}),
        expressions,
        None,
        0,
        8,
        ["tenant-index"],
        ["dataset-a", "dataset-b"],
        rank_feature=rank_feature,
    )
    connection.es.search.assert_called_once()
    call = connection.es.search.call_args
    assert call.kwargs["index"] == ["tenant-index"]
    assert call.kwargs["track_total_hits"] is True
    return call.kwargs["body"]


def matches(clause, document):
    """Evaluate this fixture's filter subset; unknown DSL fails the test."""
    if "bool" in clause:
        value = clause["bool"]
        for key in ("must", "filter", "must_not", "should"):
            assert isinstance(value.get(key, []), list)
        required = value.get("must", []) + value.get("filter", [])
        minimum = value.get("minimum_should_match", 0 if required else int(bool(value.get("should"))))
        return (
            all(matches(item, document) for item in required)
            and not any(matches(item, document) for item in value.get("must_not", []))
            and sum(matches(item, document) for item in value.get("should", [])) >= minimum
        )
    if "terms" in clause:
        return all(document.get(field) in allowed for field, allowed in clause["terms"].items())
    if "term" in clause:
        return all(document.get(field) == expected for field, expected in clause["term"].items())
    if "range" in clause:
        return all(document.get(field) is not None and document[field] < bounds["lt"] for field, bounds in clause["range"].items())
    if "exists" in clause:
        return document.get(clause["exists"]["field"]) is not None
    if "query_string" in clause:
        value = clause["query_string"]
        terms = set(value["query"].split())
        present = set(document.get("content_ltks", "").split())
        minimum = math.floor(len(terms) * int(value["minimum_should_match"].rstrip("%")) / 100)
        return len(terms & present) >= minimum
    raise AssertionError(f"Unexpected fixture clause: {tuple(clause)}")


def fixture_document(**overrides):
    return {"kb_id": "dataset-a", "doc_id": "doc-a", "available_int": 1, "content_ltks": "unrelated vocabulary", "id": "chunk-a", **overrides}


@pytest.mark.parametrize("lexical_text", ["unrelated vocabulary", "alpha"])
def test_semantic_candidates_survive_absent_or_weak_lexical_match(connection, lexical_text):
    body = search_body(connection, [text_expr(), dense_expr(), FusionExpr("weighted_sum", 8, {"weights": "0.05,0.95"})])
    semantic = fixture_document(content_ltks=lexical_text)
    assert not matches(body["query"], semantic)
    assert matches(body["knn"]["filter"], semantic)
    # The independent text leg still admits a lexical candidate.
    assert matches(body["query"], fixture_document(content_ltks="alpha beta gamma"))
    assert body["query"]["bool"]["boost"] == pytest.approx(0.05)
    assert body["knn"]["query_vector"] == [1.0, 0.0]
    assert (body["knn"]["k"], body["knn"]["num_candidates"], body["knn"]["similarity"]) == (8, 16, 0.6)
    assert body["fields"] == ["q_2_vec"]
    assert body["size"] == 8


@pytest.mark.parametrize(
    "overrides",
    [
        {"kb_id": "other-museum"},
        {"doc_id": "unselected-document"},
        {"available_int": 0},
        {"available_int": -1},
        {"deleted_at": 123},
        {"language_kwd": "unselected-language"},
    ],
)
def test_scope_and_eligibility_exclude_both_retrieval_legs(connection, overrides):
    body = search_body(
        connection,
        [text_expr(), dense_expr()],
        condition={
            "doc_id": ["doc-a", "doc-b"],
            "available_int": 1,
            "must_not": {"exists": "deleted_at"},
            "language_kwd": "selected-language",
        },
    )
    allowed = fixture_document(content_ltks="alpha beta gamma delta", language_kwd="selected-language")
    assert matches(body["query"], allowed)
    assert matches(body["knn"]["filter"], allowed)
    denied = {**allowed, **overrides}
    assert not matches(body["query"], denied)
    assert not matches(body["knn"]["filter"], denied)


@pytest.mark.parametrize("id_condition", ["chunk-a", ["chunk-a"]])
@pytest.mark.parametrize("stored_id", ["id", "_id"])
def test_second_pass_chunk_allowlist_keeps_id_and_legacy_id_support(connection, id_condition, stored_id):
    body = search_body(connection, [dense_expr()], condition={"id": id_condition})
    assert matches(body["knn"]["filter"], {"kb_id": "dataset-a", stored_id: "chunk-a"})
    assert not matches(body["knn"]["filter"], {"kb_id": "dataset-a", stored_id: "other-chunk"})
    assert not matches(body["knn"]["filter"], {"kb_id": "other-museum", stored_id: "chunk-a"})


@pytest.mark.parametrize(
    "expressions",
    [
        lambda: [text_expr(), dense_expr()],
        lambda: [dense_expr(), text_expr()],
        lambda: [text_expr(), dense_expr(), text_expr()],
    ],
)
def test_knn_filter_does_not_depend_on_expression_order_or_rank_features(connection, expressions):
    body = search_body(connection, expressions(), rank_feature={PAGERANK_FLD: 2.0, "synthetic-tag": 0.5})
    assert matches(body["knn"]["filter"], fixture_document())
    assert "must" not in body["knn"]["filter"]["bool"]
    assert "should" not in body["knn"]["filter"]["bool"]
    assert len(body["query"]["bool"]["should"]) == 2


@pytest.mark.parametrize("available,expected", [(0, {0, -1}), (1, {1, None})])
def test_available_int_semantics_are_preserved(connection, available, expected):
    body = search_body(connection, [dense_expr()], condition={"available_int": available})
    actual = {value for value in (0, -1, 1, None) if matches(body["knn"]["filter"], fixture_document(available_int=value))}
    assert actual == expected


@pytest.mark.parametrize("expressions", [[], [text_expr()]])
def test_non_vector_search_keeps_its_query_and_has_no_knn(connection, expressions):
    body = search_body(connection, expressions)
    assert "knn" not in body
    assert matches(body["query"], fixture_document(content_ltks="alpha beta gamma delta"))
    assert not matches(body["query"], fixture_document(kb_id="other-museum", content_ltks="alpha beta gamma delta"))

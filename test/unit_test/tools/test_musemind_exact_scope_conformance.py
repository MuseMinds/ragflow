from copy import deepcopy

import pytest

from tools.musemind_conformance.exact_scope import ConformanceRunner, ManifestError, load_config


def _manifest(counter=True):
    dataset_ids = {
        "alpha": ("1" * 32, "2" * 32),
        "beta": ("3" * 32, "4" * 32),
    }
    document_ids = {
        "alpha": ("5" * 32, "6" * 32, "7" * 32, "8" * 32),
        "beta": ("9" * 32, "a" * 32, "b" * 32, "c" * 32),
    }

    def documents(museum):
        return [
            {
                "label": "A",
                "dataset_id": dataset_ids[museum][0],
                "document_id": document_ids[museum][0],
                "mime_type": "application/pdf",
                "marker": f"MM-C02-{museum.upper()}-A",
            },
            {
                "label": "B",
                "dataset_id": dataset_ids[museum][0],
                "document_id": document_ids[museum][1],
                "mime_type": "text/plain",
                "marker": f"MM-C02-{museum.upper()}-B",
            },
            {
                "label": "C",
                "dataset_id": dataset_ids[museum][1],
                "document_id": document_ids[museum][2],
                "mime_type": "text/markdown",
                "marker": f"MM-C02-{museum.upper()}-C",
            },
            {
                "label": "D",
                "dataset_id": dataset_ids[museum][1],
                "document_id": document_ids[museum][3],
                "mime_type": "text/plain",
                "marker": f"MM-C02-{museum.upper()}-D",
            },
        ]

    return {
        "schema": "musemind.ragflow-c02-c03/v1",
        "base_url": "http://127.0.0.1:9380",
        "bundle": {
            "fork_commit": "a" * 40,
            "image_digest": f"sha256:{'b' * 64}",
            "sdk_sha256": "c" * 64,
            "bundle_descriptor_sha256": "d" * 64,
        },
        "museums": [
            {"name": "museum-alpha", "token_env": "ALPHA_TOKEN", "documents": documents("alpha")},
            {"name": "museum-beta", "token_env": "BETA_TOKEN", "documents": documents("beta")},
        ],
        "timeouts": {"connect_seconds": 5, "read_seconds": 60},
        "provider_call_counter": (
            {"url": "http://127.0.0.1:9390/counter", "token_env": None, "json_field": "calls"}
            if counter
            else None
        ),
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["bundle"].update(image_digest="musemind/ragflow:latest"),
        lambda manifest: manifest["bundle"].update(fork_commit="0" * 40),
        lambda manifest: manifest["museums"][0]["documents"].pop(),
        lambda manifest: manifest["museums"][0]["documents"][0].update(mime_type="application/docx"),
        lambda manifest: manifest["museums"][0]["documents"][0].update(marker="real museum content"),
        lambda manifest: manifest["museums"][1].update(token_env="ALPHA_TOKEN"),
        lambda manifest: manifest["museums"][1]["documents"][0].update(
            dataset_id="1" * 32, document_id="5" * 32
        ),
    ],
)
def test_manifest_rejects_mutable_or_incoherent_fixture(mutate):
    manifest = _manifest()
    mutate(manifest)

    with pytest.raises(ManifestError):
        load_config(manifest)


class _FakeProvider:
    def __init__(self, manifest, provenance_failure=None, feature_on=None):
        self.calls = 0
        self.provenance_failure = provenance_failure
        self.feature_on = feature_on
        self.by_museum = {
            museum["name"]: {
                document["marker"]: (document["dataset_id"], document["document_id"])
                for document in museum["documents"]
            }
            for museum in manifest["museums"]
        }
        self.owned_pairs = {
            museum["name"]: {
                (document["dataset_id"], document["document_id"])
                for document in museum["documents"]
            }
            for museum in manifest["museums"]
        }

    def post(self, museum_name, _token_env, payload):
        scope = payload.get("document_scope")
        if payload.get("exact_mode") is not True or not isinstance(scope, list) or not scope:
            return 200, {"code": 102, "message": "rejected"}
        if payload.get("toc_enhance") or payload.get("use_kg"):
            return 200, {"code": 102, "message": "rejected"}
        pairs = {(item["dataset_id"], item["document_id"]) for item in scope}
        if not pairs.issubset(self.owned_pairs[museum_name]):
            return 200, {"code": 102, "message": "rejected"}

        self.calls += 1
        target = self.by_museum[museum_name][payload["question"]]
        if target not in pairs:
            return 200, {"code": 0, "data": {"chunks": []}}
        dataset_id, document_id = target
        if self.provenance_failure == "rogue":
            document_id = "rogue-document"
        chunk = {
            "id": "synthetic-chunk",
            "dataset_id": dataset_id,
            "document_id": document_id,
            "content": "must not enter evidence",
        }
        if self.provenance_failure == "missing":
            chunk.pop("document_id")
        return 200, {
            "code": 0,
            "data": {
                "chunks": [chunk]
            },
        }

    def get(self, museum_name, _token_env, path):
        dataset_id = path.rsplit("/", 1)[-1]
        owned_dataset_ids = {pair[0] for pair in self.owned_pairs[museum_name]}
        if dataset_id not in owned_dataset_ids:
            return 200, {"code": 102, "message": "rejected"}
        parser_config = {
            "raptor": {"use_raptor": False},
            "graphrag": {"use_graphrag": False},
            "parent_child": {"use_parent_child": False},
        }
        if self.feature_on:
            flag = "use_graphrag" if self.feature_on == "graphrag" else f"use_{self.feature_on}"
            parser_config[self.feature_on][flag] = True
        return 200, {
            "code": 0,
            "data": {
                "id": dataset_id,
                "chunk_method": "naive",
                "parser_config": parser_config,
            },
        }


def test_matrix_passes_with_counter_and_sanitizes_output():
    manifest = _manifest()
    provider = _FakeProvider(manifest)
    result = ConformanceRunner(
        load_config(manifest), provider.post, provider.get, lambda: provider.calls
    ).run()

    assert result["status"] == "PASSED"
    assert result["case_count"] == 28
    assert result["failed_case_count"] == 0
    rendered = str(result)
    assert "ALPHA_TOKEN" not in rendered
    assert "MM-C02-ALPHA-A" not in rendered
    assert "must not enter evidence" not in rendered
    rejection_cases = [case for case in result["cases"] if "counter_proof" in case]
    assert all(case.get("provider_counter_delta") == 0 for case in rejection_cases)


def test_missing_counter_keeps_gate_incomplete():
    manifest = _manifest(counter=False)
    provider = _FakeProvider(manifest)

    result = ConformanceRunner(load_config(manifest), provider.post, provider.get, None).run()

    assert result["status"] == "INCOMPLETE"
    assert result["failed_case_count"] == 0
    assert result["provider_counter_proof"] == "UNAVAILABLE"


@pytest.mark.parametrize("feature_on", ["raptor", "graphrag", "parent_child"])
def test_enabled_derived_feature_fails_config_readback(feature_on):
    manifest = _manifest()
    provider = _FakeProvider(manifest, feature_on=feature_on)

    result = ConformanceRunner(
        load_config(manifest), provider.post, provider.get, lambda: provider.calls
    ).run()

    assert result["status"] == "FAILED"
    failed_config = [
        case
        for case in result["cases"]
        if case["status"] == "FAILED" and "dataset_config_readback" in case["name"]
    ]
    assert len(failed_config) == 4


@pytest.mark.parametrize("provenance_failure", ["rogue", "missing"])
def test_invalid_provenance_invalidates_result(provenance_failure):
    manifest = deepcopy(_manifest())
    provider = _FakeProvider(manifest, provenance_failure=provenance_failure)

    result = ConformanceRunner(
        load_config(manifest), provider.post, provider.get, lambda: provider.calls
    ).run()

    assert result["status"] == "FAILED"
    assert result["failed_case_count"] == 12
    failed_exact = [case for case in result["cases"] if case["status"] == "FAILED"]
    assert all(
        case["all_pairs_allowed"] is False or case["wire_shape_valid"] is False
        for case in failed_exact
    )

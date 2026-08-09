"""C-02/C-03 exact-scope conformance runner.

The runner consumes only synthetic fixture metadata. API tokens are resolved from
environment variable names declared in the manifest and are never copied into the
result. A provider-call counter is required before the result can be called PASSED;
without one the wire checks can pass, but the gate remains INCOMPLETE.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


SCHEMA = "musemind.ragflow-c02-c03/v1"
ALLOWED_MIME_TYPES = {"application/pdf", "text/markdown", "text/plain"}
EXPECTED_LABELS = {"A", "B", "C", "D"}
HEX_64 = re.compile(r"[0-9a-f]{64}")
HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_32 = re.compile(r"[0-9a-f]{32}")
SYNTHETIC_MARKER = re.compile(r"MM-C02-[A-Z0-9-]{1,64}")
PostJson = Callable[[str, str, dict[str, Any]], tuple[int, dict[str, Any]]]
GetJson = Callable[[str, str, str], tuple[int, dict[str, Any]]]
ReadCounter = Callable[[], int]


class ManifestError(ValueError):
    """Raised when a conformance manifest is incomplete or mutable."""


@dataclass(frozen=True)
class DocumentRef:
    label: str
    dataset_id: str
    document_id: str
    mime_type: str
    marker: str

    @property
    def pair(self) -> tuple[str, str]:
        return self.dataset_id, self.document_id

    @property
    def wire_pair(self) -> dict[str, str]:
        return {"dataset_id": self.dataset_id, "document_id": self.document_id}


@dataclass(frozen=True)
class MuseumFixture:
    name: str
    token_env: str
    documents: dict[str, DocumentRef]


@dataclass(frozen=True)
class BundleIdentity:
    fork_commit: str
    image_digest: str
    sdk_sha256: str
    bundle_descriptor_sha256: str


@dataclass(frozen=True)
class CounterConfig:
    url: str
    token_env: str | None
    json_field: str


@dataclass(frozen=True)
class HarnessConfig:
    base_url: str
    bundle: BundleIdentity
    museums: tuple[MuseumFixture, MuseumFixture]
    counter: CounterConfig | None
    connect_timeout_seconds: float
    read_timeout_seconds: float


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{path} must be a non-empty string")
    return value.strip()


def _require_exact_hex(value: Any, path: str, pattern: re.Pattern[str]) -> str:
    text = _require_string(value, path)
    if pattern.fullmatch(text) is None:
        raise ManifestError(f"{path} must be lowercase hexadecimal with the required length")
    if set(text) == {"0"}:
        raise ManifestError(f"{path} must not use the all-zero placeholder")
    return text


def _validate_url(value: Any, path: str) -> str:
    url = _require_string(value, path).rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ManifestError(f"{path} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ManifestError(f"{path} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ManifestError(f"{path} must not contain a query or fragment")
    return url


def _load_document(raw: Any, path: str) -> DocumentRef:
    if not isinstance(raw, dict):
        raise ManifestError(f"{path} must be an object")
    expected = {"label", "dataset_id", "document_id", "mime_type", "marker"}
    if set(raw) != expected:
        raise ManifestError(f"{path} must contain exactly {sorted(expected)}")
    label = _require_string(raw["label"], f"{path}.label")
    if label not in EXPECTED_LABELS:
        raise ManifestError(f"{path}.label must be one of A/B/C/D")
    mime_type = _require_string(raw["mime_type"], f"{path}.mime_type")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ManifestError(f"{path}.mime_type is outside the ADR-0029 allowlist")
    marker = _require_string(raw["marker"], f"{path}.marker")
    if SYNTHETIC_MARKER.fullmatch(marker) is None:
        raise ManifestError(f"{path}.marker must use the synthetic MM-C02-* format")
    return DocumentRef(
        label=label,
        dataset_id=_require_exact_hex(raw["dataset_id"], f"{path}.dataset_id", HEX_32),
        document_id=_require_exact_hex(raw["document_id"], f"{path}.document_id", HEX_32),
        mime_type=mime_type,
        marker=marker,
    )


def _load_museum(raw: Any, path: str) -> MuseumFixture:
    if not isinstance(raw, dict) or set(raw) != {"name", "token_env", "documents"}:
        raise ManifestError(f"{path} must contain exactly name, token_env and documents")
    documents_raw = raw["documents"]
    if not isinstance(documents_raw, list):
        raise ManifestError(f"{path}.documents must be a list")
    documents = [_load_document(item, f"{path}.documents[{index}]") for index, item in enumerate(documents_raw)]
    by_label = {document.label: document for document in documents}
    if len(by_label) != len(documents) or set(by_label) != EXPECTED_LABELS:
        raise ManifestError(f"{path}.documents must contain each synthetic label A/B/C/D exactly once")
    if len({document.dataset_id for document in documents}) < 2:
        raise ManifestError(f"{path}.documents must span at least two stable datasets")
    if {document.mime_type for document in documents} != ALLOWED_MIME_TYPES:
        raise ManifestError(f"{path}.documents must cover PDF, plain text and Markdown")
    if len({document.document_id for document in documents}) != len(documents):
        raise ManifestError(f"{path}.document_id values must be distinct")
    if len({document.marker for document in documents}) != len(documents):
        raise ManifestError(f"{path}.marker values must be distinct")
    return MuseumFixture(
        name=_require_string(raw["name"], f"{path}.name"),
        token_env=_require_string(raw["token_env"], f"{path}.token_env"),
        documents=by_label,
    )


def load_config(raw: Any) -> HarnessConfig:
    if not isinstance(raw, dict):
        raise ManifestError("manifest must be a JSON object")
    expected = {"schema", "base_url", "bundle", "museums", "timeouts", "provider_call_counter"}
    if set(raw) != expected:
        raise ManifestError(f"manifest must contain exactly {sorted(expected)}")
    if raw["schema"] != SCHEMA:
        raise ManifestError(f"schema must be {SCHEMA}")

    bundle_raw = raw["bundle"]
    if not isinstance(bundle_raw, dict):
        raise ManifestError("bundle must be an object")
    bundle_fields = {"fork_commit", "image_digest", "sdk_sha256", "bundle_descriptor_sha256"}
    if set(bundle_raw) != bundle_fields:
        raise ManifestError(f"bundle must contain exactly {sorted(bundle_fields)}")
    image_digest = _require_string(bundle_raw["image_digest"], "bundle.image_digest")
    image_hash = image_digest.removeprefix("sha256:")
    if not image_digest.startswith("sha256:") or HEX_64.fullmatch(image_hash) is None or set(image_hash) == {"0"}:
        raise ManifestError("bundle.image_digest must be an immutable sha256 OCI digest")
    bundle = BundleIdentity(
        fork_commit=_require_exact_hex(bundle_raw["fork_commit"], "bundle.fork_commit", HEX_40),
        image_digest=image_digest,
        sdk_sha256=_require_exact_hex(bundle_raw["sdk_sha256"], "bundle.sdk_sha256", HEX_64),
        bundle_descriptor_sha256=_require_exact_hex(
            bundle_raw["bundle_descriptor_sha256"], "bundle.bundle_descriptor_sha256", HEX_64
        ),
    )

    museums_raw = raw["museums"]
    if not isinstance(museums_raw, list) or len(museums_raw) != 2:
        raise ManifestError("museums must contain exactly two museum fixtures")
    museums = tuple(_load_museum(item, f"museums[{index}]") for index, item in enumerate(museums_raw))
    if museums[0].name == museums[1].name:
        raise ManifestError("museum names must be distinct")
    if museums[0].token_env == museums[1].token_env:
        raise ManifestError("each museum must use a distinct token environment variable")
    museum_one_pairs = {document.pair for document in museums[0].documents.values()}
    museum_two_pairs = {document.pair for document in museums[1].documents.values()}
    if museum_one_pairs & museum_two_pairs:
        raise ManifestError("provider dataset/document pairs must not be shared across museums")

    timeouts = raw["timeouts"]
    if not isinstance(timeouts, dict) or set(timeouts) != {"connect_seconds", "read_seconds"}:
        raise ManifestError("timeouts must contain exactly connect_seconds and read_seconds")
    connect_timeout = timeouts["connect_seconds"]
    read_timeout = timeouts["read_seconds"]
    if not isinstance(connect_timeout, (int, float)) or connect_timeout <= 0:
        raise ManifestError("timeouts.connect_seconds must be positive")
    if not isinstance(read_timeout, (int, float)) or read_timeout <= 0:
        raise ManifestError("timeouts.read_seconds must be positive")

    counter_raw = raw["provider_call_counter"]
    counter = None
    if counter_raw is not None:
        if not isinstance(counter_raw, dict) or set(counter_raw) != {"url", "token_env", "json_field"}:
            raise ManifestError("provider_call_counter must be null or contain exactly url, token_env and json_field")
        token_env = counter_raw["token_env"]
        if token_env is not None:
            token_env = _require_string(token_env, "provider_call_counter.token_env")
        counter = CounterConfig(
            url=_validate_url(counter_raw["url"], "provider_call_counter.url"),
            token_env=token_env,
            json_field=_require_string(counter_raw["json_field"], "provider_call_counter.json_field"),
        )

    return HarnessConfig(
        base_url=_validate_url(raw["base_url"], "base_url"),
        bundle=bundle,
        museums=museums,  # type: ignore[arg-type]
        counter=counter,
        connect_timeout_seconds=float(connect_timeout),
        read_timeout_seconds=float(read_timeout),
    )


def _case(name: str, passed: bool, **details: Any) -> dict[str, Any]:
    return {"name": name, "status": "PASSED" if passed else "FAILED", **details}


class ConformanceRunner:
    def __init__(
        self,
        config: HarnessConfig,
        post_json: PostJson,
        get_json: GetJson,
        read_counter: ReadCounter | None,
    ):
        self.config = config
        self.post_json = post_json
        self.get_json = get_json
        self.read_counter = read_counter

    def _request(self, museum: MuseumFixture, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self.post_json(museum.name, museum.token_env, payload)

    def _dataset_config_case(self, museum: MuseumFixture, dataset_id: str) -> dict[str, Any]:
        http_status, response = self.get_json(
            museum.name,
            museum.token_env,
            f"/api/v1/datasets/{dataset_id}",
        )
        data = response.get("data") or {}
        parser_config = data.get("parser_config") or {}
        feature_flags = {
            "raptor": (parser_config.get("raptor") or {}).get("use_raptor"),
            "graphrag": (parser_config.get("graphrag") or {}).get("use_graphrag"),
            "parent_child": (parser_config.get("parent_child") or {}).get("use_parent_child"),
        }
        passed = (
            response.get("code") == 0
            and data.get("id") == dataset_id
            and data.get("chunk_method") == "naive"
            and all(value is False for value in feature_flags.values())
        )
        return _case(
            f"{museum.name}:dataset_config_readback_{dataset_id[:8]}",
            passed,
            http_status=http_status,
            api_code=response.get("code"),
            exact_dataset_observed=data.get("id") == dataset_id,
            naive_chunk_method=data.get("chunk_method") == "naive",
            raptor_off=feature_flags["raptor"] is False,
            graphrag_off=feature_flags["graphrag"] is False,
            parent_child_off=feature_flags["parent_child"] is False,
        )

    def _rejection_case(self, name: str, museum: MuseumFixture, payload: dict[str, Any]) -> dict[str, Any]:
        before = self.read_counter() if self.read_counter else None
        http_status, response = self._request(museum, payload)
        after = self.read_counter() if self.read_counter else None
        api_code = response.get("code")
        rejected = api_code != 0
        zero_provider_calls = before == after if before is not None and after is not None else None
        passed = rejected and zero_provider_calls is not False
        return _case(
            name,
            passed,
            http_status=http_status,
            api_code=api_code,
            provider_counter_delta=(after - before) if before is not None and after is not None else None,
            counter_proof="AVAILABLE" if zero_provider_calls is not None else "UNAVAILABLE",
        )

    def _exact_case(self, name: str, museum: MuseumFixture, scope_labels: str, target_label: str) -> dict[str, Any]:
        scope = [museum.documents[label] for label in scope_labels]
        target = museum.documents[target_label]
        http_status, response = self._request(
            museum,
            {
                "exact_mode": True,
                "document_scope": [document.wire_pair for document in scope],
                "question": target.marker,
                "page": 1,
                "page_size": 30,
                "similarity_threshold": 0,
                "vector_similarity_weight": 0.3,
                "top_k": 64,
                "toc_enhance": False,
                "use_kg": False,
            },
        )
        chunks = ((response.get("data") or {}).get("chunks") or []) if isinstance(response, dict) else []
        allowed_pairs = {document.pair for document in scope}
        returned_pairs: list[tuple[Any, Any]] = []
        wire_shape_valid = True
        derived_expansion_marker_absent = True
        for chunk in chunks:
            if not isinstance(chunk, dict) or not {"id", "dataset_id", "document_id"}.issubset(chunk):
                wire_shape_valid = False
                continue
            if chunk.get("mom_id") or chunk.get("raptor_kwd"):
                derived_expansion_marker_absent = False
            returned_pairs.append((chunk["dataset_id"], chunk["document_id"]))
        passed = (
            response.get("code") == 0
            and bool(chunks)
            and wire_shape_valid
            and derived_expansion_marker_absent
            and all(pair in allowed_pairs for pair in returned_pairs)
            and target.pair in returned_pairs
        )
        return _case(
            name,
            passed,
            http_status=http_status,
            api_code=response.get("code"),
            chunk_count=len(chunks),
            returned_pair_count=len(set(returned_pairs)),
            target_pair_observed=target.pair in returned_pairs,
            wire_shape_valid=wire_shape_valid,
            derived_expansion_marker_absent=derived_expansion_marker_absent,
            all_pairs_allowed=all(pair in allowed_pairs for pair in returned_pairs),
        )

    def run(self) -> dict[str, Any]:
        museum_one, museum_two = self.config.museums
        cases: list[dict[str, Any]] = []
        for museum in self.config.museums:
            for dataset_id in sorted({document.dataset_id for document in museum.documents.values()}):
                cases.append(self._dataset_config_case(museum, dataset_id))
            cases.extend(
                [
                    self._rejection_case(
                        f"{museum.name}:omitted_scope",
                        museum,
                        {"exact_mode": True, "question": "synthetic"},
                    ),
                    self._rejection_case(
                        f"{museum.name}:empty_scope",
                        museum,
                        {"exact_mode": True, "document_scope": [], "question": "synthetic"},
                    ),
                    self._rejection_case(
                        f"{museum.name}:toc_rejected",
                        museum,
                        {
                            "exact_mode": True,
                            "document_scope": [museum.documents["A"].wire_pair],
                            "question": "synthetic",
                            "toc_enhance": True,
                        },
                    ),
                    self._rejection_case(
                        f"{museum.name}:kg_rejected",
                        museum,
                        {
                            "exact_mode": True,
                            "document_scope": [museum.documents["A"].wire_pair],
                            "question": "synthetic",
                            "use_kg": True,
                        },
                    ),
                ]
            )
            for scope_labels in ("ABC", "ABD"):
                for target_label in scope_labels:
                    cases.append(
                        self._exact_case(
                            f"{museum.name}:scope_{scope_labels.lower()}_target_{target_label.lower()}",
                            museum,
                            scope_labels,
                            target_label,
                        )
                    )

            first_dataset = museum.documents["A"].dataset_id
            other_document = next(
                document
                for document in museum.documents.values()
                if document.dataset_id != first_dataset
            )
            cases.append(
                self._rejection_case(
                    f"{museum.name}:wrong_dataset_document_pair",
                    museum,
                    {
                        "exact_mode": True,
                        "document_scope": [
                            {"dataset_id": first_dataset, "document_id": other_document.document_id}
                        ],
                        "question": "synthetic",
                    },
                )
            )

        cases.extend(
            [
                self._rejection_case(
                    f"{museum_one.name}:foreign_museum_scope",
                    museum_one,
                    {
                        "exact_mode": True,
                        "document_scope": [museum_two.documents["A"].wire_pair],
                        "question": "synthetic",
                    },
                ),
                self._rejection_case(
                    f"{museum_two.name}:foreign_museum_scope",
                    museum_two,
                    {
                        "exact_mode": True,
                        "document_scope": [museum_one.documents["A"].wire_pair],
                        "question": "synthetic",
                    },
                ),
            ]
        )

        failures = [case for case in cases if case["status"] != "PASSED"]
        missing_counter_proof = any(case.get("counter_proof") == "UNAVAILABLE" for case in cases)
        if failures:
            status = "FAILED"
        elif missing_counter_proof:
            status = "INCOMPLETE"
        else:
            status = "PASSED"
        return {
            "schema": SCHEMA,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "bundle": {
                "fork_commit": self.config.bundle.fork_commit,
                "image_digest": self.config.bundle.image_digest,
                "sdk_sha256": self.config.bundle.sdk_sha256,
                "bundle_descriptor_sha256": self.config.bundle.bundle_descriptor_sha256,
            },
            "fixture_summary": {
                "museum_count": len(self.config.museums),
                "documents_per_museum": 4,
                "labels": sorted(EXPECTED_LABELS),
                "mime_types": sorted(ALLOWED_MIME_TYPES),
            },
            "provider_counter_proof": "AVAILABLE" if self.read_counter else "UNAVAILABLE",
            "case_count": len(cases),
            "failed_case_count": len(failures),
            "cases": cases,
        }


def _build_http_transports(
    config: HarnessConfig, environment: Mapping[str, str]
) -> tuple[PostJson, GetJson, ReadCounter | None]:
    tokens: dict[str, str] = {}
    for museum in config.museums:
        token = environment.get(museum.token_env)
        if not token:
            raise ManifestError(f"required token environment variable {museum.token_env} is not set")
        tokens[museum.name] = token
    session = requests.Session()
    timeout = (config.connect_timeout_seconds, config.read_timeout_seconds)

    def post_json(museum_name: str, _token_env: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = session.post(
            f"{config.base_url}/api/v1/retrieval",
            headers={"Authorization": f"Bearer {tokens[museum_name]}"},
            json=payload,
            timeout=timeout,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("retrieval endpoint returned non-JSON data") from exc
        if not isinstance(body, dict):
            raise RuntimeError("retrieval endpoint returned a non-object JSON value")
        return response.status_code, body

    def get_json(museum_name: str, _token_env: str, path: str) -> tuple[int, dict[str, Any]]:
        response = session.get(
            f"{config.base_url}{path}",
            headers={"Authorization": f"Bearer {tokens[museum_name]}"},
            timeout=timeout,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("dataset endpoint returned non-JSON data") from exc
        if not isinstance(body, dict):
            raise RuntimeError("dataset endpoint returned a non-object JSON value")
        return response.status_code, body

    if config.counter is None:
        return post_json, get_json, None

    counter_token = None
    if config.counter.token_env:
        counter_token = environment.get(config.counter.token_env)
        if not counter_token:
            raise ManifestError(
                f"required counter token environment variable {config.counter.token_env} is not set"
            )

    def read_counter() -> int:
        headers = {"Authorization": f"Bearer {counter_token}"} if counter_token else None
        response = session.get(config.counter.url, headers=headers, timeout=timeout)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("provider counter returned a non-object JSON value")
        value = body.get(config.counter.json_field)
        if not isinstance(value, int) or value < 0:
            raise RuntimeError("provider counter field must be a non-negative integer")
        return value

    return post_json, get_json, read_counter


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MuseMind RAGFlow C-02/C-03 live conformance")
    parser.add_argument("--manifest", type=Path, required=True, help="JSON manifest with synthetic fixture IDs")
    parser.add_argument("--output", type=Path, help="Optional content-free JSON result path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        raw = json.loads(args.manifest.read_text(encoding="utf-8"))
        config = load_config(raw)
        post_json, get_json, read_counter = _build_http_transports(config, os.environ)
        result = ConformanceRunner(config, post_json, get_json, read_counter).run()
    except (ManifestError, OSError, json.JSONDecodeError, requests.RequestException, RuntimeError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "ERROR", "error": str(exc)}, sort_keys=True))
        return 2

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

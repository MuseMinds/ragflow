#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
"""Fail-closed live conformance runner for MuseMind C-04 create-or-adopt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import requests


SCHEMA = "musemind.ragflow-c04/v1"
ALLOWED_MIME_TYPES = {"application/pdf", "text/markdown", "text/plain"}
EXPECTED_LABELS = frozenset("ABCD")
HEX_32 = re.compile(r"[0-9a-f]{32}")
HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_64 = re.compile(r"[0-9a-f]{64}")


class ManifestError(ValueError):
    """The qualification manifest is unsafe, mutable, or internally inconsistent."""


@dataclass(frozen=True)
class Bundle:
    fork_commit: str
    image_digest: str
    sdk_sha256: str
    bundle_descriptor_sha256: str


@dataclass(frozen=True)
class DocumentFixture:
    label: str
    dataset_id: str
    document_id: str
    mime_type: str
    source_path: Path
    size_bytes: int
    sha256: str
    collision_source_path: Path | None = None
    collision_size_bytes: int | None = None
    collision_sha256: str | None = None

    @property
    def suffix(self) -> str:
        return {"application/pdf": ".pdf", "text/markdown": ".md", "text/plain": ".txt"}[self.mime_type]


@dataclass(frozen=True)
class MuseumFixture:
    name: str
    token_env: str
    documents: tuple[DocumentFixture, ...]

    def document(self, label: str) -> DocumentFixture:
        return next(document for document in self.documents if document.label == label)


@dataclass(frozen=True)
class HarnessConfig:
    base_url: str
    bundle: Bundle
    museums: tuple[MuseumFixture, ...]
    connect_timeout_seconds: float
    read_timeout_seconds: float
    parse_deadline_seconds: float
    poll_interval_seconds: float


Upload = Callable[[str, DocumentFixture, bytes], tuple[int, dict[str, Any]]]
ListDocument = Callable[[str, DocumentFixture], tuple[int, dict[str, Any]]]
Download = Callable[[str, DocumentFixture], tuple[int, bytes]]
Parse = Callable[[str, str, list[str]], tuple[int, dict[str, Any]]]
ListChunks = Callable[[str, DocumentFixture], tuple[int, dict[str, Any]]]


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string")
    return value.strip()


def _immutable_hex(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    rendered = _required_string(value, field)
    if pattern.fullmatch(rendered) is None or set(rendered) == {"0"}:
        raise ManifestError(f"{field} must identify a non-zero immutable artifact")
    return rendered


def _positive_number(value: Any, field: str, *, allow_zero: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ManifestError(f"{field} must be numeric")
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        raise ManifestError(f"{field} must be {'non-negative' if allow_zero else 'positive'}")
    return number


def _load_document(raw: Any, manifest_dir: Path) -> DocumentFixture:
    if not isinstance(raw, dict):
        raise ManifestError("each document fixture must be an object")
    label = _required_string(raw.get("label"), "document.label")
    if label not in EXPECTED_LABELS:
        raise ManifestError("document.label must be one of A/B/C/D")
    dataset_id = _immutable_hex(raw.get("dataset_id"), "document.dataset_id", HEX_32)
    document_id = _immutable_hex(raw.get("document_id"), "document.document_id", HEX_32)
    mime_type = _required_string(raw.get("mime_type"), "document.mime_type")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ManifestError("document.mime_type is outside the ADR-0029 allowlist")

    def verified_file(path_value: Any, size_value: Any, digest_value: Any, prefix: str):
        path = Path(_required_string(path_value, f"{prefix}.path"))
        if not path.is_absolute():
            path = manifest_dir / path
        path = path.resolve()
        if not path.is_file():
            raise ManifestError(f"{prefix}.path must identify an existing regular file")
        expected_size = int(_positive_number(size_value, f"{prefix}.size_bytes"))
        expected_digest = _immutable_hex(digest_value, f"{prefix}.sha256", HEX_64)
        content = path.read_bytes()
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_digest:
            raise ManifestError(f"{prefix} size/SHA-256 does not match the local fixture")
        return path, expected_size, expected_digest

    source_path, size_bytes, sha256 = verified_file(raw.get("source_path"), raw.get("size_bytes"), raw.get("sha256"), "document.source")
    collision_path = collision_size = collision_sha256 = None
    if label == "C":
        collision_path, collision_size, collision_sha256 = verified_file(
            raw.get("collision_source_path"),
            raw.get("collision_size_bytes"),
            raw.get("collision_sha256"),
            "document.collision_source",
        )
        if sha256 == collision_sha256:
            raise ManifestError("C collision fixture must have a different SHA-256")
    elif any(key.startswith("collision_") for key in raw):
        raise ManifestError("collision fixture fields are allowed only for label C")

    return DocumentFixture(
        label=label,
        dataset_id=dataset_id,
        document_id=document_id,
        mime_type=mime_type,
        source_path=source_path,
        size_bytes=size_bytes,
        sha256=sha256,
        collision_source_path=collision_path,
        collision_size_bytes=collision_size,
        collision_sha256=collision_sha256,
    )


def load_config(raw: Any, manifest_dir: Path = Path(".")) -> HarnessConfig:
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ManifestError(f"manifest schema must be {SCHEMA}")
    base_url = _required_string(raw.get("base_url"), "base_url").rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ManifestError("base_url must use HTTP or HTTPS")

    bundle_raw = raw.get("bundle")
    if not isinstance(bundle_raw, dict):
        raise ManifestError("bundle must be an object")
    image_digest = _required_string(bundle_raw.get("image_digest"), "bundle.image_digest")
    if not image_digest.startswith("sha256:"):
        raise ManifestError("bundle.image_digest must be an OCI sha256 digest")
    image_hash = _immutable_hex(image_digest.removeprefix("sha256:"), "bundle.image_digest", HEX_64)
    bundle = Bundle(
        fork_commit=_immutable_hex(bundle_raw.get("fork_commit"), "bundle.fork_commit", HEX_40),
        image_digest=f"sha256:{image_hash}",
        sdk_sha256=_immutable_hex(bundle_raw.get("sdk_sha256"), "bundle.sdk_sha256", HEX_64),
        bundle_descriptor_sha256=_immutable_hex(bundle_raw.get("bundle_descriptor_sha256"), "bundle.bundle_descriptor_sha256", HEX_64),
    )

    museums_raw = raw.get("museums")
    if not isinstance(museums_raw, list) or len(museums_raw) != 2:
        raise ManifestError("museums must contain exactly two fixtures")
    museums = []
    all_dataset_ids: set[str] = set()
    all_document_ids: set[str] = set()
    token_envs: set[str] = set()
    for museum_raw in museums_raw:
        if not isinstance(museum_raw, dict):
            raise ManifestError("each museum fixture must be an object")
        name = _required_string(museum_raw.get("name"), "museum.name")
        token_env = _required_string(museum_raw.get("token_env"), "museum.token_env")
        if token_env in token_envs:
            raise ManifestError("museum token environment variables must be distinct")
        token_envs.add(token_env)
        documents_raw = museum_raw.get("documents")
        if not isinstance(documents_raw, list) or len(documents_raw) != 4:
            raise ManifestError("each museum must define exactly A/B/C/D")
        documents = tuple(_load_document(item, manifest_dir) for item in documents_raw)
        if {document.label for document in documents} != EXPECTED_LABELS:
            raise ManifestError("each museum must define each A/B/C/D label exactly once")
        if len({document.dataset_id for document in documents}) != 2:
            raise ManifestError("each museum must use exactly two stable datasets")
        dataset_ids = {document.dataset_id for document in documents}
        document_ids = {document.document_id for document in documents}
        if all_dataset_ids.intersection(dataset_ids) or all_document_ids.intersection(document_ids):
            raise ManifestError("dataset and document identities must not cross museum fixtures")
        all_dataset_ids.update(dataset_ids)
        all_document_ids.update(document_ids)
        museums.append(MuseumFixture(name=name, token_env=token_env, documents=documents))
    if len({museum.name for museum in museums}) != 2:
        raise ManifestError("museum names must be distinct")

    timeouts = raw.get("timeouts")
    if not isinstance(timeouts, dict):
        raise ManifestError("timeouts must be an object")
    return HarnessConfig(
        base_url=base_url,
        bundle=bundle,
        museums=tuple(museums),
        connect_timeout_seconds=_positive_number(timeouts.get("connect_seconds"), "timeouts.connect_seconds"),
        read_timeout_seconds=_positive_number(timeouts.get("read_seconds"), "timeouts.read_seconds"),
        parse_deadline_seconds=_positive_number(timeouts.get("parse_deadline_seconds"), "timeouts.parse_deadline_seconds"),
        poll_interval_seconds=_positive_number(timeouts.get("poll_interval_seconds"), "timeouts.poll_interval_seconds", allow_zero=True),
    )


def _case(name: str, passed: bool, **facts: Any) -> dict[str, Any]:
    return {"name": name, "status": "PASSED" if passed else "FAILED", **facts}


class ConformanceRunner:
    def __init__(
        self,
        config: HarnessConfig,
        upload: Upload,
        list_document: ListDocument,
        download: Download,
        parse: Parse,
        list_chunks: ListChunks,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.upload = upload
        self.list_document = list_document
        self.download = download
        self.parse = parse
        self.list_chunks = list_chunks
        self.sleep = sleep

    @staticmethod
    def _api_code(body: dict[str, Any]) -> Any:
        code = body.get("code")
        return code if isinstance(code, (int, str)) else None

    @staticmethod
    def _numeric(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0

    def _exact_readback(self, museum: MuseumFixture, document: DocumentFixture) -> dict[str, Any]:
        http_status, body = self.list_document(museum.name, document)
        data = body.get("data") if isinstance(body, dict) else None
        docs = data.get("docs") if isinstance(data, dict) else None
        docs = docs if isinstance(docs, list) else []
        exact_docs = [item for item in docs if isinstance(item, dict) and item.get("id") == document.document_id]
        exact_pair = len(exact_docs) == 1 and exact_docs[0].get("dataset_id") == document.dataset_id
        download_status, content = self.download(museum.name, document)
        size_matches = len(content) == document.size_bytes
        checksum_matches = hashlib.sha256(content).hexdigest() == document.sha256
        return {
            "passed": (http_status == 200 and self._api_code(body) == 0 and len(docs) == 1 and exact_pair and download_status == 200 and size_matches and checksum_matches),
            "document_count": len(docs),
            "exact_pair": exact_pair,
            "download_size_matches": size_matches,
            "download_checksum_matches": checksum_matches,
        }

    def _preflight(self) -> tuple[list[dict[str, Any]], bool]:
        cases = []
        clean = True
        for museum in self.config.museums:
            for document in museum.documents:
                http_status, body = self.list_document(museum.name, document)
                data = body.get("data") if isinstance(body, dict) else None
                docs = data.get("docs") if isinstance(data, dict) else None
                docs = docs if isinstance(docs, list) else []
                passed = http_status == 200 and self._api_code(body) == 0 and not docs
                clean = clean and passed
                cases.append(_case(f"{museum.name}:{document.label}:clean_preflight", passed, document_count=len(docs)))
        return cases, clean

    def _response_loss(self, museum: MuseumFixture) -> dict[str, Any]:
        document = museum.document("A")
        content = document.source_path.read_bytes()
        self.upload(museum.name, document, content)  # Deliberately discard the completed response.
        retry_status, retry_body = self.upload(museum.name, document, content)
        readback = self._exact_readback(museum, document)
        retry_conflict = retry_status == 200 and self._api_code(retry_body) not in (None, 0, "0")
        return _case(
            f"{museum.name}:A:response_loss_adoption",
            retry_conflict and readback["passed"],
            retry_conflict=retry_conflict,
            **{key: value for key, value in readback.items() if key != "passed"},
        )

    def _concurrent(self, museum: MuseumFixture) -> dict[str, Any]:
        document = museum.document("B")
        content = document.source_path.read_bytes()
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: self.upload(museum.name, document, content), range(2)))
        codes = [self._api_code(body) if status == 200 else None for status, body in outcomes]
        success_count = sum(code in (0, "0") for code in codes)
        conflict_count = sum(code not in (None, 0, "0") for code in codes)
        readback = self._exact_readback(museum, document)
        return _case(
            f"{museum.name}:B:concurrent_create",
            success_count == 1 and conflict_count == 1 and readback["passed"],
            upload_success_count=success_count,
            upload_conflict_count=conflict_count,
            **{key: value for key, value in readback.items() if key != "passed"},
        )

    def _checksum_collision(self, museum: MuseumFixture) -> dict[str, Any]:
        document = museum.document("C")
        original = document.source_path.read_bytes()
        collision = document.collision_source_path.read_bytes()  # type: ignore[union-attr]
        initial_status, initial_body = self.upload(museum.name, document, original)
        collision_status, collision_body = self.upload(museum.name, document, collision)
        readback = self._exact_readback(museum, document)
        initial_success = initial_status == 200 and self._api_code(initial_body) in (0, "0")
        collision_rejected = collision_status == 200 and self._api_code(collision_body) not in (None, 0, "0")
        downloaded_status, downloaded = self.download(museum.name, document)
        alternate_adoption_rejected = downloaded_status == 200 and (len(downloaded) != document.collision_size_bytes or hashlib.sha256(downloaded).hexdigest() != document.collision_sha256)
        return _case(
            f"{museum.name}:C:checksum_collision",
            initial_success and collision_rejected and alternate_adoption_rejected and readback["passed"],
            initial_create_succeeded=initial_success,
            collision_upload_rejected=collision_rejected,
            alternate_adoption_rejected=alternate_adoption_rejected,
            **{key: value for key, value in readback.items() if key != "passed"},
        )

    def _wrong_dataset_collision(self, museum: MuseumFixture) -> dict[str, Any]:
        document = museum.document("A")
        wrong_dataset_id = museum.document("C").dataset_id
        wrong_pair = replace(document, dataset_id=wrong_dataset_id)
        status, body = self.upload(museum.name, wrong_pair, document.source_path.read_bytes())
        collision_rejected = status == 200 and self._api_code(body) not in (None, 0, "0")
        list_status, listed = self.list_document(museum.name, wrong_pair)
        data = listed.get("data") if isinstance(listed, dict) else None
        docs = data.get("docs") if isinstance(data, dict) else None
        wrong_dataset_write_absent = list_status == 200 and self._api_code(listed) == 0 and isinstance(docs, list) and not docs
        original = self._exact_readback(museum, document)
        return _case(
            f"{museum.name}:A:wrong_dataset_collision",
            collision_rejected and wrong_dataset_write_absent and original["passed"],
            collision_upload_rejected=collision_rejected,
            wrong_dataset_write_absent=wrong_dataset_write_absent,
            original_download_checksum_matches=original["download_checksum_matches"],
        )

    def _normal_create(self, museum: MuseumFixture) -> dict[str, Any]:
        document = museum.document("D")
        status, body = self.upload(museum.name, document, document.source_path.read_bytes())
        readback = self._exact_readback(museum, document)
        created = status == 200 and self._api_code(body) in (0, "0")
        return _case(
            f"{museum.name}:D:exact_create",
            created and readback["passed"],
            initial_create_succeeded=created,
            **{key: value for key, value in readback.items() if key != "passed"},
        )

    def _parse_dataset(self, museum: MuseumFixture, dataset_id: str) -> list[dict[str, Any]]:
        documents = [document for document in museum.documents if document.dataset_id == dataset_id]
        status, body = self.parse(museum.name, dataset_id, [document.document_id for document in documents])
        accepted = status == 200 and self._api_code(body) in (0, "0")
        cases = [_case(f"{museum.name}:{dataset_id}:parse_once", accepted, document_count=len(documents))]
        if not accepted:
            return cases

        deadline = time.monotonic() + self.config.parse_deadline_seconds
        pending = {document.document_id: document for document in documents}
        final_docs: dict[str, dict[str, Any]] = {}
        while pending and time.monotonic() < deadline:
            for document_id, document in list(pending.items()):
                _, listed = self.list_document(museum.name, document)
                data = listed.get("data") if isinstance(listed, dict) else None
                docs = data.get("docs") if isinstance(data, dict) else None
                exact = [item for item in docs or [] if isinstance(item, dict) and item.get("id") == document_id]
                if len(exact) != 1:
                    continue
                run = str(exact[0].get("run", "")).upper()
                if run in {"FAIL", "4", "CANCEL", "2"}:
                    final_docs[document_id] = exact[0]
                    pending.pop(document_id)
                elif run in {"DONE", "3"} or self._numeric(exact[0].get("progress")) >= 1.0:
                    final_docs[document_id] = exact[0]
                    pending.pop(document_id)
            if pending:
                self.sleep(self.config.poll_interval_seconds)

        for document in documents:
            metadata = final_docs.get(document.document_id, {})
            run = str(metadata.get("run", "")).upper()
            chunk_status, chunk_body = self.list_chunks(museum.name, document)
            chunk_data = chunk_body.get("data") if isinstance(chunk_body, dict) else None
            chunks = chunk_data.get("chunks") if isinstance(chunk_data, dict) else None
            chunks = chunks if isinstance(chunks, list) else []
            chunk_ids = [chunk.get("id") for chunk in chunks if isinstance(chunk, dict)]
            unique_chunk_ids = len(chunk_ids) == len(chunks) and all(isinstance(chunk_id, str) and chunk_id for chunk_id in chunk_ids) and len(chunk_ids) == len(set(chunk_ids))
            terminal_done = run in {"DONE", "3"} or self._numeric(metadata.get("progress")) >= 1.0
            metadata_chunk_count = int(self._numeric(metadata.get("chunk_count")))
            passed = terminal_done and metadata_chunk_count > 0 and chunk_status == 200 and self._api_code(chunk_body) == 0 and bool(chunks) and unique_chunk_ids
            cases.append(
                _case(
                    f"{museum.name}:{document.label}:single_chunk_set",
                    passed,
                    terminal_done=terminal_done,
                    metadata_chunk_count=metadata_chunk_count,
                    returned_chunk_count=len(chunks),
                    unique_chunk_ids=bool(unique_chunk_ids),
                )
            )
        return cases

    def run(self) -> dict[str, Any]:
        cases, clean = self._preflight()
        if clean:
            for museum in self.config.museums:
                cases.extend(
                    [
                        self._response_loss(museum),
                        self._wrong_dataset_collision(museum),
                        self._concurrent(museum),
                        self._checksum_collision(museum),
                        self._normal_create(museum),
                    ]
                )
                for dataset_id in sorted({document.dataset_id for document in museum.documents}):
                    cases.extend(self._parse_dataset(museum, dataset_id))
        failures = [case for case in cases if case["status"] != "PASSED"]
        return {
            "schema": SCHEMA,
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "status": "PASSED" if not failures and clean else "FAILED",
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
                "mime_types": sorted({document.mime_type for museum in self.config.museums for document in museum.documents}),
            },
            "clean_namespace_proof": clean,
            "case_count": len(cases),
            "failed_case_count": len(failures),
            "cases": cases,
        }


def _build_http_transports(config: HarnessConfig, environment: Mapping[str, str]):
    tokens = {}
    for museum in config.museums:
        token = environment.get(museum.token_env)
        if not token:
            raise ManifestError(f"required token environment variable {museum.token_env} is not set")
        tokens[museum.name] = token
    timeout = (config.connect_timeout_seconds, config.read_timeout_seconds)

    def headers(museum_name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens[museum_name]}"}

    def json_body(response: requests.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("provider returned non-JSON control data") from exc
        if not isinstance(body, dict):
            raise RuntimeError("provider returned a non-object JSON value")
        return body

    def upload(museum_name: str, document: DocumentFixture, content: bytes):
        response = requests.post(
            f"{config.base_url}/api/v1/datasets/{document.dataset_id}/documents",
            headers=headers(museum_name),
            files={"file": (f"{document.document_id}{document.suffix}", content, document.mime_type)},
            data={"document_id": document.document_id},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    def list_document(museum_name: str, document: DocumentFixture):
        response = requests.get(
            f"{config.base_url}/api/v1/datasets/{document.dataset_id}/documents",
            headers=headers(museum_name),
            params={"id": document.document_id, "page": 1, "page_size": 2},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    def download(museum_name: str, document: DocumentFixture):
        response = requests.get(
            f"{config.base_url}/api/v1/datasets/{document.dataset_id}/documents/{document.document_id}",
            headers=headers(museum_name),
            timeout=timeout,
        )
        return response.status_code, response.content

    def parse(museum_name: str, dataset_id: str, document_ids: list[str]):
        response = requests.post(
            f"{config.base_url}/api/v1/datasets/{dataset_id}/chunks",
            headers=headers(museum_name),
            json={"document_ids": document_ids},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    def list_chunks(museum_name: str, document: DocumentFixture):
        response = requests.get(
            f"{config.base_url}/api/v1/datasets/{document.dataset_id}/documents/{document.document_id}/chunks",
            headers=headers(museum_name),
            params={"page": 1, "page_size": 1000},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    return upload, list_document, download, parse, list_chunks


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MuseMind RAGFlow C-04 live conformance")
    parser.add_argument("--manifest", type=Path, required=True, help="JSON manifest with synthetic fixtures")
    parser.add_argument("--output", type=Path, help="Optional content-free JSON result path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        raw = json.loads(args.manifest.read_text(encoding="utf-8"))
        config = load_config(raw, args.manifest.resolve().parent)
        transports = _build_http_transports(config, os.environ)
        result = ConformanceRunner(config, *transports).run()
    except (ManifestError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "ERROR", "error": str(exc)}, sort_keys=True))
        return 2
    except (requests.RequestException, RuntimeError):
        print(json.dumps({"schema": SCHEMA, "status": "ERROR", "error": "provider transport failure"}, sort_keys=True))
        return 2

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

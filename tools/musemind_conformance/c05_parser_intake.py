#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
"""Fail-closed live conformance runner for MuseMind C-05 parser intake."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import multiprocessing
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

SCHEMA = "musemind.ragflow-c05/v1"
MAX_SOURCE_BYTES = 25_000_000
ALLOWED_MIME_TYPES = {"application/pdf", "text/markdown", "text/plain"}
LIVE_CASES = ("valid_pdf", "valid_plain", "valid_markdown", "parser_bomb")
INVALID_CASE_REASONS = {
    "mime_magic_mismatch": "MIME_MAGIC_MISMATCH",
    "nul_text": "TEXT_NUL",
    "non_utf8_text": "TEXT_NOT_UTF8",
    "encrypted_pdf": "PDF_ENCRYPTED",
    "corrupt_pdf": "PDF_INVALID",
    "oversize": "TOO_LARGE",
}
EXPECTED_CASES = frozenset((*LIVE_CASES, *INVALID_CASE_REASONS))
HEX_32 = re.compile(r"[0-9a-f]{32}")
HEX_40 = re.compile(r"[0-9a-f]{40}")
HEX_64 = re.compile(r"[0-9a-f]{64}")
KNOWN_BINARY_SIGNATURES = (
    b"%PDF-",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"PK\x03\x04",
    b"\x1f\x8b",
    b"\x7fELF",
)


class ManifestError(ValueError):
    """The qualification manifest is unsafe, mutable, or internally inconsistent."""


@dataclass(frozen=True)
class Bundle:
    fork_commit: str
    image_digest: str
    sdk_sha256: str
    bundle_descriptor_sha256: str


@dataclass(frozen=True)
class Fixture:
    case: str
    document_id: str
    mime_type: str
    source_path: Path
    size_bytes: int
    sha256: str

    @property
    def suffix(self) -> str:
        return {"application/pdf": ".pdf", "text/markdown": ".md", "text/plain": ".txt"}[self.mime_type]


@dataclass(frozen=True)
class HarnessConfig:
    base_url: str
    bundle: Bundle
    harness_sha256: str
    museum_name: str
    token_env: str
    canary_env: str
    dataset_id: str
    fixtures: tuple[Fixture, ...]
    connect_timeout_seconds: float
    read_timeout_seconds: float
    pdf_validation_deadline_seconds: float
    valid_parse_deadline_seconds: float
    hang_deadline_seconds: float
    post_cancel_observation_seconds: float
    poll_interval_seconds: float
    trace_sink: str

    def fixture(self, case: str) -> Fixture:
        return next(fixture for fixture in self.fixtures if fixture.case == case)


Upload = Callable[[Fixture, bytes], tuple[int, dict[str, Any]]]
ListDocument = Callable[[Fixture], tuple[int, dict[str, Any]]]
Parse = Callable[[list[str]], tuple[int, dict[str, Any]]]
Stop = Callable[[list[str]], tuple[int, dict[str, Any]]]
ListChunks = Callable[[Fixture], tuple[int, dict[str, Any]]]


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


def _verified_fixture(raw: Any, manifest_dir: Path) -> Fixture:
    if not isinstance(raw, dict) or set(raw) != {"case", "document_id", "mime_type", "source_path", "size_bytes", "sha256"}:
        raise ManifestError("each fixture must contain exactly case, document_id, mime_type, source_path, size_bytes and sha256")
    case = _required_string(raw["case"], "fixture.case")
    if case not in EXPECTED_CASES:
        raise ManifestError("fixture.case is not part of the C-05 matrix")
    mime_type = _required_string(raw["mime_type"], "fixture.mime_type")
    if mime_type not in ALLOWED_MIME_TYPES:
        raise ManifestError("fixture.mime_type is outside the ADR-0029 allowlist")
    path = Path(_required_string(raw["source_path"], "fixture.source_path"))
    if not path.is_absolute():
        path = manifest_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ManifestError("fixture.source_path must identify an existing regular file")
    size_bytes = int(_positive_number(raw["size_bytes"], "fixture.size_bytes", allow_zero=True))
    sha256 = _immutable_hex(raw["sha256"], "fixture.sha256", HEX_64)
    digest = hashlib.sha256()
    observed_size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            observed_size += len(block)
            digest.update(block)
    if observed_size != size_bytes or digest.hexdigest() != sha256:
        raise ManifestError("fixture size/SHA-256 does not match the local file")
    return Fixture(
        case=case,
        document_id=_immutable_hex(raw["document_id"], "fixture.document_id", HEX_32),
        mime_type=mime_type,
        source_path=path,
        size_bytes=size_bytes,
        sha256=sha256,
    )


def load_config(raw: Any, manifest_dir: Path = Path(".")) -> HarnessConfig:
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ManifestError(f"manifest schema must be {SCHEMA}")
    expected = {
        "schema",
        "base_url",
        "bundle",
        "harness_sha256",
        "museum",
        "fixtures",
        "timeouts",
        "observability",
    }
    if set(raw) != expected:
        raise ManifestError("manifest fields do not match the C-05 schema")
    base_url = _required_string(raw["base_url"], "base_url").rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ManifestError("base_url must use HTTP or HTTPS")

    bundle_raw = raw["bundle"]
    if not isinstance(bundle_raw, dict) or set(bundle_raw) != {
        "fork_commit",
        "image_digest",
        "sdk_sha256",
        "bundle_descriptor_sha256",
    }:
        raise ManifestError("bundle fields do not match the immutable bundle schema")
    image_digest = _required_string(bundle_raw["image_digest"], "bundle.image_digest")
    if not image_digest.startswith("sha256:"):
        raise ManifestError("bundle.image_digest must be an OCI sha256 digest")
    bundle = Bundle(
        fork_commit=_immutable_hex(bundle_raw["fork_commit"], "bundle.fork_commit", HEX_40),
        image_digest=f"sha256:{_immutable_hex(image_digest.removeprefix('sha256:'), 'bundle.image_digest', HEX_64)}",
        sdk_sha256=_immutable_hex(bundle_raw["sdk_sha256"], "bundle.sdk_sha256", HEX_64),
        bundle_descriptor_sha256=_immutable_hex(
            bundle_raw["bundle_descriptor_sha256"], "bundle.bundle_descriptor_sha256", HEX_64
        ),
    )
    harness_sha256 = _immutable_hex(raw["harness_sha256"], "harness_sha256", HEX_64)
    observed_harness_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if harness_sha256 != observed_harness_sha256:
        raise ManifestError("harness_sha256 does not match the executed runner")

    museum_raw = raw["museum"]
    if not isinstance(museum_raw, dict) or set(museum_raw) != {"name", "token_env", "canary_env", "dataset_id"}:
        raise ManifestError("museum fields do not match the C-05 schema")
    fixtures_raw = raw["fixtures"]
    if not isinstance(fixtures_raw, list) or len(fixtures_raw) != len(EXPECTED_CASES):
        raise ManifestError("fixtures must contain the complete C-05 matrix")
    fixtures = tuple(_verified_fixture(item, manifest_dir) for item in fixtures_raw)
    if {fixture.case for fixture in fixtures} != EXPECTED_CASES:
        raise ManifestError("fixtures must define every C-05 case exactly once")
    if len({fixture.document_id for fixture in fixtures}) != len(fixtures):
        raise ManifestError("fixture document IDs must be unique")
    if any(next(fixture for fixture in fixtures if fixture.case == case).mime_type != expected for case, expected in {
        "valid_pdf": "application/pdf",
        "valid_plain": "text/plain",
        "valid_markdown": "text/markdown",
        "parser_bomb": "application/pdf",
        "nul_text": "text/plain",
        "non_utf8_text": "text/markdown",
        "encrypted_pdf": "application/pdf",
        "corrupt_pdf": "application/pdf",
        "oversize": "text/plain",
        "mime_magic_mismatch": "text/plain",
    }.items()):
        raise ManifestError("fixture MIME assignments do not match the C-05 matrix")

    timeouts = raw["timeouts"]
    if not isinstance(timeouts, dict) or set(timeouts) != {
        "connect_seconds",
        "read_seconds",
        "pdf_validation_deadline_seconds",
        "valid_parse_deadline_seconds",
        "hang_deadline_seconds",
        "post_cancel_observation_seconds",
        "poll_interval_seconds",
    }:
        raise ManifestError("timeouts fields do not match the C-05 schema")
    observability = raw["observability"]
    if not isinstance(observability, dict) or set(observability) != {"trace_sink"}:
        raise ManifestError("observability must contain exactly trace_sink")
    trace_sink = _required_string(observability["trace_sink"], "observability.trace_sink")
    if trace_sink != "NOT_CONFIGURED":
        raise ManifestError("C-05 local qualification supports only an explicitly absent trace sink")
    return HarnessConfig(
        base_url=base_url,
        bundle=bundle,
        harness_sha256=harness_sha256,
        museum_name=_required_string(museum_raw["name"], "museum.name"),
        token_env=_required_string(museum_raw["token_env"], "museum.token_env"),
        canary_env=_required_string(museum_raw["canary_env"], "museum.canary_env"),
        dataset_id=_immutable_hex(museum_raw["dataset_id"], "museum.dataset_id", HEX_32),
        fixtures=fixtures,
        connect_timeout_seconds=_positive_number(timeouts["connect_seconds"], "timeouts.connect_seconds"),
        read_timeout_seconds=_positive_number(timeouts["read_seconds"], "timeouts.read_seconds"),
        pdf_validation_deadline_seconds=_positive_number(
            timeouts["pdf_validation_deadline_seconds"], "timeouts.pdf_validation_deadline_seconds"
        ),
        valid_parse_deadline_seconds=_positive_number(
            timeouts["valid_parse_deadline_seconds"], "timeouts.valid_parse_deadline_seconds"
        ),
        hang_deadline_seconds=_positive_number(timeouts["hang_deadline_seconds"], "timeouts.hang_deadline_seconds"),
        post_cancel_observation_seconds=_positive_number(
            timeouts["post_cancel_observation_seconds"], "timeouts.post_cancel_observation_seconds"
        ),
        poll_interval_seconds=_positive_number(
            timeouts["poll_interval_seconds"], "timeouts.poll_interval_seconds", allow_zero=True
        ),
        trace_sink=trace_sink,
    )


def _pdf_validation_worker(payload: bytes, connection) -> None:
    result = "PDF_INVALID"
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(payload), strict=True)
        if reader.is_encrypted:
            result = "PDF_ENCRYPTED"
        elif len(reader.pages) < 1:
            result = "PDF_INVALID"
        else:
            for page in reader.pages:
                page.get_object()
                _ = page.mediabox
            reader.trailer["/Root"].get_object()
            result = "VALID"
    except Exception:  # noqa: BLE001 - all untrusted parser failures collapse to one safe reason
        result = "PDF_INVALID"
    try:
        connection.send(result)
    finally:
        connection.close()


def _validate_pdf(payload: bytes, deadline_seconds: float) -> str:
    if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-2048:]:
        return "MIME_MAGIC_MISMATCH" if not payload.startswith(b"%PDF-") else "PDF_INVALID"
    available_methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("fork" if "fork" in available_methods else "spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_pdf_validation_worker, args=(payload, child), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(deadline_seconds):
            process.terminate()
            process.join(timeout=1)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1)
            return "PDF_VALIDATION_TIMEOUT"
        try:
            result = parent.recv()
        except EOFError:
            return "PDF_INVALID"
        return result if result in {"VALID", "PDF_ENCRYPTED", "PDF_INVALID"} else "PDF_INVALID"
    finally:
        parent.close()
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)


def validate_intake(payload: bytes, mime_type: str, pdf_validation_deadline_seconds: float) -> str:
    """Return VALID or one stable, content-free C-05 intake reason."""
    if len(payload) > MAX_SOURCE_BYTES:
        return "TOO_LARGE"
    if mime_type not in ALLOWED_MIME_TYPES:
        return "MIME_NOT_ALLOWED"
    if mime_type == "application/pdf":
        return _validate_pdf(payload, pdf_validation_deadline_seconds)
    if any(payload.startswith(signature) for signature in KNOWN_BINARY_SIGNATURES):
        return "MIME_MAGIC_MISMATCH"
    if b"\x00" in payload:
        return "TEXT_NUL"
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "TEXT_NOT_UTF8"
    return "VALID"


def _case(name: str, passed: bool, **facts: Any) -> dict[str, Any]:
    return {"name": name, "status": "PASSED" if passed else "FAILED", **facts}


class ConformanceRunner:
    def __init__(
        self,
        config: HarnessConfig,
        upload: Upload,
        list_document: ListDocument,
        parse: Parse,
        stop: Stop,
        list_chunks: ListChunks,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self._upload_transport = upload
        self._list_document_transport = list_document
        self._parse_transport = parse
        self._stop_transport = stop
        self._list_chunks_transport = list_chunks
        self.sleep = sleep
        self.provider_call_count = 0

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

    def _upload(self, fixture: Fixture, payload: bytes) -> tuple[int, dict[str, Any]]:
        self.provider_call_count += 1
        return self._upload_transport(fixture, payload)

    def _list_document(self, fixture: Fixture) -> tuple[int, dict[str, Any]]:
        self.provider_call_count += 1
        return self._list_document_transport(fixture)

    def _parse(self, document_ids: list[str]) -> tuple[int, dict[str, Any]]:
        self.provider_call_count += 1
        return self._parse_transport(document_ids)

    def _stop(self, document_ids: list[str]) -> tuple[int, dict[str, Any]]:
        self.provider_call_count += 1
        return self._stop_transport(document_ids)

    def _list_chunks(self, fixture: Fixture) -> tuple[int, dict[str, Any]]:
        self.provider_call_count += 1
        return self._list_chunks_transport(fixture)

    def _document(self, fixture: Fixture) -> tuple[int, dict[str, Any] | None]:
        status, body = self._list_document(fixture)
        data = body.get("data") if isinstance(body, dict) else None
        docs = data.get("docs") if isinstance(data, dict) else None
        docs = docs if isinstance(docs, list) else []
        exact = [item for item in docs if isinstance(item, dict) and item.get("id") == fixture.document_id]
        return status, exact[0] if len(exact) == 1 else None

    @staticmethod
    def _run_state(document: dict[str, Any] | None) -> str:
        raw = str((document or {}).get("run", "")).upper()
        return {
            "0": "UNSTART",
            "1": "RUNNING",
            "2": "CANCEL",
            "3": "DONE",
            "4": "FAIL",
        }.get(raw, raw)

    def _chunk_count(self, document: dict[str, Any] | None) -> int:
        return int(self._numeric((document or {}).get("chunk_count", (document or {}).get("chunk_num", 0))))

    def _chunks(self, fixture: Fixture) -> tuple[bool, int, bool]:
        status, body = self._list_chunks(fixture)
        data = body.get("data") if isinstance(body, dict) else None
        chunks = data.get("chunks") if isinstance(data, dict) else None
        chunks = chunks if isinstance(chunks, list) else []
        ids = [item.get("id") for item in chunks if isinstance(item, dict) and isinstance(item.get("id"), str)]
        return status == 200 and self._api_code(body) in (0, "0"), len(chunks), len(ids) == len(chunks) == len(set(ids))

    def _invalid_cases(self) -> list[dict[str, Any]]:
        cases = []
        for case_name, expected_reason in INVALID_CASE_REASONS.items():
            fixture = self.config.fixture(case_name)
            before = self.provider_call_count
            reason = validate_intake(
                fixture.source_path.read_bytes(),
                fixture.mime_type,
                self.config.pdf_validation_deadline_seconds,
            )
            provider_delta = self.provider_call_count - before
            cases.append(
                _case(
                    f"{case_name}:intake_reject",
                    reason == expected_reason and provider_delta == 0,
                    reason=reason,
                    provider_call_delta=provider_delta,
                )
            )
        return cases

    def _clean_preflight(self) -> tuple[list[dict[str, Any]], bool]:
        cases = []
        clean = True
        for case_name in LIVE_CASES:
            fixture = self.config.fixture(case_name)
            status, document = self._document(fixture)
            passed = status == 200 and document is None
            clean = clean and passed
            cases.append(_case(f"{case_name}:clean_preflight", passed, document_count=0 if document is None else 1))
        return cases, clean

    def _cancel_and_prove_empty(self, fixture: Fixture) -> dict[str, Any]:
        stop_status, stop_body = self._stop([fixture.document_id])
        stop_accepted = stop_status == 200 and self._api_code(stop_body) in (0, "0")
        deadline = time.monotonic() + self.config.post_cancel_observation_seconds
        observed_cancel = False
        terminal_document: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            _, terminal_document = self._document(fixture)
            observed_cancel = observed_cancel or self._run_state(terminal_document) == "CANCEL"
            self.sleep(self.config.poll_interval_seconds)
        _, terminal_document = self._document(fixture)
        chunks_ok, chunk_count, _ = self._chunks(fixture)
        final_state = self._run_state(terminal_document)
        no_ready_output = final_state == "CANCEL" and self._chunk_count(terminal_document) == 0 and chunks_ok and chunk_count == 0
        return {
            "stop_accepted": stop_accepted,
            "cancel_observed": observed_cancel,
            "final_state": final_state,
            "chunk_count": chunk_count,
            "no_ready_output": no_ready_output,
            "passed": stop_accepted and observed_cancel and no_ready_output,
        }

    def _valid_parse(self, case_name: str) -> dict[str, Any]:
        fixture = self.config.fixture(case_name)
        payload = fixture.source_path.read_bytes()
        intake_reason = validate_intake(payload, fixture.mime_type, self.config.pdf_validation_deadline_seconds)
        upload_status, upload_body = self._upload(fixture, payload) if intake_reason == "VALID" else (0, {})
        upload_accepted = upload_status == 200 and self._api_code(upload_body) in (0, "0")
        parse_status, parse_body = self._parse([fixture.document_id]) if upload_accepted else (0, {})
        parse_accepted = parse_status == 200 and self._api_code(parse_body) in (0, "0")
        started = time.monotonic()
        deadline = started + self.config.valid_parse_deadline_seconds
        terminal: dict[str, Any] | None = None
        while parse_accepted and time.monotonic() < deadline:
            _, observed = self._document(fixture)
            state = self._run_state(observed)
            if state in {"DONE", "FAIL", "CANCEL"}:
                terminal = observed
                break
            self.sleep(self.config.poll_interval_seconds)
        duration_ms = int((time.monotonic() - started) * 1000)
        state = self._run_state(terminal)
        chunks_ok, chunk_count, unique_chunk_ids = self._chunks(fixture) if state == "DONE" else (False, 0, False)
        passed = (
            intake_reason == "VALID"
            and upload_accepted
            and parse_accepted
            and state == "DONE"
            and self._chunk_count(terminal) > 0
            and chunks_ok
            and chunk_count > 0
            and unique_chunk_ids
        )
        cancellation = None
        if parse_accepted and state not in {"DONE", "FAIL", "CANCEL"}:
            cancellation = self._cancel_and_prove_empty(fixture)
        return _case(
            f"{case_name}:live_parse",
            passed,
            intake_reason=intake_reason,
            upload_accepted=upload_accepted,
            parse_accepted=parse_accepted,
            terminal_state=state or "DEADLINE",
            duration_ms=duration_ms,
            chunk_count=chunk_count,
            unique_chunk_ids=unique_chunk_ids,
            deadline_seconds=self.config.valid_parse_deadline_seconds,
            fail_closed_cancellation=bool(cancellation and cancellation["passed"]),
        )

    def _parser_bomb(self) -> dict[str, Any]:
        fixture = self.config.fixture("parser_bomb")
        payload = fixture.source_path.read_bytes()
        intake_reason = validate_intake(payload, fixture.mime_type, self.config.pdf_validation_deadline_seconds)
        upload_status, upload_body = self._upload(fixture, payload) if intake_reason == "VALID" else (0, {})
        upload_accepted = upload_status == 200 and self._api_code(upload_body) in (0, "0")
        parse_status, parse_body = self._parse([fixture.document_id]) if upload_accepted else (0, {})
        parse_accepted = parse_status == 200 and self._api_code(parse_body) in (0, "0")
        started = time.monotonic()
        deadline = started + self.config.hang_deadline_seconds
        state_at_deadline = ""
        while parse_accepted and time.monotonic() < deadline:
            _, observed = self._document(fixture)
            state_at_deadline = self._run_state(observed)
            if state_at_deadline in {"DONE", "FAIL", "CANCEL"}:
                break
            self.sleep(self.config.poll_interval_seconds)
        deadline_elapsed = time.monotonic() >= deadline and state_at_deadline not in {"DONE", "FAIL", "CANCEL"}
        cancellation = self._cancel_and_prove_empty(fixture) if deadline_elapsed else {"passed": False, "final_state": state_at_deadline, "chunk_count": 0, "no_ready_output": False}
        duration_ms = int((time.monotonic() - started) * 1000)
        passed = intake_reason == "VALID" and upload_accepted and parse_accepted and deadline_elapsed and cancellation["passed"]
        return _case(
            "parser_bomb:deadline_cancel",
            passed,
            intake_reason=intake_reason,
            upload_accepted=upload_accepted,
            parse_accepted=parse_accepted,
            deadline_elapsed=deadline_elapsed,
            state_at_deadline=state_at_deadline or "RUNNING",
            final_state=cancellation["final_state"],
            duration_ms=duration_ms,
            chunk_count=cancellation["chunk_count"],
            no_ready_output=cancellation["no_ready_output"],
            deadline_seconds=self.config.hang_deadline_seconds,
            observation_seconds=self.config.post_cancel_observation_seconds,
        )

    def run(self, canary: str) -> dict[str, Any]:
        cases = self._invalid_cases()
        preflight_cases, clean = self._clean_preflight()
        cases.extend(preflight_cases)
        if clean:
            for case_name in LIVE_CASES[:3]:
                cases.append(self._valid_parse(case_name))
            cases.append(self._parser_bomb())
        failures = [case for case in cases if case["status"] != "PASSED"]
        runtime_status = "PASSED" if not failures and clean else "FAILED"
        result = {
            "schema": SCHEMA,
            "status": "INCOMPLETE" if runtime_status == "PASSED" else "FAILED",
            "runtime_status": runtime_status,
            "executed_at": datetime.now(UTC).isoformat(),
            "bundle": {
                "fork_commit": self.config.bundle.fork_commit,
                "image_digest": self.config.bundle.image_digest,
                "sdk_sha256": self.config.bundle.sdk_sha256,
                "bundle_descriptor_sha256": self.config.bundle.bundle_descriptor_sha256,
            },
            "harness_sha256": self.config.harness_sha256,
            "museum_fixture": self.config.museum_name,
            "clean_namespace_proof": clean,
            "case_count": len(cases),
            "failed_case_count": len(failures),
            "provider_call_count": self.provider_call_count,
            "observed_valid_parse_duration_ms": {
                case["name"].split(":", 1)[0]: case["duration_ms"]
                for case in cases
                if case["name"].endswith(":live_parse")
            },
            "observability": {
                "canary_absent_from_runtime_result": None,
                "canary_absent_from_runtime_logs": None,
                "provider_errors_content_safe": True,
                "trace_sink": self.config.trace_sink,
            },
            "cases": cases,
        }
        rendered = json.dumps(result, sort_keys=True)
        result["observability"]["canary_absent_from_runtime_result"] = canary not in rendered
        if not result["observability"]["canary_absent_from_runtime_result"]:
            result["runtime_status"] = "FAILED"
            result["status"] = "FAILED"
            result["failed_case_count"] += 1
        return result


def _build_http_transports(config: HarnessConfig, environment: Mapping[str, str]):
    token = environment.get(config.token_env)
    if not token:
        raise ManifestError("museum token environment variable is missing")
    headers = {"Authorization": f"Bearer {token}"}
    timeout = (config.connect_timeout_seconds, config.read_timeout_seconds)

    def json_body(response: requests.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("provider returned non-JSON control data") from exc
        if not isinstance(body, dict):
            raise TypeError("provider returned a non-object JSON value")
        return body

    def upload(fixture: Fixture, payload: bytes):
        response = requests.post(
            f"{config.base_url}/api/v1/datasets/{config.dataset_id}/documents",
            headers=headers,
            files={"file": (f"{fixture.document_id}{fixture.suffix}", payload, fixture.mime_type)},
            data={"document_id": fixture.document_id},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    def list_document(fixture: Fixture):
        response = requests.get(
            f"{config.base_url}/api/v1/datasets/{config.dataset_id}/documents",
            headers=headers,
            params={"ids": fixture.document_id, "page": 1, "page_size": 2},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    def parse(document_ids: list[str]):
        response = requests.post(
            f"{config.base_url}/api/v1/datasets/{config.dataset_id}/documents/parse",
            headers=headers,
            json={"document_ids": document_ids},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    def stop(document_ids: list[str]):
        response = requests.post(
            f"{config.base_url}/api/v1/datasets/{config.dataset_id}/documents/stop",
            headers=headers,
            json={"document_ids": document_ids},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    def list_chunks(fixture: Fixture):
        response = requests.get(
            f"{config.base_url}/api/v1/datasets/{config.dataset_id}/documents/{fixture.document_id}/chunks",
            headers=headers,
            params={"page": 1, "page_size": 100},
            timeout=timeout,
        )
        return response.status_code, json_body(response)

    return upload, list_document, parse, stop, list_chunks


def finalize_log_scan(config: HarnessConfig, result: Any, log_bytes: bytes, canary: str) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("schema") != SCHEMA:
        raise ManifestError("existing result does not use the C-05 schema")
    if result.get("runtime_status") != "PASSED" or result.get("status") != "INCOMPLETE":
        raise ManifestError("only a runtime-PASSED incomplete result can be finalized")
    canary_bytes = canary.encode("utf-8")
    rendered_before = json.dumps(result, sort_keys=True).encode("utf-8")
    observability = result.get("observability")
    if not isinstance(observability, dict):
        raise ManifestError("existing result observability is malformed")
    observability["canary_absent_from_runtime_result"] = canary_bytes not in rendered_before
    observability["canary_absent_from_runtime_logs"] = canary_bytes not in log_bytes
    observability["runtime_log_sha256"] = hashlib.sha256(log_bytes).hexdigest()
    observability["runtime_log_size_bytes"] = len(log_bytes)
    observability["trace_sink"] = config.trace_sink
    passed = all(
        (
            observability["canary_absent_from_runtime_result"],
            observability["canary_absent_from_runtime_logs"],
            observability.get("provider_errors_content_safe") is True,
            config.trace_sink == "NOT_CONFIGURED",
        )
    )
    result["status"] = "PASSED" if passed else "FAILED"
    result["finalized_at"] = datetime.now(UTC).isoformat()
    return result


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MuseMind RAGFlow C-05 live conformance")
    parser.add_argument("--manifest", type=Path, required=True, help="JSON manifest with synthetic fixtures")
    parser.add_argument("--output", type=Path, required=True, help="Content-free JSON result path")
    parser.add_argument("--finalize-log-scan", type=Path, help="Finalize an existing result using captured runtime logs")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        raw = json.loads(args.manifest.read_text(encoding="utf-8"))
        config = load_config(raw, args.manifest.parent)
        canary = os.environ.get(config.canary_env)
        if not canary or len(canary) < 24:
            raise ManifestError("canary environment variable is missing or too short")
        if args.finalize_log_scan:
            existing = json.loads(args.output.read_text(encoding="utf-8"))
            result = finalize_log_scan(config, existing, args.finalize_log_scan.read_bytes(), canary)
        else:
            transports = _build_http_transports(config, os.environ)
            result = ConformanceRunner(config, *transports).run(canary)
    except (ManifestError, OSError, json.JSONDecodeError):
        print(json.dumps({"schema": SCHEMA, "status": "ERROR", "error": "invalid qualification input"}, sort_keys=True))
        return 2
    except requests.RequestException:
        print(json.dumps({"schema": SCHEMA, "status": "ERROR", "error": "provider transport failure"}, sort_keys=True))
        return 2
    except (RuntimeError, TypeError):
        print(json.dumps({"schema": SCHEMA, "status": "ERROR", "error": "provider control failure"}, sort_keys=True))
        return 2

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

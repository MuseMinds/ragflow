import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pypdf import PdfWriter

from tools.musemind_conformance.c05_parser_intake import (
    MAX_SOURCE_BYTES,
    ConformanceRunner,
    ManifestError,
    finalize_log_scan,
    load_config,
    validate_intake,
)

CANARY = "MM-C05-CANARY-0123456789ABCDEF"


def _write(path: Path, content: bytes):
    path.write_bytes(content)
    return {
        "source_path": str(path),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _pdf(path: Path, *, encrypted: bool = False, pages: int = 1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    if encrypted:
        writer.encrypt("synthetic-password")
    with path.open("wb") as stream:
        writer.write(stream)
    return _write(path, path.read_bytes())


def _manifest(tmp_path: Path):
    valid_pdf = _pdf(tmp_path / "valid.pdf")
    encrypted_pdf = _pdf(tmp_path / "encrypted.pdf", encrypted=True)
    parser_bomb = _pdf(tmp_path / "bomb.pdf", pages=20)
    mismatch_pdf = _pdf(tmp_path / "mismatch.pdf")
    corrupt_pdf = _write(tmp_path / "corrupt.pdf", b"%PDF-1.7\ncorrupt\n%%EOF\n")
    oversize_path = tmp_path / "oversize.txt"
    with oversize_path.open("wb") as stream:
        stream.write(CANARY.encode())
        stream.seek(MAX_SOURCE_BYTES)
        stream.write(b"x")
    oversize = _write(oversize_path, oversize_path.read_bytes())
    fixtures = {
        "valid_pdf": ("application/pdf", valid_pdf),
        "valid_plain": ("text/plain", _write(tmp_path / "valid.txt", f"Italiano {CANARY}\n".encode())),
        "valid_markdown": ("text/markdown", _write(tmp_path / "valid.md", f"# Italiano\n\n{CANARY}\n".encode())),
        "parser_bomb": ("application/pdf", parser_bomb),
        "mime_magic_mismatch": ("text/plain", mismatch_pdf),
        "nul_text": ("text/plain", _write(tmp_path / "nul.txt", f"{CANARY}\x00".encode())),
        "non_utf8_text": ("text/markdown", _write(tmp_path / "non-utf8.md", CANARY.encode() + b"\xff")),
        "encrypted_pdf": ("application/pdf", encrypted_pdf),
        "corrupt_pdf": ("application/pdf", corrupt_pdf),
        "oversize": ("text/plain", oversize),
    }
    documents = []
    for index, (case, (mime_type, details)) in enumerate(fixtures.items(), start=1):
        documents.append(
            {
                "case": case,
                "document_id": f"{index:032x}",
                "mime_type": mime_type,
                **details,
            }
        )
    runner_path = Path(__file__).parents[3] / "tools" / "musemind_conformance" / "c05_parser_intake.py"
    return {
        "schema": "musemind.ragflow-c05/v1",
        "base_url": "http://127.0.0.1:9380",
        "bundle": {
            "fork_commit": "d" * 40,
            "image_digest": f"sha256:{'e' * 64}",
            "sdk_sha256": "f" * 64,
            "bundle_descriptor_sha256": "1" * 64,
        },
        "harness_sha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
        "museum": {
            "name": "museum-alpha",
            "token_env": "C05_TOKEN",
            "canary_env": "C05_CANARY",
            "dataset_id": "2" * 32,
        },
        "fixtures": documents,
        "timeouts": {
            "connect_seconds": 5,
            "read_seconds": 60,
            "pdf_validation_deadline_seconds": 5,
            "valid_parse_deadline_seconds": 0.02,
            "hang_deadline_seconds": 0.002,
            "post_cancel_observation_seconds": 0.002,
            "poll_interval_seconds": 0,
        },
        "observability": {"trace_sink": "NOT_CONFIGURED"},
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["bundle"].update(image_digest="musemind/ragflow:latest"),
        lambda manifest: manifest.update(harness_sha256="0" * 64),
        lambda manifest: manifest["fixtures"].pop(),
        lambda manifest: manifest["fixtures"][0].update(mime_type="text/plain"),
        lambda manifest: manifest["fixtures"][1].update(document_id=manifest["fixtures"][0]["document_id"]),
        lambda manifest: manifest["observability"].update(trace_sink="http://collector"),
    ],
)
def test_manifest_rejects_mutable_or_incoherent_input(tmp_path, mutate):
    manifest = _manifest(tmp_path)
    mutate(manifest)

    with pytest.raises(ManifestError):
        load_config(manifest)


def test_intake_matrix_is_fail_closed(tmp_path):
    config = load_config(_manifest(tmp_path))
    expected = {
        "valid_pdf": "VALID",
        "valid_plain": "VALID",
        "valid_markdown": "VALID",
        "parser_bomb": "VALID",
        "mime_magic_mismatch": "MIME_MAGIC_MISMATCH",
        "nul_text": "TEXT_NUL",
        "non_utf8_text": "TEXT_NOT_UTF8",
        "encrypted_pdf": "PDF_ENCRYPTED",
        "corrupt_pdf": "PDF_INVALID",
        "oversize": "TOO_LARGE",
    }

    observed = {
        fixture.case: validate_intake(
            fixture.source_path.read_bytes(),
            fixture.mime_type,
            config.pdf_validation_deadline_seconds,
        )
        for fixture in config.fixtures
    }

    assert observed == expected


class _FakeProvider:
    def __init__(self, *, hang_regular=False, late_ready_after_cancel=False):
        self.documents = {}
        self.chunks = {}
        self.hang_regular = hang_regular
        self.late_ready_after_cancel = late_ready_after_cancel

    def upload(self, fixture, payload):
        self.documents[fixture.document_id] = {"fixture": fixture, "run": "0", "chunk_count": 0}
        return 200, {"code": 0, "message": CANARY}

    def list_document(self, fixture):
        document = self.documents.get(fixture.document_id)
        docs = []
        if document:
            docs.append(
                {
                    "id": fixture.document_id,
                    "run": document["run"],
                    "chunk_count": document["chunk_count"],
                    "progress_msg": CANARY,
                }
            )
        return 200, {"code": 0, "data": {"docs": docs, "total": len(docs)}, "message": CANARY}

    def parse(self, document_ids):
        for document_id in document_ids:
            document = self.documents[document_id]
            case = document["fixture"].case
            if case == "parser_bomb" or (self.hang_regular and case == "valid_pdf"):
                document["run"] = "1"
            else:
                document["run"] = "3"
                document["chunk_count"] = 1
                self.chunks[document_id] = [{"id": f"chunk-{document_id}", "content": CANARY}]
        return 200, {"code": 0, "message": CANARY}

    def stop(self, document_ids):
        for document_id in document_ids:
            document = self.documents[document_id]
            if self.late_ready_after_cancel:
                document["run"] = "3"
                document["chunk_count"] = 1
                self.chunks[document_id] = [{"id": f"late-{document_id}", "content": CANARY}]
            else:
                document["run"] = "2"
                document["chunk_count"] = 0
                self.chunks[document_id] = []
        return 200, {"code": 0, "message": CANARY}

    def list_chunks(self, fixture):
        chunks = self.chunks.get(fixture.document_id, [])
        return 200, {"code": 0, "data": {"chunks": chunks, "total": len(chunks)}, "message": CANARY}


def _run(manifest, provider):
    config = load_config(manifest)
    return config, ConformanceRunner(
        config,
        provider.upload,
        provider.list_document,
        provider.parse,
        provider.stop,
        provider.list_chunks,
        sleep=lambda _: None,
    ).run(CANARY)


def test_live_matrix_passes_and_emits_only_content_free_facts(tmp_path):
    config, result = _run(_manifest(tmp_path), _FakeProvider())

    assert result["runtime_status"] == "PASSED"
    assert result["status"] == "INCOMPLETE"
    assert result["clean_namespace_proof"] is True
    assert result["failed_case_count"] == 0
    invalid_cases = [case for case in result["cases"] if case["name"].endswith(":intake_reject")]
    assert len(invalid_cases) == 6
    assert {case["provider_call_delta"] for case in invalid_cases} == {0}
    rendered = json.dumps(result)
    assert CANARY not in rendered
    assert str(tmp_path) not in rendered

    finalized = finalize_log_scan(config, deepcopy(result), b"content-free logs", CANARY)
    assert finalized["status"] == "PASSED"
    assert finalized["observability"]["canary_absent_from_runtime_logs"] is True
    assert finalized["observability"]["runtime_log_size_bytes"] == len(b"content-free logs")


def test_log_canary_fails_finalization(tmp_path):
    config, result = _run(_manifest(tmp_path), _FakeProvider())

    finalized = finalize_log_scan(config, result, f"leak={CANARY}".encode(), CANARY)

    assert finalized["status"] == "FAILED"
    assert finalized["observability"]["canary_absent_from_runtime_logs"] is False


@pytest.mark.parametrize(
    "provider",
    [
        _FakeProvider(hang_regular=True),
        _FakeProvider(late_ready_after_cancel=True),
    ],
)
def test_regular_timeout_or_late_ready_output_fails_closed(tmp_path, provider):
    _, result = _run(_manifest(tmp_path), provider)

    assert result["runtime_status"] == "FAILED"
    assert result["status"] == "FAILED"
    assert result["failed_case_count"] > 0

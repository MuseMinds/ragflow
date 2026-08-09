import hashlib
import threading
from copy import deepcopy

import pytest

from tools.musemind_conformance.create_adopt import ConformanceRunner, ManifestError, load_config


def _write_fixture(path, content):
    path.write_bytes(content)
    return {
        "source_path": str(path),
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _manifest(tmp_path):
    datasets = {"alpha": ("1" * 32, "2" * 32), "beta": ("3" * 32, "4" * 32)}
    document_ids = {
        "alpha": ("5" * 32, "6" * 32, "7" * 32, "8" * 32),
        "beta": ("9" * 32, "a" * 32, "b" * 32, "c" * 32),
    }
    mime_types = {
        "A": "application/pdf",
        "B": "text/plain",
        "C": "text/markdown",
        "D": "text/plain",
    }

    def documents(museum):
        result = []
        for index, label in enumerate("ABCD"):
            content = f"MM-C04-{museum.upper()}-{label}\n".encode()
            fixture = _write_fixture(tmp_path / f"{museum}-{label}.bin", content)
            item = {
                "label": label,
                "dataset_id": datasets[museum][0 if index < 2 else 1],
                "document_id": document_ids[museum][index],
                "mime_type": mime_types[label],
                **fixture,
            }
            if label == "C":
                collision = _write_fixture(tmp_path / f"{museum}-{label}-collision.bin", b"different synthetic bytes\n")
                item.update(
                    collision_source_path=collision["source_path"],
                    collision_size_bytes=collision["size_bytes"],
                    collision_sha256=collision["sha256"],
                )
            result.append(item)
        return result

    return {
        "schema": "musemind.ragflow-c04/v1",
        "base_url": "http://127.0.0.1:9380",
        "bundle": {
            "fork_commit": "d" * 40,
            "image_digest": f"sha256:{'e' * 64}",
            "sdk_sha256": "f" * 64,
            "bundle_descriptor_sha256": "1" * 64,
        },
        "museums": [
            {"name": "museum-alpha", "token_env": "ALPHA_TOKEN", "documents": documents("alpha")},
            {"name": "museum-beta", "token_env": "BETA_TOKEN", "documents": documents("beta")},
        ],
        "timeouts": {
            "connect_seconds": 5,
            "read_seconds": 60,
            "parse_deadline_seconds": 10,
            "poll_interval_seconds": 0,
        },
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["bundle"].update(image_digest="musemind/ragflow:latest"),
        lambda manifest: manifest["bundle"].update(fork_commit="0" * 40),
        lambda manifest: manifest["museums"][0]["documents"].pop(),
        lambda manifest: manifest["museums"][0]["documents"][0].update(mime_type="application/docx"),
        lambda manifest: manifest["museums"][1].update(token_env="ALPHA_TOKEN"),
        lambda manifest: manifest["museums"][0]["documents"][0].update(size_bytes=999),
        lambda manifest: manifest["museums"][0]["documents"][2].update(collision_sha256=manifest["museums"][0]["documents"][2]["sha256"]),
    ],
)
def test_manifest_rejects_mutable_or_incoherent_fixture(tmp_path, mutate):
    manifest = _manifest(tmp_path)
    mutate(manifest)

    with pytest.raises(ManifestError):
        load_config(manifest)


class _FakeProvider:
    def __init__(self, overwrite_on_conflict=False, accept_duplicate=False, allow_cross_dataset_id=False):
        self.lock = threading.Lock()
        self.documents = {}
        self.chunks = {}
        self.parse_calls = []
        self.overwrite_on_conflict = overwrite_on_conflict
        self.accept_duplicate = accept_duplicate
        self.allow_cross_dataset_id = allow_cross_dataset_id

    @staticmethod
    def _key(museum_name, document):
        return museum_name, document.dataset_id, document.document_id

    def upload(self, museum_name, document, content):
        key = self._key(museum_name, document)
        with self.lock:
            identity_collision = key in self.documents or (not self.allow_cross_dataset_id and any(existing[2] == document.document_id for existing in self.documents))
            if identity_collision:
                if self.overwrite_on_conflict:
                    self.documents[key] = content
                return 200, {"code": 0 if self.accept_duplicate else 100, "message": "not evidence"}
            self.documents[key] = content
            return 200, {"code": 0, "data": [{"id": document.document_id}]}

    def list_document(self, museum_name, document):
        key = self._key(museum_name, document)
        content = self.documents.get(key)
        docs = []
        if content is not None:
            chunks = self.chunks.get(key, [])
            docs.append(
                {
                    "id": document.document_id,
                    "dataset_id": document.dataset_id,
                    "run": "DONE" if chunks else "UNSTART",
                    "progress": 1 if chunks else 0,
                    "chunk_count": len(chunks),
                }
            )
        return 200, {"code": 0, "data": {"total": len(docs), "docs": docs}}

    def download(self, museum_name, document):
        return 200, self.documents.get(self._key(museum_name, document), b"")

    def parse(self, museum_name, dataset_id, document_ids):
        self.parse_calls.append((museum_name, dataset_id, tuple(document_ids)))
        for document_id in document_ids:
            matching = [key for key in self.documents if key == (museum_name, dataset_id, document_id)]
            if not matching:
                return 200, {"code": 100, "message": "not evidence"}
            self.chunks[matching[0]] = [{"id": f"chunk-{document_id}"}]
        return 200, {"code": 0}

    def list_chunks(self, museum_name, document):
        chunks = self.chunks.get(self._key(museum_name, document), [])
        return 200, {"code": 0, "data": {"total": len(chunks), "chunks": chunks}}


def _run(manifest, provider):
    return ConformanceRunner(
        load_config(manifest),
        provider.upload,
        provider.list_document,
        provider.download,
        provider.parse,
        provider.list_chunks,
        sleep=lambda _: None,
    ).run()


def test_matrix_passes_and_emits_only_content_free_facts(tmp_path):
    manifest = _manifest(tmp_path)
    provider = _FakeProvider()

    result = _run(manifest, provider)

    assert result["status"] == "PASSED"
    assert result["clean_namespace_proof"] is True
    assert result["case_count"] == 30
    assert result["failed_case_count"] == 0
    assert len(provider.documents) == 8
    assert len(provider.parse_calls) == 4
    rendered = str(result)
    assert "ALPHA_TOKEN" not in rendered
    assert "MM-C04-ALPHA-A" not in rendered
    assert "different synthetic bytes" not in rendered
    assert str(tmp_path) not in rendered


@pytest.mark.parametrize(
    "provider",
    [
        _FakeProvider(accept_duplicate=True),
        _FakeProvider(overwrite_on_conflict=True),
        _FakeProvider(allow_cross_dataset_id=True),
    ],
)
def test_duplicate_success_or_overwrite_fails_closed(tmp_path, provider):
    result = _run(_manifest(tmp_path), provider)

    assert result["status"] == "FAILED"
    assert result["failed_case_count"] > 0


def test_dirty_namespace_stops_before_any_provider_mutation(tmp_path):
    manifest = _manifest(tmp_path)
    config = load_config(deepcopy(manifest))
    provider = _FakeProvider()
    existing = config.museums[0].document("A")
    provider.documents[(config.museums[0].name, existing.dataset_id, existing.document_id)] = b"old"

    result = _run(manifest, provider)

    assert result["status"] == "FAILED"
    assert result["clean_namespace_proof"] is False
    assert len(provider.documents) == 1
    assert provider.parse_calls == []

"""Local parser and build guards; no network and no installed-package mutation."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools/musemind_patches/werkzeug_multipart.py"
SPEC = importlib.util.spec_from_file_location("multipart_patch", SCRIPT)
patcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(patcher)


def original_source():
    distribution = importlib.metadata.distribution("werkzeug")
    assert distribution.version == patcher.VERSION
    value = Path(distribution.locate_file("werkzeug/sansio/multipart.py")).read_bytes()
    if patcher.sha256(value) == patcher.PATCHED_SHA256:
        # Tests also run after a build applied the patch. Recover the exact baseline
        # in memory from the checked diff; never alter the installed package.
        hunks = patcher.PATCH_PATH.read_text().split("@@")
        for body in hunks[2::2]:
            lines = body.splitlines(keepends=True)[1:]
            before = "".join(line[1:] for line in lines if line.startswith((" ", "-"))).encode()
            after = "".join(line[1:] for line in lines if line.startswith((" ", "+"))).encode()
            assert value.count(after) == 1
            value = value.replace(after, before, 1)
    assert patcher.sha256(value) == patcher.ORIGINAL_SHA256
    return value


def test_exact_apply_verify_idempotency_and_hardlink_cache_isolation(tmp_path):
    cache = tmp_path / "cache.py"
    target = tmp_path / "multipart.py"
    cache.write_bytes(original_source())
    os.link(cache, target)
    assert patcher.apply_or_verify(target, "3.1.5", verify=False) == "APPLIED"
    assert patcher.sha256(target.read_bytes()) == patcher.PATCHED_SHA256
    assert patcher.sha256(cache.read_bytes()) == patcher.ORIGINAL_SHA256
    before = target.stat()
    assert patcher.apply_or_verify(target, "3.1.5", verify=False) == "VERIFIED"
    assert patcher.apply_or_verify(target, "3.1.5", verify=True) == "VERIFIED"
    assert target.stat().st_ino == before.st_ino
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cache.py", "multipart.py"]


@pytest.mark.parametrize("case", ["verify-original", "wrong-version", "wrong-source", "wrong-patch"])
def test_preflight_rejection_never_writes(tmp_path, case):
    target = tmp_path / "multipart.py"
    target.write_bytes(b"unrecognized-source" if case == "wrong-source" else original_source())
    patch_path = patcher.PATCH_PATH
    if case == "wrong-patch":
        patch_path = tmp_path / "wrong.patch"
        patch_path.write_bytes(patcher.PATCH_PATH.read_bytes() + b"\n")
    before = target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns
    with pytest.raises(patcher.PatchError):
        patcher.apply_or_verify(target, "3.1.8" if case == "wrong-version" else "3.1.5", verify=case == "verify-original", patch_path=patch_path)
    assert (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns) == before
    assert not list(tmp_path.glob(".musemind-multipart-*"))


def test_atomic_replace_failure_preserves_original_and_cleans_temporary(tmp_path, monkeypatch):
    target = tmp_path / "multipart.py"
    target.write_bytes(original_source())

    def fail_replace(*_):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(patcher.os, "replace", fail_replace)
    with pytest.raises(OSError):
        patcher.apply_or_verify(target, "3.1.5", verify=False)
    assert patcher.sha256(target.read_bytes()) == patcher.ORIGINAL_SHA256
    assert list(tmp_path.iterdir()) == [target]


def test_concurrent_source_change_is_preserved_and_not_overwritten(tmp_path, monkeypatch):
    target = tmp_path / "multipart.py"
    target.write_bytes(original_source())
    real_chmod = patcher.os.chmod

    def concurrent_change(path, mode):
        real_chmod(path, mode)
        target.write_bytes(b"independent-concurrent-change")

    monkeypatch.setattr(patcher.os, "chmod", concurrent_change)
    with pytest.raises(patcher.PatchError, match="PACKAGE_SOURCE_CHANGED"):
        patcher.apply_or_verify(target, "3.1.5", verify=False)
    assert target.read_bytes() == b"independent-concurrent-change"
    assert list(tmp_path.iterdir()) == [target]


def test_patched_digest_is_checked_before_filesystem_mutation(tmp_path, monkeypatch):
    target = tmp_path / "multipart.py"
    target.write_bytes(original_source())
    monkeypatch.setattr(patcher, "PATCHED_SHA256", "0" * 64)
    with pytest.raises(patcher.PatchError, match="PATCHED_DIGEST_MISMATCH"):
        patcher.apply_or_verify(target, "3.1.5", verify=False)
    assert patcher.sha256(target.read_bytes()) == patcher.ORIGINAL_SHA256
    assert list(tmp_path.iterdir()) == [target]


def test_baseline_recovery_when_the_test_environment_is_already_patched(tmp_path, monkeypatch):
    original = original_source()
    target = tmp_path / "multipart.py"
    target.write_bytes(patcher.patched_source(original, patcher.PATCH_PATH.read_bytes()))

    class Distribution:
        version = "3.1.5"

        def locate_file(self, _):
            return target

    monkeypatch.setattr(importlib.metadata, "distribution", lambda _: Distribution())
    assert original_source() == original


def test_real_quart_fragmentation_and_form_contract_on_temporary_package(tmp_path):
    distribution = importlib.metadata.distribution("werkzeug")
    package = Path(distribution.locate_file("werkzeug"))
    temporary_package = tmp_path / "werkzeug"
    shutil.copytree(package, temporary_package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    metadata_name = f"werkzeug-{distribution.version}.dist-info"
    shutil.copytree(Path(distribution.locate_file(metadata_name)), tmp_path / metadata_name)
    target = temporary_package / "sansio/multipart.py"
    target.write_bytes(original_source())
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for arguments, expected_code, expected_status in [(["--verify"], 1, "STOPPED"), ([], 0, "APPLIED"), (["--verify"], 0, "VERIFIED"), ([], 0, "VERIFIED")]:
        command = subprocess.run([sys.executable, "-B", str(SCRIPT), *arguments], env=environment, capture_output=True, text=True, timeout=15, check=False)
        assert command.returncode == expected_code, command.stdout + command.stderr
        assert json.loads(command.stdout)["status"] == expected_status
    result = subprocess.run([sys.executable, "-B", str(Path(__file__)), "--runtime-probe"], env=environment, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["single_file_cases"] > 6000
    assert report["multiple_field_file_cases"] > 5000
    assert report["limit_denials"] == 4
    assert report["source_sha256"] == patcher.PATCHED_SHA256


class Body:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for chunk in self.chunks:
            yield chunk


async def parser_regressions():
    """Exercise the actual installed Quart parser; never replace its decoder."""
    import httpx
    import werkzeug.sansio.multipart
    from quart.formparser import MultiPartParser
    from werkzeug.exceptions import RequestEntityTooLarge

    source_digest = patcher.sha256(await asyncio.to_thread(Path(werkzeug.sansio.multipart.__file__).read_bytes))
    assert source_digest == patcher.PATCHED_SHA256
    boundary = b"local-audit-boundary-0123456789abcd"
    headers = {"content-type": "multipart/form-data; boundary=" + boundary.decode()}
    payloads = [
        b"",
        b"X",
        b"X\n",
        b"X\r",
        b"X\r\n",
        b"\r\nX\r\n",
        bytes(range(256)),
        b"\r\n" * 50,
        b"\x00\xff" * 50,
        b"Synthetic multipart preservation.\n",
        b"prefix\r\n--" + boundary + b"-Xsuffix",
        b"prefix\r\n--" + boundary + b"Xsuffix",
        b"prefix\r\n--" + boundary[:-1],
    ]
    single_count = multi_count = denials = 0
    randomizer = random.Random(0)

    async def parse(chunks, expected_fields, expected_files):
        form, files = await MultiPartParser().parse(Body(chunks), boundary, sum(map(len, chunks)))
        assert dict(form.lists()) == expected_fields
        actual = {name: [(item.filename, item.read(), item.content_type) for item in values] for name, values in files.lists()}
        assert actual == expected_files

    for payload in payloads:
        request = httpx.Request("POST", "https://local-test.invalid", headers=headers, data={"document_id": "a" * 32}, files={"file": ("a.txt", payload, "text/plain")})
        native = list(request.stream)
        wire = b"".join(native)
        fields = {"document_id": ["a" * 32]}
        files = {"file": [("a.txt", payload, "text/plain")]}
        await parse(native, fields, files)
        single_count += 1
        for split in range(1, len(wire)):
            await parse([wire[:split], wire[split:]], fields, files)
            single_count += 1
        for size in range(1, 65):
            await parse([wire[i : i + size] for i in range(0, len(wire), size)], fields, files)
            single_count += 1
        for _ in range(100):
            chunks = []
            offset = 0
            while offset < len(wire):
                size = randomizer.randint(1, 17)
                chunks.append(wire[offset : offset + size])
                offset += size
            await parse(chunks, fields, files)
            single_count += 1

    request = httpx.Request(
        "POST",
        "https://local-test.invalid",
        headers=headers,
        data={"document_id": "a" * 32, "repeated": ["first", "second"], "empty": "", "unicode": "Caffè"},
        files=[("file", ("one.txt", b"first\r\n", "text/plain")), ("file", ("two.bin", bytes(range(256)), "application/octet-stream")), ("another", ("empty.bin", b"", "application/octet-stream"))],
    )
    native = list(request.stream)
    wire = b"".join(native)
    fields = {"document_id": ["a" * 32], "repeated": ["first", "second"], "empty": [""], "unicode": ["Caffè"]}
    files = {"file": [("one.txt", b"first\r\n", "text/plain"), ("two.bin", bytes(range(256)), "application/octet-stream")], "another": [("empty.bin", b"", "application/octet-stream")]}
    await parse(native, fields, files)
    multi_count += 1
    for variant in (wire, wire[:-2], wire + b"local-synthetic-epilogue", wire.replace(boundary + b"\r\n", boundary + b" \t \t \r\n")):
        for split in range(1, len(variant)):
            await parse([variant[:split], variant[split:]], fields, files)
            multi_count += 1
        for size in range(1, 65):
            await parse([variant[i : i + size] for i in range(0, len(variant), size)], fields, files)
            multi_count += 1
    for options in ({"max_form_parts": 2}, {"max_form_memory_size": 16}):
        for chunks in ([wire], [wire[i : i + 1] for i in range(len(wire))]):
            with pytest.raises(RequestEntityTooLarge):
                await MultiPartParser(**options).parse(Body(chunks), boundary, len(wire))
            denials += 1
    return {"single_file_cases": single_count, "multiple_field_file_cases": multi_count, "limit_denials": denials, "source_sha256": source_digest}


if __name__ == "__main__" and sys.argv[1:] == ["--runtime-probe"]:
    print(json.dumps(asyncio.run(parser_regressions()), sort_keys=True))

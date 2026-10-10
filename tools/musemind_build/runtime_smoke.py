"""Read-only candidate image provenance and actual Quart multipart regressions."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import zipfile
from pathlib import Path

SDK_SOURCE_MEMBER = "ragflow_sdk/ragflow.py"
SDK_DATASET_CONTRACT_V3 = "musemind.ragflow-dataset-create-or-adopt/v3"


def verify_sdk(sdk_dist: Path, expected_files: dict[str, str]) -> dict:
    """Verify packaged approved bytes and the explicit v3 entry point without imports."""
    wheels = list(sdk_dist.glob("ragflow_sdk-*.whl"))
    if len(wheels) != 1:
        raise ValueError("SDK_CARDINALITY_INVALID")
    if set(expected_files) != {SDK_SOURCE_MEMBER}:
        raise ValueError("SDK_SOURCE_EXPECTATION_INVALID")
    with zipfile.ZipFile(wheels[0]) as wheel:
        if wheel.namelist().count(SDK_SOURCE_MEMBER) != 1:
            raise ValueError("SDK_SOURCE_CARDINALITY_INVALID")
        source = wheel.read(SDK_SOURCE_MEMBER)
    if hashlib.sha256(source).hexdigest() != expected_files[SDK_SOURCE_MEMBER]:
        raise ValueError("SDK_SOURCE_MISMATCH")

    classes = [node for node in ast.parse(source).body if isinstance(node, ast.ClassDef) and node.name == "RAGFlow"]
    methods = [node for cls in classes for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "create_or_adopt_dataset_exact"]
    if len(classes) != 1 or len(methods) != 1:
        raise ValueError("SDK_DATASET_ENTRY_POINT_INVALID")
    method = methods[0]
    keyword_defaults = dict(zip((arg.arg for arg in method.args.kwonlyargs), method.args.kw_defaults))
    default = keyword_defaults.get("contract_schema")
    if not isinstance(default, ast.Constant) or default.value is not None:
        raise ValueError("SDK_EXPLICIT_CONTRACT_KEYWORD_INVALID")
    if not any(isinstance(node, ast.Constant) and node.value == SDK_DATASET_CONTRACT_V3 for node in ast.walk(method)):
        raise ValueError("SDK_DATASET_V3_MISSING")
    return {
        "sdk_sha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest(),
        "sdk_source_files": expected_files,
        "sdk_dataset_contract_schema": SDK_DATASET_CONTRACT_V3,
    }


class Body:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for chunk in self.chunks:
            yield chunk


async def parser_regressions() -> int:
    import httpx
    from quart.formparser import MultiPartParser

    boundary = b"musemind-candidate-synthetic-boundary"
    payloads = [b"", b"X", b"X\n", b"X\r", b"X\r\n", b"\r\nX\r\n", bytes(range(256)), b"\r\n" * 50, b"\x00\xff" * 50, b"prefix\r\n--" + boundary + b"-Xsuffix", b"prefix\r\n--" + boundary[:-1]]
    count = 0
    for payload in payloads:
        request = httpx.Request(
            "POST",
            "https://synthetic.invalid",
            headers={"content-type": "multipart/form-data; boundary=" + boundary.decode()},
            data={"document_id": "a" * 32, "empty": ""},
            files=[("file", ("one.bin", payload, "application/octet-stream")), ("file", ("two.txt", b"second\r\n", "text/plain"))],
        )
        native = list(request.stream)
        raw = b"".join(native)
        variants = [("native", native), ("coalesced", [raw])]
        for wire in (raw, raw[:-2], raw + b"synthetic-epilogue", raw.replace(boundary + b"\r\n", boundary + b" \t \r\n")):
            for size in (1, 2, 3, 7, 64):
                variants.append(("fragmented", [wire[offset : offset + size] for offset in range(0, len(wire), size)]))
        for _, chunks in variants:
            form, files = await MultiPartParser().parse(Body(chunks), boundary, sum(map(len, chunks)))
            if dict(form.lists()) != {"document_id": ["a" * 32], "empty": [""]}:
                raise ValueError("MULTIPART_FIELDS_CHANGED")
            actual = [(item.filename, item.read(), item.content_type) for item in files.getlist("file")]
            if actual != [("one.bin", payload, "application/octet-stream"), ("two.txt", b"second\r\n", "text/plain")]:
                raise ValueError("MULTIPART_BYTES_CHANGED")
            for _, values in files.lists():
                for item in values:
                    item.close()
            count += 1
    return count


def main() -> int:
    try:
        expected = json.loads(Path(sys.argv[1]).read_bytes())
        root = Path("/ragflow")
        for relative, digest in expected["files"].items():
            path = root / relative
            if not path.resolve().is_relative_to(root) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("RUNTIME_SOURCE_MISMATCH")
        if (root / "VERSION").read_text().strip() != expected["version"]:
            raise ValueError("RUNTIME_VERSION_MISMATCH")
        result = subprocess.run([sys.executable, "/ragflow/tools/musemind_patches/werkzeug_multipart.py", "--verify"], capture_output=True, check=False, timeout=30)
        if result.returncode != 0:
            raise ValueError("RUNTIME_PATCH_INVALID")
        patch = json.loads(result.stdout)
        if patch.get("status") != "VERIFIED":
            raise ValueError("RUNTIME_PATCH_INVALID")
        cases = asyncio.run(parser_regressions())
        sdk = verify_sdk(root / "sdk-dist", expected["sdk_files"])
        print(
            json.dumps(
                {
                    "schema": "musemind.ragflow-candidate-runtime-smoke/v1",
                    "status": "PASSED",
                    "source_commit": expected["source_commit"],
                    "quart_parser_cases": cases,
                    "source_file_count": len(expected["files"]),
                    "python_version": platform.python_version(),
                    "dependency_versions": {name: importlib.metadata.version(name) for name in ("anyio", "fsspec", "pyjwt", "pypdf", "urllib3", "nltk")},
                    "werkzeug_version": importlib.metadata.version("werkzeug"),
                    "werkzeug_source_sha256": patch["source_sha256"],
                    **sdk,
                }
            )
        )
        return 0
    except Exception:
        print(json.dumps({"status": "STOPPED", "code": "RUNTIME_SMOKE_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

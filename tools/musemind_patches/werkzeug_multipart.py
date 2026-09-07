"""Apply or verify the exact build-time Werkzeug multipart correction.

No runtime hook and no package-cache writes. Atomic replacement breaks uv hardlinks.
The patch derives from Pallets' BSD-3-Clause source; see LICENSE-Werkzeug.txt.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import stat
import tempfile
from pathlib import Path

VERSION = "3.1.5"
ORIGINAL_SHA256 = "b8b74b1fb7df7a515745a5eedbf5972bffd7d75b80edb1c2b2a7f22deaea192a"
PATCHED_SHA256 = "122f1368000466008e105033bbf7cb65b390f4791c539c67ac0b36b58c72d4b5"
PATCH_SHA256 = "7187bfa31a7989da889cc3555e8655f8cec9fb2ffd7c6408698ae6b625c0f4bf"
PATCH_PATH = Path(__file__).with_name("werkzeug-multipart.patch")
HUNK = re.compile(r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n")


class PatchError(Exception):
    """Content-free failure code."""


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def patched_source(original: bytes, patch: bytes) -> bytes:
    if sha256(original) != ORIGINAL_SHA256 or sha256(patch) != PATCH_SHA256:
        raise PatchError("SOURCE_OR_PATCH_DIGEST_MISMATCH")
    source = original.decode("utf-8").splitlines(keepends=True)
    diff = patch.decode("utf-8").splitlines(keepends=True)
    if diff[:2] != ["--- a/werkzeug/sansio/multipart.py\n", "+++ b/werkzeug/sansio/multipart.py\n"]:
        raise PatchError("PATCH_HEADER_INVALID")
    result: list[str] = []
    source_offset = 0
    offset = 2
    while offset < len(diff):
        match = HUNK.fullmatch(diff[offset])
        if match is None:
            raise PatchError("PATCH_HUNK_INVALID")
        old_start, old_count, new_start, new_count = map(int, match.groups())
        if old_start - 1 < source_offset:
            raise PatchError("PATCH_HUNK_ORDER_INVALID")
        result.extend(source[source_offset : old_start - 1])
        source_offset = old_start - 1
        if len(result) != new_start - 1:
            raise PatchError("PATCH_POSITION_INVALID")
        offset += 1
        removed = added = 0
        while offset < len(diff) and not diff[offset].startswith("@@ "):
            line = diff[offset]
            if line[0] not in " +-":
                raise PatchError("PATCH_LINE_INVALID")
            if line[0] in " -":
                if source_offset >= len(source) or source[source_offset] != line[1:]:
                    raise PatchError("PATCH_CONTEXT_MISMATCH")
                source_offset += 1
                removed += 1
            if line[0] in " +":
                result.append(line[1:])
                added += 1
            offset += 1
        if (removed, added) != (old_count, new_count):
            raise PatchError("PATCH_LINE_COUNT_INVALID")
    result.extend(source[source_offset:])
    value = "".join(result).encode("utf-8")
    if sha256(value) != PATCHED_SHA256:
        raise PatchError("PATCHED_DIGEST_MISMATCH")
    return value


def apply_or_verify(path: Path, version: str, *, verify: bool, patch_path: Path = PATCH_PATH) -> str:
    if version != VERSION:
        raise PatchError("WERKZEUG_VERSION_MISMATCH")
    if sha256(patch_path.read_bytes()) != PATCH_SHA256:
        raise PatchError("PATCH_DIGEST_MISMATCH")
    information = path.lstat()
    if not stat.S_ISREG(information.st_mode):
        raise PatchError("PACKAGE_SOURCE_NOT_REGULAR")
    original = path.read_bytes()
    checksum = sha256(original)
    if checksum == PATCHED_SHA256:
        return "VERIFIED"
    if checksum != ORIGINAL_SHA256:
        raise PatchError("PACKAGE_SOURCE_DIGEST_MISMATCH")
    if verify:
        raise PatchError("PACKAGE_PATCH_NOT_APPLIED")
    value = patched_source(original, patch_path.read_bytes())
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".musemind-multipart-", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, stat.S_IMODE(information.st_mode))
        # Refuse an unexpected concurrent change before the one atomic mutation.
        current = path.lstat()
        if (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_size) != (information.st_dev, information.st_ino, information.st_mtime_ns, information.st_size) or sha256(
            path.read_bytes()
        ) != ORIGINAL_SHA256:
            raise PatchError("PACKAGE_SOURCE_CHANGED")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    if sha256(path.read_bytes()) != PATCHED_SHA256:
        raise PatchError("PACKAGE_PATCH_READBACK_FAILED")
    return "APPLIED"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Read-only: require the exact patched source.")
    args = parser.parse_args()
    try:
        distribution = importlib.metadata.distribution("werkzeug")
        path = Path(distribution.locate_file("werkzeug/sansio/multipart.py"))
        outcome = apply_or_verify(path, distribution.version, verify=args.verify)
        print(json.dumps({"schema": "musemind.werkzeug-multipart-patch/v1", "status": outcome, "werkzeug_version": VERSION, "source_sha256": PATCHED_SHA256}))
        return 0
    except PatchError as error:
        print(json.dumps({"status": "STOPPED", "code": str(error)}))
    except (OSError, UnicodeError, ValueError, importlib.metadata.PackageNotFoundError):
        print(json.dumps({"status": "STOPPED", "code": "PACKAGE_PATCH_FAILURE"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

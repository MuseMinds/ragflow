# Exact Werkzeug multipart build correction

Apply after installing the locked Python graph, before starting Python workloads:

```sh
.venv/bin/python tools/musemind_patches/werkzeug_multipart.py
.venv/bin/python tools/musemind_patches/werkzeug_multipart.py --verify
```

The first command applies once or verifies an already exact patched file. The
second is read-only and fails on unpatched, unknown or mismatched source. Both
require Werkzeug 3.1.5 and the checked patch artifact. The applicator checks the
complete upstream file hash, patch hash, and final file hash. It writes a new
sibling file and atomically replaces the installed path, preserving permissions
and breaking uv cache hardlinks without changing their other links. Run only in
the intended build/local environment; there is no runtime monkeypatch or fallback.

Copy this entire directory into the image for patch provenance and verification.
Retain the installed Werkzeug license as well as [the reproduced upstream license](LICENSE-Werkzeug.txt).
The patch derives from Pallets Werkzeug, BSD-3-Clause. Upstream source:
`https://github.com/pallets/werkzeug/blob/3.1.5/src/werkzeug/sansio/multipart.py`.
The two `data_start` offset corrections also occur in upstream 3.1.8; that version
alone still emits a trailing CR for an incompletely received closing delimiter.

The precise [diff](werkzeug-multipart.patch) preserves the part separator offset,
retains an incomplete closing `--` suffix, and retains only exact incomplete
boundaries with permitted horizontal whitespace. It does not trim file content,
change encodings, bypass form limits, or modify field/file collection semantics.
Source ownership: `MultipartDecoder._parse_data` and
`MultipartDecoder._last_partial_boundary_index` in the pinned dependency.

| Artifact | SHA-256 |
|---|---|
| Original `werkzeug/sansio/multipart.py` | `b8b74b1fb7df7a515745a5eedbf5972bffd7d75b80edb1c2b2a7f22deaea192a` |
| Exact patch | `7187bfa31a7989da889cc3555e8655f8cec9fb2ffd7c6408698ae6b625c0f4bf` |
| Patched source | `122f1368000466008e105033bbf7cb65b390f4791c539c67ac0b36b58c72d4b5` |

Focused tests use temporary package copies and actual Quart parsing, with no
network or modification of the test runner's installed dependency:

```sh
.venv/bin/python -B -m pytest --noconftest -p no:cacheprovider test/unit_test/tools/test_musemind_multipart.py
```

Local tests and `--verify` are not provider qualification. Apply the repository's
patch-ledger, immutable image, official-image reproduction, and dependency-scoped
qualification gates before replacing a frozen deployed digest.

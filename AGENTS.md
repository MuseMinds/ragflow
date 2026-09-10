# Governed MuseMind RAGFlow fork instructions

## Scope and governance

This is a frozen provider fork, not MuseMind application authority. PostgreSQL/application S3 and
the contracts in `../MuseMindArchitecture` remain authoritative. Before changing this repository,
read `README.md`, `MUSEMIND_PATCHES.md` and the canonical RAG/provider-generation contracts.

- Pin a stable upstream release and keep the MuseMind delta minimal, explicit and reproducible.
- Do not mix upstream sync, dependency refresh and product behavior in one change. Record upstream
  base, patch purpose, files, tests and qualification impact in `MUSEMIND_PATCHES.md`.
- Use a small `mm/*` branch for an unavoidable patch. Prefer configuration or
  `musemind-rag-integration` adaptation when the provider need not change.
- Never add application tenant, visit, publication or retrieval authority to RAGFlow. The provider
  must not choose latest/default scope or receive broad application credentials.
- Remove obsolete compatibility paths/comments rather than preserving parallel implementations.
  Keep local changes focused and avoid unrelated upstream refactors.

## MuseMind provider invariants

- Provider/model/request changes create a new immutable generation; key rotation does not. Never
  fall back to another embedder/model on failure.
- Preserve the qualified Jina generation as rollback when using the Gemini v2 generation. Dataset
  creation uses only the strict generation projection and exact composite IDs; vectors from
  different generations never mix.
- Do not emit source content, image bytes, prompts, chunks, credentials or provider payloads in
  logs, traces, callbacks, errors or evidence.
- Apply and verify every locked runtime correction exactly as documented in the patch ledger.
  Version/hash mismatch fails closed; never edit a shared dependency cache to bypass it.
- Qualified images are immutable `linux/amd64` artifacts. Environment promotion mirrors the exact
  digest without rebuild and verifies destination digest equality.

## Develop and Production delivery

Normal fork qualification and deployment target Develop; the root monorepo `develop` pins the
verified fork commit and immutable digest. Production receives only the release-pinned qualified
digest through `../procedura-rilascio.md`. A fork branch or image publication is not independent
Production release authority. Any new Production provider generation or changed semantics requires
the canonical impact-specific qualification and decision route.

After every PR merge, delete its source branch remotely and locally once no active task or worktree
uses it; preserve long-lived and still-active release or hotfix branches.

## Working areas

- `api/`, `rag/`, `agent/`, `graphrag/`: Python backend and retrieval services.
- `internal/`: Go ingestion, parser, service, storage, router and server code.
- `web/`: frontend.
- `docker/`: Compose/runtime packaging.
- `tools/musemind_patches/` and `MUSEMIND_PATCHES.md`: governed local patch machinery/ledger.

Use the nearest owning path and one implementation route. In actively refactored Go packages,
remove transitional wrappers and stale comments rather than extending them.

## Validation

Run the narrowest relevant test first. Use the repository wrappers for Go because they supply
required CGO/native libraries; do not default to raw `go test` or `go build`.

Backend:

```bash
uv sync --python 3.13 --all-extras
uv run python3 ragflow_deps/download_deps.py
python tools/musemind_patches/werkzeug_multipart.py
python tools/musemind_patches/werkzeug_multipart.py --verify
uv run pytest
ruff check
ruff format --check
```

Frontend:

```bash
cd web
npm install
npm run build
npm run lint
npm run test
npm run type-check
```

Go:

```bash
uv run ragflow_deps/download_deps.py
bash build.sh --test ./path/to/package/...
bash build.sh --go
```

Any patch that affects provider semantics requires the dependency-scoped qualification named by
the architecture/patch ledger before its digest can replace the frozen one.

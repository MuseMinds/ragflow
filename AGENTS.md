# RAGFlow Instructions

Use this file as the local operating guide for the current codebase. Prefer the code and the current CLAUDE.md over any older convention or remembered project shape.

## Core stance
- The MuseMind pilot runs the currently qualified `musemind` commit/digest as a frozen dependency.
  Do not add a MuseMind patch unless the exact behavior is reproduced on the compatible official
  upstream image and cannot be safely normalized at the private RAG Integration proxy. Pilot-ASAP
  work belongs outside this repository by default.
- Treat legacy code as liability, not as a compatibility target.
- Prefer deletion over shims, deprecated branches, wrapper APIs, and dual-track migration notes.
- If old and new implementations coexist, converge to one path unless an external contract forces compatibility.
- Remove dead tests, commented-out code, stale docs, and "move later" notes instead of preserving them.
- Reduce public surface area when a helper can be made private or internal.
- Keep refactors centered on the owning abstraction, not on adjacent compatibility layers.

## Current stack
- Backend: Python 3.13+, Quart-based API server, Peewee ORM, async workers.
- Frontend: React + TypeScript + Vite in `web/`.
- Go: the repository also has a substantial Go module for servers, ingestion, parser/runtime, CLI, and supporting services.
- Runtime services commonly include MySQL/PostgreSQL, Redis, MinIO, and Elasticsearch/Infinity/OpenSearch depending on configuration.

## Code layout to expect
- `api/`: Python API server entrypoints, blueprints, services, and database code.
- `rag/`: ingestion, retrieval, LLM integration, and graph RAG logic.
- `deepdoc/`: parsing and OCR.
- `agent/`: workflow canvas, components, tools, and templates.
- `cmd/`: Go entrypoints. `ragflow_main` is the main server/admin/ingestor binary surface; `ragflow-cli` is the CLI entrypoint.
- `internal/`: main Go application code. Important subtrees:
- `internal/agent/`: Go agent runtime, canvas execution, components, tool bindings, workflow helpers.
- `internal/cli/`: CLI parsing, HTTP transport, command execution, response formatting.
- `internal/dao/`: Go data-access layer and persistence-facing helpers.
- `internal/deepdoc/`: Go DeepDOC integrations, especially native-backed PDF/DOCX parsing.
- `internal/engine/`: search/index backends such as Elasticsearch and Infinity.
- `internal/entity/`: shared Go entities and model definitions.
- `internal/handler/`: HTTP handlers and route-facing request logic.
- `internal/ingestion/`: Go ingestion pipeline, canvas adapter, components, wiring, service orchestration.
- `internal/ingestion/component/`: stage implementations such as file/parser/chunker/tokenizer/extractor.
- `internal/ingestion/pipeline/`: DSL translation, canvas-driven execution, checkpoints, resume/run logic.
- `internal/parser/`: parser and chunk libraries used by ingestion and other Go paths.
- `internal/parser/parser/`: typed parse-result parsers for markdown/html/pdf/docx/xlsx/text and related families.
- `internal/parser/chunk/`: chunk operator library and DSL/typed execution helpers.
- `internal/service/`: higher-level business services used by handlers and server flows.
- `internal/storage/`: storage backends and in-memory test doubles.
- `internal/router/`: HTTP route registration.
- `internal/server/`: server bootstrap/config wiring.
- `internal/cpp/`: C++ sources used by native-backed Go features.
- `web/`: frontend application.
- `docker/`: local and production compose files.
- `sdk/` and `test/`: SDK and automated tests.

## Go-specific rules
- Treat `internal/ingestion`, `internal/parser`, and `internal/deepdoc` as actively refactored code. Prefer collapsing duplicate paths over preserving transitional wrappers.
- Do not add or preserve deprecated Go APIs just to ease migration inside the repo.
- Remove commented-out Go code instead of leaving recovery notes in place.
- Keep package comments and doc comments aligned with the current runtime path, not with migration history.

## Working rules
- Before editing, inspect the nearest code path that actually owns the behavior.
- Keep changes small and local unless the task is explicitly a broader refactor.
- Prefer one implementation path instead of preserving old and new versions side by side.
- Preserve behavior with focused tests when the behavior is still valid; do not keep tests that protect obsolete behavior.
- If a surface is only there for compatibility, remove it unless the user asks to keep it.
- Do not add new compatibility wording in comments or docs.
- Mirror a qualified MuseMind runtime image to environment ECR without rebuilding it, and fail the
  workflow unless the destination manifest digest exactly equals the reviewed source digest.
- Any unavoidable pilot patch remains a small `mm/*` branch change with focused tests, patch-ledger
  update and full dependency-scoped qualification before its digest can replace the frozen one.
- ADR-0037's first API generation is the exact Jina v3 request contract recorded in
  `MUSEMIND_PATCHES.md`. Reconcile its service-tenant key only through the schema-aware one-shot;
  provider/model/request changes require a new immutable generation, key rotation does not, and no
  failure may fall back to TEI/Builtin/another embedder.

## Commands
### Backend
```bash
uv sync --python 3.13 --all-extras
uv run python3 ragflow_deps/download_deps.py
docker compose -f docker/docker-compose-base.yml up -d
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh
uv run pytest
ruff check
ruff format
```

### Frontend
```bash
cd web
npm install
npm run dev
npm run build
npm run lint
npm run test
npm run type-check
```

### Go
```bash
uv run ragflow_deps/download_deps.py
bash build.sh --test ./path/to/package/...
bash build.sh --go
# or build specific binaries:
bash build.sh --all
```

## Validation preference
- Run the narrowest relevant test, lint, or build command after a change.
- For backend changes, prefer targeted pytest or ruff checks over full-suite runs.
- For frontend changes, prefer the touched-package lint, type-check, or test command.
- For Go changes, prefer package-scoped `bash build.sh --test ...` first.
- Do not default to raw `go test`, `go build`, or IDE Run/Debug for Go in this repo. They often miss the required CGO flags and native static libraries (`office_oxide`, `pdfium-static`, `pdf_oxide`) that `build.sh` wires correctly.
- If Go native builds fail, inspect `build.sh` and `internal/development.md` before changing code. Common environment issues are missing downloaded native deps and missing `lld` on Linux.

## Default review checklist
- Remove instead of retaining `deprecated`, `legacy`, or compatibility-only code.
- Collapse duplicate implementations to one path.
- Drop stale comments and documentation that describe a superseded design.
- Keep exported APIs only when the current code actually needs them.

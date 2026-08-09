# MuseMind patch ledger

This ledger is part of the immutable RAGFlow bundle evidence required by MuseMind ADR-0032. A
qualification artifact must replace every `PENDING` value below with the exact merged fork commit,
OCI/SDK digest, test result and date that were actually exercised. A source diff or successful unit
test does not by itself qualify a runtime bundle.

## Upstream baseline

- Repository: `infiniflow/ragflow`
- Stable tag: `v0.26.4`
- Upstream commit: `cb93883f3f8c975eecb2fed81210effeb3bdb06f`
- MuseMind base commit: `9a8edce43f1b5424166ce83d54c2da927cf0b9f0`
- MuseMind development branch: `mm/c01-runtime-remediation`
- MuseMind source patch commit: `802ef6aac725e0eda53207d7f2fc2a9adbe16874`
- MuseMind pull request: `MuseMinds/ragflow#2`
- Merged provider-contract commit: `6800999cbebf841efabd7ed82633a671f9fcda5c`
- Merged immutable-build-input commit: `dd015ee36b57738a5bb39f207588b7b5b4009f5b`
- Merged embedded-SDK/failed-candidate evidence commit: `fc4e2bdfa71d23b6b6c507ec188c6f7fb7d37d68`
- C-01 application-remediation candidate: `75a4d7c72a9ff8083750310707f64c08daa3d98b` (local, not merged)
- Qualified fork commit: `PENDING`
- Upstream PRs: none opened; all six patches are MuseMind-specific pending qualification

## Patch MM-RF-0001 — exact scope

- Contract: an `exact_mode=true` retrieval accepts only a non-empty `document_scope` of exact
  `{dataset_id, document_id}` pairs.
- Enforcement: omitted/empty/malformed scope is rejected before retrieval; each pair is checked
  against dataset ownership; TOC/KG and parent-child expansion are disabled; every returned chunk
  must carry an allowed exact pair or the entire result fails.
- Response provenance: `chunk_id`, `dataset_id`, `document_id` remain explicit in the REST/SDK
  contract.
- Tests: focused route tests implemented; C-02/C-03 live harness `PENDING`.
- Rollback: pin the previous qualified bundle and disable new publication work. Never fall back to
  the legacy unscoped route for a MuseMind candidate or runtime request.

## Patch MM-RF-0002 — create or adopt

- Contract: a single local upload may carry a caller-persisted 32-character lowercase hexadecimal
  `document_id`.
- Enforcement: the provider-visible filename is derived from that ID; an existing DB or storage
  identity is never overwritten or renamed; the DB identity is claimed before blob creation so a
  concurrent loser performs no storage write.
- Adoption: upload conflict is not success. The caller must exact-read the same dataset/document,
  download the bytes and verify byte count plus SHA-256 before adopting it.
- Tests: focused route/service tests implemented; C-04 response-loss/concurrency/checksum harness
  `PENDING`.
- Rollback: pin the previous qualified bundle and stop new materialization. Do not delete or choose
  an existing document by filename/list order.

## Patch MM-RF-0003 — client/auth hardening

- Contract: invalid API tokens are never partially logged and every bundled Python SDK request has
  an explicit configurable connect/read timeout.
- Enforcement: SDK default is `(5 s connect, 60 s read)` and may be overridden by operational
  configuration; timeout does not accept partial output as READY.
- Tests: focused SDK timeout test implemented; content-canary scan and proxy/network matrix C-06
  `PENDING`.
- Rollback: pin the previous qualified bundle only if its log/timeout behavior has independently
  passed the same hardening evidence; otherwise disable the affected workload.

## Patch MM-RF-0004 — immutable build inputs

- Contract: the C-01 build consumes an OCI-pinned Ubuntu base, an OCI-pinned `ragflow_deps` stage
  and an exact `infiniflow/resource` commit; tags and default branches are not build authority.
- Pins resolved 2026-08-09 from the official registries/upstream:
  `ubuntu:24.04@sha256:561618e2c15bf2397621dd04f96926663a3b5616c189cf7e38db7e82f5c538ea`,
  `infiniflow/ragflow_deps@sha256:dfda0dbc4b392d5046a1e81ddf09c1c56f86035a43412538b163c81cc36eb2aa`
  and resource commit `0937399b60f1949267388548e33ea0d5c0cc25f7`.
- Tests: required CI rejects malformed/mutable defaults; exact application OCI build, SBOM and scan
  passed on candidate `75a4d7c72…`. All five stateful image SBOM/scans were produced; their 11
  Critical and 300 High findings make the overall C-01 result `FAILED` pending new image digests.
- Rollback: select a prior exact qualified bundle. Updating any pin requires a reviewed PR and a new
  C-01 bundle/evidence run.

## Patch MM-RF-0005 — embedded SDK artifact

- Contract: the C-01 application image carries the exact Python SDK wheel used by the MuseMind RAG
  Integration Service contract; source files present only in the build context are not bundle
  evidence.
- Evidence: the first exact rebuild of `dd015ee36b57738a5bb39f207588b7b5b4009f5b` succeeded but
  contained no `ragflow_sdk` package or wheel, so C-01 failed. Candidate
  `dc2ec60591c0c3e28808a793d892a1463ddd9cd7` builds the wheel with
  `uv build --wheel --no-build-isolation`, copies it to `/ragflow/sdk-dist/` and adds a required-CI
  source/build guard; it was merged by PR `MuseMinds/ragflow#4` as `fc4e2bdfa…`.
- Candidate artifact: `/ragflow/sdk-dist/ragflow_sdk-0.26.4-py3-none-any.whl`, 18,142 bytes,
  SHA-256 `76e904c44d9daaee000928f3f08355f3c2beb539dbed3bfbc729d09510e1a43b` in the current
  application-remediation candidate.
- Tests: isolated wheel build, archive membership and exact OCI rebuild passed. Required CI passed
  on PR run `31320981828` and protected-branch merge run `31321072388`.
- Rollback: no prior qualified MuseMind bundle exists. Keep publication disabled and do not deploy
  the SDK-less `dd015ee…` image.

## Patch MM-RF-0006 — C-01 runtime vulnerability remediation

- Contract: the MuseMind production image contains no Critical or High vulnerability without an
  explicit risk acceptance; build-only or non-servable surfaces are not retained in production.
- Enforcement: remove Node/npm, Tika DOC/PPT fallback artifacts and legacy `libssl1.1` from the
  production stage; remove the non-servable AgentRun/Aliyun provider from the runtime registry;
  upgrade vulnerable Python packages and the Mistral/Zhipu call sites; enforce the locked security
  floors in required CI.
- PDF compatibility: replace `xgboost 1.6.0`, which imports removed `pkg_resources`, with official
  CPU-only `xgboost-cpu 2.1.4`; loading the bundled `updown_concat_xgb.model` passed inside the
  exact candidate image.
- Evidence: source commit `75a4d7c72a9ff8083750310707f64c08daa3d98b`; OCI manifest
  `sha256:b706ec1f79cb6f9d5ba3739c9604d7a773407cef67f8dd1fd1fb94964fe5fd10`;
  Trivy 0.70.0 found 0 Critical and 0 High using databases updated 2026-08-09. No risk acceptance.
- Tests: 25 dependency floors, 23 embedding tests, 1 exact route test, 8 upload/auth/provider tests,
  8 SDK tests, compile, Ruff and diff checks passed. Remote required CI and protected-branch merge
  of this patch remain `PENDING`.
- Rollback: keep publication disabled or select a prior fully qualified immutable bundle. Do not
  restore the vulnerable artifacts to production to regain unsupported MIME/provider behavior.

## Qualification status

| Evidence | Status |
|---|---|
| Source patch and focused tests | Implemented; exact route `1`, upload/auth/provider `8`, SDK `8`, embedding `23` passed on Python 3.13.14 |
| Required source CI | `musemind-provider-contract` passed for merged embedded-SDK commit `fc4e2bdfa71d23b6b6c507ec188c6f7fb7d37d68`; PR run `31320981828`, merge run `31321072388`. Runtime-remediation candidate `75a4d7c72…` is local and has no remote CI result. |
| Branch protection for `musemind` | Active 2026-08-09: PR required, admins enforced, conversations resolved, stale reviews dismissed, no force-push/delete; strict required check `musemind-provider-contract` |
| OCI application digest and embedded SDK checksum | Current candidate: OCI manifest `sha256:b706ec1f79cb6f9d5ba3739c9604d7a773407cef67f8dd1fd1fb94964fe5fd10`; OCI config `sha256:4987234ee17f47c789c917331d8a1676eeae9adbde33792e4ff7a83fbf4ceb8b`; SDK SHA-256 `76e904c44d9daaee000928f3f08355f3c2beb539dbed3bfbc729d09510e1a43b`. Not published or fully qualified. |
| Stateful service digests and config checksum | Exact index/platform digests and all five SBOM/scans are recorded. Stateful descriptor SHA-256 `dc4200acf7358fdb0746ca553950c5935c156cc2044a82c7335616dbd671c9ca`; application/bundle descriptor SHA-256 `13c7ce19704548f691bb5258d2537c41afd2e09d6c0b141ddf48856e796fc9cb`. The ADR-0032 generation JCS checksum remains `PENDING`. |
| SBOM and vulnerability disposition | Current application CycloneDX SBOM produced with Syft 1.50.0; Trivy 0.70.0 found 0 Critical and 0 High. No risk acceptance. Exact reports and checksums are in `MuseMindArchitecture/docs/architecture/research/artifacts/0024-ragflow-c01/`. |
| C-01–C-09 reproducible results | C-01 application image security sub-gate `PASSED`; stateful scans found 11 Critical/300 High with no risk acceptance, so C-01 overall `FAILED`; C-02–C-09 `PENDING` |

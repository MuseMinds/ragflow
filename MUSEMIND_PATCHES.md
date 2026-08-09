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
- MuseMind development branch: `mm/c01-bundle-qualification`
- MuseMind source patch commit: `802ef6aac725e0eda53207d7f2fc2a9adbe16874`
- MuseMind pull request: `MuseMinds/ragflow#2`
- Merged provider-contract commit: `6800999cbebf841efabd7ed82633a671f9fcda5c`
- Merged immutable-build-input commit: `dd015ee36b57738a5bb39f207588b7b5b4009f5b`
- C-01 candidate commit: `dc2ec60591c0c3e28808a793d892a1463ddd9cd7` (local, not merged)
- Qualified fork commit: `PENDING`
- Upstream PRs: none opened; all five patches are MuseMind-specific pending qualification

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
- Tests: required CI rejects malformed/mutable defaults; OCI build, SBOM and scan remain `PENDING`.
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
  source/build guard.
- Candidate artifact: `/ragflow/sdk-dist/ragflow_sdk-0.26.4-py3-none-any.whl`, 18,142 bytes,
  SHA-256 `1210ca56aa16fb10812b44fd38a3f3c71e218c59de5f78a338d3f5ca9b7e8c97`.
- Tests: isolated wheel build and archive membership check passed; exact OCI rebuild passed. Remote
  required CI and protected-branch merge remain `PENDING`.
- Rollback: no prior qualified MuseMind bundle exists. Keep publication disabled and do not deploy
  the SDK-less `dd015ee…` image.

## Qualification status

| Evidence | Status |
|---|---|
| Source patch and focused tests | Implemented; focused exact route `1`, upload/auth `6`, SDK `8` passed on Python 3.13.14 |
| Required source CI | `musemind-provider-contract` passed for merged immutable-build-input commit `dd015ee36b57738a5bb39f207588b7b5b4009f5b`; GitHub Actions run `31315559553`. Candidate `dc2ec605…` is local and has no remote CI result. |
| Branch protection for `musemind` | Active 2026-08-09: PR required, admins enforced, conversations resolved, stale reviews dismissed, no force-push/delete; strict required check `musemind-provider-contract` |
| OCI application digest and embedded SDK checksum | Candidate only: OCI manifest `sha256:d36e9bde9347f7133ae74ef7d24199e131efc5b1ebce668ec4e1565da1902a94`; OCI config `sha256:bfc3da48e328f6876d8949d44221e50735913026fc62fe66c50454221fd8c5f0`; SDK SHA-256 `1210ca56aa16fb10812b44fd38a3f3c71e218c59de5f78a338d3f5ca9b7e8c97`. Not published or qualified. |
| Stateful service digests and config checksum | Exact index/platform digests are recorded in the C-01 qualification descriptor, whose SHA-256 is `b7df5e61c54be8e939e76b03253a907e5248187b873e2e16ad7580c253b29474`. Stateful SBOM/scans and the ADR-0032 generation JCS checksum remain `PENDING`. |
| SBOM and vulnerability disposition | CycloneDX SBOM produced with Syft 1.50.0. Trivy 0.70.0 found 5 Critical and 94 High (5/92 with fixes, 2 High without fixes); zero risk acceptances. C-01 `FAILED`. Exact reports and checksums are in `MuseMindArchitecture/docs/architecture/research/artifacts/0024-ragflow-c01/`. |
| C-01–C-09 reproducible results | C-01 `FAILED`; C-02–C-09 `PENDING` |

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
- MuseMind development branch: `mm/phase-2-provider-contract`
- MuseMind source patch commit: `802ef6aac725e0eda53207d7f2fc2a9adbe16874`
- MuseMind pull request: `MuseMinds/ragflow#2`
- Qualified fork commit: `PENDING`
- Upstream PRs: none opened; all three patches are MuseMind-specific pending qualification

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

## Qualification status

| Evidence | Status |
|---|---|
| Source patch and focused tests | Implemented; focused exact route `1`, upload/auth `6`, SDK `8` passed on Python 3.13.14 |
| Required source CI | `musemind-provider-contract` passed on Python 3.13.14 for fork commit `ace0b7d596093f36ea39157e4cc1a977afc10075`; GitHub Actions run `31314801177` |
| Branch protection for `musemind` | Active 2026-08-09: PR required, admins enforced, conversations resolved, stale reviews dismissed, no force-push/delete; strict required check `musemind-provider-contract` |
| OCI application digest and embedded SDK checksum | `PENDING` |
| Stateful service digests and config checksum | `PENDING` |
| SBOM and vulnerability disposition | `PENDING` |
| C-01–C-09 reproducible results | `PENDING` |

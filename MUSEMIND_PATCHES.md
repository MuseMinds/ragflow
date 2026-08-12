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
- MuseMind protected branch: `musemind`
- MuseMind source patch commit: `802ef6aac725e0eda53207d7f2fc2a9adbe16874`
- MuseMind pull request: `MuseMinds/ragflow#2`
- Merged provider-contract commit: `6800999cbebf841efabd7ed82633a671f9fcda5c`
- Merged immutable-build-input commit: `dd015ee36b57738a5bb39f207588b7b5b4009f5b`
- Merged embedded-SDK/failed-candidate evidence commit: `fc4e2bdfa71d23b6b6c507ec188c6f7fb7d37d68`
- C-01 application-remediation source: `75a4d7c72a9ff8083750310707f64c08daa3d98b`
- Merged application-remediation commit: `90f69de96c21023a0dc741ad8f0e27357a94d77f`
- C-02/C-03 harness source: `d5f77d473dcfe61b2da3143faedc9409ccba04c6`
- C-02/C-03 CI fix/head: `c3fcb7319d2776c1bb063d06eda63983e4c5afa4`
- Merged C-02/C-03 harness commit: `e27df812f9c2a0dd10ecb5ff1436b755d645a5e5`
- C-02/C-03 pull request: `MuseMinds/ragflow#7`
- C-04 harness source: `ad807a77699dc24676da40e0fcfc50028fc0bc4d`
- Merged C-04 harness commit: `80dd3b66e2f10416b7f72687507ae275451aac7f`
- C-04 pull request: `MuseMinds/ragflow#9`
- C-04 clean-preflight fix source: `08dce6593a231e0acfd33c59b1d7eb8715f125cf`
- Merged C-04 clean-preflight fix: `c24f8942d10964ebd2258ac6372ed69885712712`
- C-04 clean-preflight fix pull request: `MuseMinds/ragflow#11`
- C-04 transaction-context fix source: `2a83f701193a388038c12e1b677ac7c625316388`
- C-04 transaction-context fix pull request: `MuseMinds/ragflow#13`
- Merged C-04 transaction-context fix: `e2513ed33107a92b5d1a8e53bf5d0279be708eba`
- C-04 chunk read-back page-size fix source: `eeb4fa0a58d375d202f0c9467e5aead13c2c65b3`
- C-04 chunk read-back page-size fix pull request: `MuseMinds/ragflow#15`
- Merged C-04 chunk read-back page-size fix: `201608f54d360700f7fb26a9fffcbc0d9a0d3d25`
- Exact bundle commit exercised through C-05: `ed23c7a7beb5e61d555bdd4a247b89c17a17f976`
- C-05 harness SHA-256: `8b4e0bcfb574d3e8085d04b3c5a80e7caa99ce20d88d77b710460a69241f78ff`
- C-05 harness source commit: `334362095d4a2488152a3509503249073325db9d`
- C-05 harness pull request: `MuseMinds/ragflow#17`
- Merged C-05 harness commit: `6991c50355e77c5e3e5c59b0021cb28ee9315927`
- C-07M exact-prefix MinIO connector source: `fce8851a30c7aa8fc7c3acdd07501d5a6b8d3c83`
- C-07M exact-prefix MinIO connector pull request: `MuseMinds/ragflow#19`
- Merged C-07M exact-prefix MinIO connector commit: `6bdea2b01fc33b6018fe7cc36a7064acb6ecd89e`
- Provider service-principal bootstrap source: `90d11e08d10a55f08f190e21e685f1802b889f24`
- Provider service-principal bootstrap pull request: `MuseMinds/ragflow#23`
- Merged provider service-principal bootstrap commit: `a7c7f5f14489f94dc4f7ac8c3eb53ca7d3ca4fa1`
- Provider bootstrap tenant-default fix source: `2c57368804e1a174c5f8cc28f1209593b55c2cbe`
- Fail-closed build version fix source: `759d6c4c2414d5afdbdf05acda8453c966297793`
- Fully qualified fork commit: `PENDING`; C-01 and local C-02–C-09 passed, but C-07M target
  EC2/EBS stop/start remains a separate deployment qualification
- Upstream PRs: none opened; runtime patches are MuseMind-specific and qualification harnesses do
  not change served behavior

## Patch MM-RF-0001 — exact scope

- Contract: an `exact_mode=true` retrieval accepts only a non-empty `document_scope` of exact
  `{dataset_id, document_id}` pairs.
- Enforcement: omitted/empty/malformed scope is rejected before retrieval; each pair is checked
  against dataset ownership; TOC/KG and parent-child expansion are disabled; every returned chunk
  must carry an allowed exact pair or the entire result fails.
- Response provenance: `chunk_id`, `dataset_id`, `document_id` remain explicit in the REST/SDK
  contract.
- Tests: focused route tests implemented; final C-02/C-03 live harness `PASSED` on exact bundle
  `ed23c7a7…`.
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
- Tests: focused route/service tests implemented. The manifest-driven C-04 harness provisions the
  shared two-museum A/B/C/D fixture topology and fails closed on response loss, concurrent claim,
  checksum collision, dirty namespace or non-terminal/duplicate chunk output. Its offline tests are
  implemented. The exact `152a870a6…` live run proved the clean namespace, then exposed a nested
  `DB.atomic()`/`DB.connection_context()` failure before any create completed; source fix
  `2a83f7011…` removes that invalid nesting while retaining the document primary key as the
  concurrency claim. The exact `b829c6809…` rebuilt/published rerun passed every create/adopt,
  collision and parse-terminal case, then exposed a harness-only `page_size=1000` chunk read-back
  against the live API maximum `100`; source fix `eeb4fa0a5…` and a wire regression are merged by
  PR `MuseMinds/ragflow#15`. A new protected-digest rerun remains `PENDING`.
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
  Critical and 300 High findings are temporarily accepted for the exact recorded digests by
  Raffaele Berzoini for the September 2026 `develop` pilot. The acceptance expires 2026-09-30 and
  does not apply to production or changed digests.
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
  8 SDK tests, compile, Ruff and diff checks passed. Required CI passed on PR run `31325124546` and
  protected-branch merge run `31325377993`; PR `MuseMinds/ragflow#5` merged as `90f69de96…`.
- Rollback: keep publication disabled or select a prior fully qualified immutable bundle. Do not
  restore the vulnerable artifacts to production to regain unsupported MIME/provider behavior.

## Patch MM-RF-0007 — C-02/C-03 conformance harness

- Contract: a manifest-driven live runner exercises two isolated museum tenants, two stable
  datasets per museum, synthetic A/B/C/D documents across all ADR-0029 MIME types, A+B+C versus
  A+B+D exact scope, pair and tenant denial, provenance and TOC/KG denial on one pinned bundle.
- Fail-closed evidence: the runner rejects mutable/all-zero bundle identities and invalid fixture
  topology, emits no token/query/content/raw error, invalidates the entire result on rogue or
  missing provenance and cannot report `PASSED` without an actual retriever-call counter proving
  zero delta around rejected scope requests.
- Tests: offline matrix `14` passed on Python 3.13.14; existing exact route `1` and SDK `8` tests
  remained green. Required CI passed on PR run `31328879842` and protected-branch merge run
  `31328950365`; PR `MuseMinds/ragflow#7` merged as `e27df812f9…`. The final exact bundle
  `ed23c7a7…` run passed `28/28`, including provider-counter proof with zero delta for every rejected
  scope and exact pair provenance for every returned chunk. Result SHA-256
  `9272b14a1247770053a5dd0598a3cc7f1612cf445f5daf6839b2cdfe4e89b672`.
- Rollback: remove the harness only; this patch changes no served route or provider behavior.

## Patch MM-RF-0008 — C-04 create-or-adopt conformance harness

- Contract: a clean-namespace, manifest-driven runner proves response-loss adoption by exact
  list/download size+SHA-256, exactly one winner under two concurrent exact-ID creates, immutable
  bytes under checksum and wrong-dataset collisions, and one non-empty unique chunk set after one
  parse request per dataset.
- Fixture reuse: the same two museums, two datasets per museum and A/B/C/D documents provision the
  exact topology consumed by C-02/C-03 and later clean-namespace C-09 evidence.
- Fail-closed evidence: mutable/all-zero bundle identity, mismatched local fixture hash, dirty
  namespace, duplicate success, overwrite, ambiguous read-back, parse failure/deadline or duplicate
  chunk IDs prevent `PASSED`. Output excludes tokens, paths, filenames, bytes and raw provider
  messages/errors.
- Tests: the offline fail-closed matrix is `14` passed on Python 3.13.14. The first live preflight on
  `bf1c782466…` stopped before mutation because the singular `id` filter returns code `102` for an
  absent document. PR `MuseMinds/ragflow#11` changed the exact filter to `ids` and added a wire
  regression; required CI passed on PR run `31335771848` and protected-branch merge run
  `31335962837`, merged as `c24f8942d…`. The rebuilt `152a870a6…` bundle passed all eight clean
  preflights but failed all creates because connection-owning service calls were nested inside
  `DB.atomic()`; content-free result SHA-256
  `ddf2220cef7b57e491138c4ea8babfce22c0223ef3d368e2332aedebd81f4adb`. Source fix
  `2a83f7011…` and its regression passed required CI in PR `MuseMinds/ragflow#13`; the fix merged as
  `e2513ed33…`. The exact protected-head `b829c6809…` application was rebuilt, scanned and
  published privately as OCI manifest
  `sha256:ec2954a081cc88faee5e5f1c005122fb606dc87a21a691c7e03630a2d4395e94`. Its fresh live C-04
  run passed clean preflight, all create/adopt/collision cases, four parse requests and terminal
  `DONE` with one metadata chunk for every document, but the harness requested chunk page size
  `1000` while the API maximum is `100`; all eight read-backs failed closed. Content-free result
  SHA-256 `ff16d801949c6ed2ac22921fe0daf45015e143f2e4d384aba61a1f5967e81f56`.
  PR `MuseMinds/ragflow#15` caps the request at `100` and adds an exact wire regression; required CI
  passed on PR run `31343172258` and protected-branch run `31343255000`, merged as `201608f54…`.
  The final exact bundle `ed23c7a7…` run passed clean preflight and all `30/30` create/adopt,
  response-loss, collision, exact-download, parse-terminal and unique chunk-set cases. Result
  SHA-256: `00181738dbe46fffad028e6284ea42ae8ac6aaf898d0b95484a5ab4f1312940e`.
- Rollback: remove only the harness/CI step; this does not alter the qualified MM-RF-0002 runtime
  behavior in exact bundle `ed23c7a7…`.

## Patch MM-RF-0009 — C-05 parser intake conformance harness

- Contract: the MuseMind-owned intake accepts at most 25,000,000 bytes and only PDF/plain/Markdown;
  it checks MIME/magic, structurally valid non-encrypted PDF and UTF-8 text without NUL before any
  RAGFlow call. Parse and hang deadlines are configurable and never accept partial output as READY.
- Isolation: PDF structural validation runs in a bounded child process. The live hang path uses a
  valid 1,500-page resource-amplifying PDF, requires deadline expiry while non-terminal, exact stop,
  stable `CANCEL` through the observation window and zero chunks.
- Fail-closed evidence: six simple-invalid fixtures returned the exact content-free reason with
  provider-call delta zero; three Italian valid MIME fixtures reached `DONE` with non-empty unique
  chunks. Raw provider messages, source paths, fixture bytes and synthetic canary never enter the
  result.
- Tests: offline matrix `11` passed on Python 3.12.3; Ruff 0.16.0 passed. Live result on exact bundle
  `ed23c7a7…` passed `14/14`; result SHA-256
  `92c49a45d0ec5e691147f18d530d4d38dac4fefaa68ceda5257dd8b67053eb0a`, manifest SHA-256
  `3ffef6d0d47bd5b897493ea3c8deac177c38cc8c89b151ad51d3735d9b6432c1`. Canary was absent from
  result and 195,635 bytes of runtime log; no trace sink was configured.
- Rollback: remove only this harness, manifest example and tests. The exact runtime bundle remains
  `ed23c7a7…`; no served route, parser or provider behavior changed.

## Patch MM-RF-0010 — MinIO exact-prefix connector

- Contract: pre-provisioned single-bucket MinIO deployments operate with a non-root identity whose
  `ListBucket` permission is conditioned to the configured exact prefix; bucket-wide `HeadBucket`,
  bucket creation and cross-prefix operations remain denied.
- Enforcement: single-bucket health consumes one prefix-scoped `ListObjects` request; object
  existence uses exact `StatObject`; copy skips destination bucket probing/creation after both keys
  have been resolved beneath the configured prefix. Multi-bucket behavior remains unchanged.
- Tests: source commit `fce8851a30c7aa8fc7c3acdd07501d5a6b8d3c83` adds seven connector
  regressions and makes them part of required CI. PR `MuseMinds/ragflow#19` merged as protected
  commit `6bdea2b01fc33b6018fe7cc36a7064acb6ecd89e`; PR run `31387152615` and protected-branch run
  `31387326684` passed. Local C-02–C-09 qualification is recorded in Architecture Evidence 0024.
- Upstream status: no PR opened; this is a MuseMind pilot least-privilege patch required by
  ADR-0034 and Evidence 0024.
- Rollback: pin a previously fully qualified immutable bundle and stop new publication work. Never
  restore `HeadBucket`, automatic bucket creation or broad `ListBucket` to preserve availability.

## Patch MM-RF-0011 — provider service-principal bootstrap

- Contract: one stable non-human provider principal exists per logical
  `(rag_instance_id, environment)` with `User.id = Tenant.id`, one exact `OWNER` relation,
  `is_superuser = false`, unusable interactive/session identity and only the current plus an
  explicitly bounded previous raw provider token.
- Enforcement: the schema-aware one-shot derives deterministic row identities, takes a MySQL
  advisory lock and reconciles all rows in one ORM transaction. It emits only `CREATED`,
  `UNCHANGED`, `REPAIRED` or content-free `CONFLICT`, repairs only unambiguous partial state and
  fails closed on foreign token ownership, unexpected tokens, incompatible ownership or an
  interactive/privileged principal.
- Rotation: `reconcile` accepts current and optional previous token files only with a bounded UTC
  deadline; `revoke-previous` removes only the explicitly identified previous token after smoke.
  Reversing current/previous supports rollback before revocation.
- Tests: deterministic identity, clean create/idempotency, compatible repair, fail-closed conflicts,
  concurrent reconciliation, content-free output and add/smoke/revoke/rollback are covered by 16
  focused unit tests. Required CI passed in PR run `31481057849` and protected-branch run
  `31481235029`; live clean-namespace qualification is `PENDING`.
- Rollback: pin the prior qualified bundle and keep the proxy non-ready. Do not seed or edit MySQL
  manually, create a replacement human principal or grant the permanent proxy database access.

## Patch MM-RF-0012 — remove unused Selenium Wire dependency

- Contract: `api.utils.web_utils` keeps Selenium `4.32.0` as an explicit runtime dependency, while
  the unused Selenium Wire interception proxy is absent from the project manifest, lockfile and
  installed runtime environment.
- Security outcome: removing Selenium Wire also removes its bundled reusable CA private key and the
  exclusively transitive `kaitaistruct`, `pydivert` and `zstandard` distributions from the locked
  graph. No RAGFlow request interception behavior was in use or is replaced.
- Enforcement: required CI fails if Selenium Wire re-enters the manifest, lockfile or installed
  environment, and verifies the exact direct Selenium version before running the existing provider
  contract suite.
- Tests: source commit `6f756e1305ad44f994b7c3c5946a303b6644d95f` merged through PR
  `MuseMinds/ragflow#25` as protected commit `2a7be698f48dc5446b85a0186769ac358c54efa1`.
  Required CI passed in PR run `31489152862` and protected-branch run `31489730264`; local lock
  consistency, Selenium/web-utils import, Selenium Wire absence, compilation and all `16` provider
  identity tests passed. The exact clean rebuilt source commit
  `5abd2a143b0474c9812e8acc299285f87dc9e986` is published privately as OCI manifest
  `sha256:239c01eba318acc91c21ad527f85893e1dbe13f52c4ed564f0a3ec0759b8bb50`; Review 0040 records the
  bounded `develop` risk acceptance and C-01 is `PASSED WITH TEMPORARY RISK ACCEPTANCE`.
- Rollback: pin the previous fully qualified immutable bundle and keep the proxy non-ready. Do not
  restore Selenium Wire merely to recover the unused interception behavior or its static CA.

## Patch MM-RF-0013 — minimal provider tenant defaults

- Contract: the schema-aware provider one-shot must populate every required internal tenant field
  without starting the full RAGFlow web application or initializing storage, retrieval or LLM
  clients.
- Enforcement: the command reads only `user_default_llm` metadata, resolves the same configured
  model naming convention and supplies a non-empty parser default. Malformed configuration fails
  closed with `PROVIDER_DEFAULTS_INVALID`; API keys are neither consumed nor retained.
- Incident evidence: exact bundle `5abd2a143b0474c9812e8acc299285f87dc9e986` reached MySQL,
  advisory lock, transaction, schema validation and an empty snapshot on target develop, but the
  uninitialized CLI value `parser_ids=None` violated the non-null tenant schema. Both attempted
  reconciliations rolled back completely and returned content-free
  `CONFLICT/LOCK_OR_TRANSACTION_FAILURE`; no provider rows or readiness marker remained.
- Tests: source commit `2c57368804e1a174c5f8cc28f1209593b55c2cbe`; all `20` focused
  provider-identity tests and Ruff checks pass locally. PR `MuseMinds/ragflow#29` passed required
  run `31502008323` and protected run `31502204693`. Exact rebuilt bundle qualification is recorded
  below; target clean-namespace bootstrap remains pending.
- Rollback: pin the prior exact bundle and keep the proxy non-ready. Do not initialize the full web
  server from the one-shot or seed the missing tenant field through SQL.

## Patch MM-RF-0014 — fail-closed immutable version build

- Contract: a qualified application image must contain a non-empty `/ragflow/VERSION` derived from
  the exact checked-out Git commit; an unreadable or incomplete Git object database is a build
  failure, never a usable anonymous version.
- Enforcement: the Docker build step now enables shell fail-fast behavior, requires `git describe`
  to return a non-empty value and verifies the written VERSION artifact. Required CI guards the
  fail-closed assertion.
- Incident evidence: the first local no-cache build attempt for protected commit
  `50b6ba6877342ca95e1670168e9aafe48eef9600` used a shared clone whose Windows alternate object
  path was not visible to BuildKit. `git describe` failed but the prior multi-command RUN continued
  and produced an empty VERSION. Local image `sha256:1f2cd40c08c1bcefe58dd93c9f18f02adfd30d56b3f7de4e4e742a21e760b0e6`
  was rejected before runtime checks, scan or publication and is not qualification evidence.
- Tests: source commit `759d6c4c2414d5afdbdf05acda8453c966297793`; diff and source guard pass
  locally. PR `MuseMinds/ragflow#30` passed required run `31503870525` and protected run
  `31504123674`. A direct, independent GitHub clone at merge commit
  `bfd0d428aaa47ade853b393957d10ae3986f7202` passed `git fsck`, had no alternates and produced a
  non-empty `/ragflow/VERSION` matching the exact clone's `git describe` result.
- Rollback: keep the current assertion. Never qualify an image with an empty or unresolvable source
  version merely because its container layers built successfully.

## Patch MM-RF-0015 — API-first Jina v3 generation

- Contract: ADR-0037's first `develop` candidate authorizes only
  `jina-embeddings-v3@musemind@Jina` for the
  non-human service tenant. The request sends the exact Jina endpoint, 1024 dimensions, query or
  passage task, normalized float output, truncation and disabled late chunking explicitly; inputs
  are text-only and no error path selects Builtin, TEI or another provider.
- Credential lifecycle: the schema-aware one-shot reconciles the tenant embedding default and one
  deterministic provider/instance/model authorization in RAGFlow's current model store from a
  mounted Jina key. A previous exact `tenant_llm` row is consumed and removed in the
  same transaction that creates the current authorization; partial, duplicated or drifted rows
  fail closed. It repairs only an unambiguous key/default rotation, emits a key fingerprint rather
  than the secret, and never leaves two credential paths active. The permanent proxy receives
  neither MySQL nor the key.
- Drift/readiness: every live response must be finite, L2-normalized and exactly 1024-dimensional.
  The content-free synthetic Italian/multilingual probe exercises passage and query adapters and
  emits only vector fingerprints, aggregate cosine values and token counts for qualification and
  startup evidence. Numeric material-drift tolerance remains a qualification output before the
  candidate can become `QUALIFIED`.
- Target finding: fresh C-04 preflights on deployed bundle `ee0d37a63â€¦` stopped before the first
  dataset was created because the public dataset resolver could not see the exact authorization in
  the superseded store (`code 102`, `Provider Jina not found`). The current-store reconciliation
  above is the correction; the rejected attempts created no documents and are not C-04 evidence.
- Tests/evidence: protected source commit `95c311e81ca2409ed38b1a214d894fae4bf59996`, PR `#33`,
  required runs `31534866305` and `31535100326`, exact local OCI manifest
  `sha256:66411ae4a6d29f2c6f82f2c415771458ad8a32c0849230304805c9377f31feb0`,
  config `sha256:c938def30f43f4ce7c3262448b87c3523482a46d58e58c6d8a8b756ea05be0c2`,
  embedded SDK SHA-256 `da45bee30e2f8d6395cdbd31bd7b16962aa2348852c939ac6efd3e8124129e9c`
  and generation JCS `b2f99fb1ddb8cc94dbc9bb583ce9f879779ed66c2d30d6dbdad12a9c89c96860`
  are recorded by Architecture Evidence 0030. Review 0042 closes the exact stateful-risk stop.
  Protected workflow commit `afa9cc9fa81c2e48b29886ee6da40f397ee62b7f`, publication run
  `31577246409`, private GHCR package version `1124214748` and idempotent mirror run `31577429006`
  verify byte-identical immutable publication, so C-01 passes with that bounded acceptance. Live
  probe and C-04/C-05/C-06/C-08/C-09 reruns remain pending. Historical BGE/TEI generation evidence
  is not relabelled.
- Rollback: fence the candidate and restore a previously qualified immutable generation. A key
  rollback uses the secret/one-shot lifecycle and does not mutate generation identity; never fall
  back automatically to TEI or another embedding model.

## Qualification status

| Evidence | Status |
|---|---|
| Source patch and focused tests | Implemented; exact route `1`, upload/auth/provider `9`, SDK `8`, embedding `23` passed on Python 3.13.14. The transaction-context regression also passed in the complete upload service file (`22` tests). |
| Required source CI | `musemind-provider-contract` passed for application-remediation PR `MuseMinds/ragflow#5`: PR run `31325124546`, protected-branch merge run `31325377993`, merge commit `90f69de96c21023a0dc741ad8f0e27357a94d77f`. C-02/C-03 harness PR `#7`: PR run `31328879842`, protected-branch merge run `31328950365`, merge commit `e27df812f9c2a0dd10ecb5ff1436b755d645a5e5`. C-04 harness PR `#9`: PR run `31329923099`, protected-branch merge run `31330292083`, merge commit `80dd3b66e2f10416b7f72687507ae275451aac7f`. C-04 clean-preflight fix PR `#11`: PR run `31335771848`, protected-branch merge run `31335962837`, merge commit `c24f8942d10964ebd2258ac6372ed69885712712`. C-04 transaction-context fix PR `#13`: PR run `31338206113`, protected-branch merge run `31338289729`, merge commit `e2513ed33107a92b5d1a8e53bf5d0279be708eba`. C-04 chunk page-size fix PR `#15`: PR run `31343172258`, protected-branch merge run `31343255000`, merge commit `201608f54d360700f7fb26a9fffcbc0d9a0d3d25`. C-05 harness PR `#17`: PR run `31366255142`, protected-branch merge run `31366398618`, merge commit `6991c50355e77c5e3e5c59b0021cb28ee9315927`. MinIO exact-prefix connector PR `#19`: PR run `31387152615`, protected-branch merge run `31387326684`, merge commit `6bdea2b01fc33b6018fe7cc36a7064acb6ecd89e`. Provider bootstrap/ledger and Selenium remediation protected runs `31481057849`, `31481235029`, `31481447412`, `31481599539`, `31489152862`, `31489730264`, `31490285058` and `31490440309` passed. Publication workflow PR `#27` passed PR run `31496232094` and protected-branch run `31496421445`, merge commit `492481af674e67aef7c6c38f0a0dbcc717a67bde`. Provider-defaults PR `#29` passed PR run `31502008323` and protected run `31502204693`; fail-closed VERSION PR `#30` passed PR run `31503870525` and protected run `31504123674`; exact-bundle publication PR `#31` passed PR run `31508722379` and the protected job in run `31509111665`, merge commit `37de7d6653d6652a47e607871d3222cc50858b11`. Jina generation PR `#33` passed PR run `31534866305` and protected run `31535100326`, merge commit `95c311e81ca2409ed38b1a214d894fae4bf59996`. Jina publication PR `#34` passed PR run `31576750709` and protected run `31576907045`, merge commit `afa9cc9fa81c2e48b29886ee6da40f397ee62b7f`. |
| Branch protection for `musemind` | Active 2026-08-09: PR required, admins enforced, conversations resolved, stale reviews dismissed, no force-push/delete; strict required check `musemind-provider-contract` |
| OCI application digest and embedded SDK checksum | Exact clean rebuilt source commit `95c311e81ca2409ed38b1a214d894fae4bf59996`: private GHCR OCI manifest `sha256:66411ae4a6d29f2c6f82f2c415771458ad8a32c0849230304805c9377f31feb0`; config `sha256:c938def30f43f4ce7c3262448b87c3523482a46d58e58c6d8a8b756ea05be0c2`; SDK SHA-256 `da45bee30e2f8d6395cdbd31bd7b16962aa2348852c939ac6efd3e8124129e9c`. Commit tag and authenticated immutable reference resolve to the reviewed digest; package version `1124214748` was created by run `31577246409`; anonymous inspection is denied. |
| Develop ECR mirror | ECR tag `qualified-66411ae4a6d2` resolves to the byte-identical OCI manifest `sha256:66411ae4a6d29f2c6f82f2c415771458ad8a32c0849230304805c9377f31feb0`. The governed GHCR-to-ECR workflow completed idempotently in run `31577429006` without rebuilding or replacing the immutable destination tag. |
| Stateful service digests and config checksum | The same five exact stateful digests have fresh Syft/Trivy reports and are enumerated in Architecture Review 0042. RFC 8785 generation checksum `b2f99fb1ddb8cc94dbc9bb583ce9f879779ed66c2d30d6dbdad12a9c89c96860`. |
| SBOM and vulnerability disposition | Exact application bundle SBOM produced with Syft 1.50.0; deterministic gzip SHA-256 `108b0e4d3821cbf1f02923e3768c359bcedc58ef81a15fe849846f3561abcfae`. Trivy 0.70.0 found 0 Critical, 0 High, 55 Medium and 72 Low; deterministic report gzip SHA-256 `526568a4298a8d7599aa33fb5847325611540991ba2cb63e46a39003e62a0d3d`. Fresh stateful scans confirm 11 Critical, 300 High, 499 Medium, 126 Low and 41 Unknown. Raffaele Berzoini explicitly accepted the bounded exact `develop` pilot risk in Architecture Review 0042 through 2026-09-30. |
| C-01–C-09 reproducible results | C-01 is `PASSED WITH TEMPORARY RISK ACCEPTANCE` for exact bundle `95c311e81…` and generation `b2f99fb1…`. Source contract, clean build, runtime import, SBOM/scan, private publication and byte-identical ECR mirror evidence are complete. Target bootstrap/readiness and C-02–C-09 live reruns on this exact bundle remain pending; prior live results are historical and are not inferred onto this bundle. |
| C-02/C-03 conformance harness | Last historical live result on bundle `6bdea2b01…`: `28/28`, counter proof `AVAILABLE`, zero provider delta for every reject and exact allowlisted provenance; result SHA-256 `0290522815a3654d94fde35685d20ffdc9b7dca8f807bfb21cfe61b1478a02ea`. Exact-bundle target rerun is pending. |
| C-04 conformance harness | Last historical live result on bundle `6bdea2b01…`: clean namespace and `30/30`; result SHA-256 `571c5dbb9d3079f4f766d937f70cf5944a281c5177ab417d723cb98652b36354`. Exact-bundle clean-namespace rerun is pending after bootstrap/readiness. |
| C-05 conformance harness | Last historical live result on bundle `6bdea2b01…`: `14/14`, invalid intake zero provider calls, three valid MIME terminal `DONE`, amplifying PDF deadline/cancel with zero chunks and canary absent; result SHA-256 `0d0eae4f516bea4f390ec40a8663e1952c89fdc7a316c5b494db61459b0a5c05`. Exact-bundle target rerun is pending. |

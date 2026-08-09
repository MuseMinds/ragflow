# MuseMind C-02/C-03 exact-scope conformance

This runner exercises the MuseMind exact retrieval wire contract against one exact fork-derived
RAGFlow service. It covers two isolated museum tenants, two stable datasets per museum, synthetic
documents A/B/C/D, all three ADR-0029 MIME types, A+B+C versus A+B+D scope, wrong dataset/document
pairs, cross-museum access, response provenance and the TOC/KG expansion denial.
It also reads back each exact dataset and requires the `naive` chunk method plus explicit
`raptor=false`, `graphrag=false` and `parent_child=false`; returned chunks carrying parent/RAPTOR
markers invalidate the result.

It does not create datasets or upload documents. Provision the synthetic fixtures through the
materialization harness so the same identities can later be reused by C-04/C-05/C-09. Do not put API
tokens in the manifest: only environment variable names are allowed.

Copy `c02-c03.manifest.example.json` outside the repository evidence directory, replace every
placeholder with the exact exercised values and export the two token variables. The bundle fields
must identify an immutable fork commit, OCI digest, embedded SDK checksum and bundle/config
descriptor checksum. The all-zero example bundle values and non-hex fixture placeholders are
rejected so an unedited example cannot become qualification evidence.

Run from the repository root:

```bash
python -m tools.musemind_conformance.exact_scope \
  --manifest /safe/path/c02-c03.manifest.json \
  --output /safe/path/c02-c03.result.json
```

The output never contains tokens, marker text, retrieved content or raw provider errors. A failed
request records only case name, status/code and content-free counts/booleans.

## Provider-call proof

The RAGFlow error response alone cannot prove that rejected empty/omitted/mixed scopes caused zero
internal retrieval calls. Point `provider_call_counter` at the content-free monotonically increasing
counter exposed by the test ingress/proxy:

```json
{
  "url": "https://private-counter.example/metrics/retrieval-calls",
  "token_env": "MUSEMIND_C02_COUNTER_TOKEN",
  "json_field": "retrieval_calls"
}
```

The runner snapshots that counter around every rejection case and requires a zero delta. With a
missing counter, wire checks may pass but the overall result is deliberately `INCOMPLETE` and the
process exits non-zero. `PASSED` therefore means both the wire matrix and the zero-provider-call
proof succeeded on the pinned bundle.

The counter must observe the actual internal retriever/search invocation (for example at a
test-only service metric or downstream search boundary), not merely incoming HTTP requests, and
must expose no query, token, document name or content. It is qualification instrumentation, not a
new production API contract.

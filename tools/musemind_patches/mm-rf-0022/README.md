# MM-RF-0022 local source evidence

This directory preserves the historical local experiment. Current joint adoption and remaining
qualification gates are recorded in the [consolidation scope](../../../MUSEMIND_PATCHES.md#mm-rf-0021–0023-consolidation-scope-2026-10-10).

The plain Markdown packing change keeps an existing ATX heading and its following
body element together. The parser's heading metadata is used before content is
packed; no heading or bibliography is copied into another section.

The focused regression is dependency-light and executes actual source bodies:

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s test/unit_test/rag/app -p test_markdown_heading_boundaries.py -v
```

The combined source run also executes these unchanged upstream tests:

- `test/unit_test/deepdoc/parser/test_naive_markdown_merge.py`
- `test/unit_test/deepdoc/parser/test_markdown_parser.py`

For the combined local run, only the exact `_is_short_header` production function
was injected into the pre-existing import fixture to avoid app/model/storage
startup. Its encoder used the locally cached, hash-verified cl100k vocabulary.
New packing tests execute the real extractor, `Markdown.__call__`, helper and
Markdown packing branch. Network access was disabled in the test subprocess.
The protected provider-contract workflow now lists the new regression explicitly,
alongside compile/lint coverage for the changed source. No remote run was started.

`source-validation-03.json` and its log are the final combined source run.
The unnumbered validation is a preserved test-runner configuration failure (no
tests ran: the disabled asyncio plugin left an unknown config option).
Run 02 passed before a whitespace-only import-block formatting correction.
No dependency was installed or modified to run these tests.

`source-receipt-02.json` identifies the final files, including the source CI wiring,
and aggregate verification. `source-receipt.json` is the prior local receipt before
that wiring was added; runtime and test bytes are identical in both receipts.
The private corpus and detailed per-document replay are intentionally outside
this repository. Its aggregate receipt records a fresh replay of the actual
modified source, not the earlier prototype's results.

Bounds remain explicit: the 512-token setting is a target; some original body
elements and heading/body groups exceed it. This patch does not repair detached
bibliographies or establish which reference proves a claim. Setext and indented
headings retain the existing text-block treatment. Images, tables, HTML, front
matter, custom delimiters, overlap and nonpositive budgets retain prior behavior.

No service, index, dataset, generation or approved source was changed. A new
immutable bundle/generation, fresh materialization and impact-scoped canonical
qualification are required before promotion. These local checks do not measure
retrieval or answer quality.

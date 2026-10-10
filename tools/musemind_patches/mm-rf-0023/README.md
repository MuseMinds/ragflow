# MM-RF-0023 local source evidence

This directory preserves the historical local experiment, including its standalone top-ten
regression. Adoption is only as part of the jointly tested final-weight `1.0` package; see the
[consolidation scope](../../../MUSEMIND_PATCHES.md#mm-rf-0021–0023-consolidation-scope-2026-10-10).

Synonym expansion now uses explicit dictionary entries only. The lookup API has
no reliable query-language or sense context. The removed fallback selected
English WordNet synset names for any lowercase Latin token, then truncated an
unordered set. A fixed Python hash seed or sorted set would address repeatability
but would not make those expansions contextually valid.

The runtime change is confined to `rag/nlp/synonym.py`. Curated lists, dictionary
loading, query construction, original words, adjacent phrases and reranking are
unchanged. Unconfigured English synonym recall can decrease; curated dictionaries
are not automatically a proof of appropriate contextual meaning. Existing
tokenizer behavior and mutable Redis dictionaries remain separate concerns.

Run the synthetic source regression without provider or model startup:

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s test/unit_test/rag/nlp -p test_curated_synonyms.py -v
```

The test loads the actual synonym module and actual query/similarity bodies.
Query layout fixtures deliberately use a whitespace tokenizer and unit weights;
they do not claim production tokenizer or Elasticsearch ranking coverage.
Separate subprocesses compare exact query text, keyword order and term scores
with hash seeds `0`, `1`, `17`, `54101` and `random`.

Private corpus questions, vectors and retrieved passages remain outside this
repository. A frozen private protocol compares cached-vector retrieval on one
physical index with unchanged scope, weights and parser. Top-ten retrieval and
post-retrieval selection are distinct evaluations; no answer generation or new
embedding is needed for that comparison.

The source workflow explicitly includes the new test and lint path. Local
validation is not immutable-bundle or production qualification. No running
provider, source corpus, index, container image or application generation is
modified by this patch's offline tests.

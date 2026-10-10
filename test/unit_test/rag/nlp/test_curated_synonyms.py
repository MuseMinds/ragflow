"""Actual synonym module and query method bodies, with no provider/model startup.

Query layout tests use ordered unit weights and a whitespace tokenizer. These
test original terms, curated expansion order and adjacent-term scoring, not the
quality of the production tokenizer or an Elasticsearch ranking simulation.
"""

import ast
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import unittest
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def load_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dealer(dictionary):
    module = load_module("actual_synonym", "rag/nlp/synonym.py")
    result = module.Dealer.__new__(module.Dealer)
    result.dictionary = dictionary
    result.redis = None
    result.lookup_num = 0
    result.load_tm = 0
    return result


def queryer(dictionary):
    """Execute unchanged query/similarity bodies, isolating dependency startup."""
    query_base = load_module("actual_query_base", "common/query_base.py")
    namespace = {
        "QueryBase": query_base.QueryBase,
        "logging": logging,
        "json": json,
        "re": re,
        "defaultdict": defaultdict,
        "rag_tokenizer": SimpleNamespace(
            tokenize=lambda value: " ".join(value.lower().split()),
            fine_grained_tokenize=lambda value: " ".join(value.lower().split()),
            tradi2simp=lambda value: value,
            strQ2B=lambda value: value,
        ),
    }
    for relative_path, class_name in [
        ("common/doc_store/doc_store_base.py", "MatchTextExpr"),
        ("rag/nlp/query.py", "FulltextQueryer"),
    ]:
        source = ROOT / relative_path
        tree = ast.parse(source.read_text(encoding="utf-8"))
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102 -- actual repository AST
    result = namespace["FulltextQueryer"].__new__(namespace["FulltextQueryer"])
    result.tw = SimpleNamespace(
        weights=lambda tokens, **_: [(token, 1 / len(tokens)) for token in tokens],
        split=lambda text: text.split(),
    )
    result.syn = dealer(dictionary)
    result.query_fields = ["content_ltks"]
    return result


def snapshot():
    curated = {
        "automobile": ["motor car", "motorcar", "car", "vehicle"],
        "ranked": ["tenth", "second", "ninth", "first", "eighth", "third", "seventh", "fourth", "sixth", "fifth"],
        "明亮": ["光亮", "敞亮"],
    }
    lookups = {token: dealer(curated).lookup(token) for token in ["gift", "pain", "sale", "die", "bank", "spring", "automobile", "ranked", "明亮"]}
    q = queryer(curated)
    queries = {}
    for text in ["automobile engine wheel design", "ranked collection date origin", "bank river deposit money", "明亮 空间"]:
        expression, keywords = q.question(text)
        queries[text] = {
            "query": expression.matching_text,
            "keywords": keywords,
            "similarity": q.token_similarity(keywords, ["automobile engine wheel design", "engine automobile wheel design", "motor car"]),
        }
    return {"lookups": lookups, "queries": queries}


class TestCuratedSynonyms(unittest.TestCase):
    def test_import_has_no_wordnet_loading_or_dependency(self):
        # Exercise the whole module, not an extracted lookup body. NLTK being
        # absent must not trigger an import, corpus load or fallback here.
        with patch.dict(sys.modules, {"nltk": None, "nltk.corpus": None}):
            module = load_module("without_nltk_synonym", "rag/nlp/synonym.py")
        self.assertTrue(callable(module.Dealer))

    def test_unconfigured_latin_words_are_not_assumed_english(self):
        value = dealer({})
        # French, German, Italian and English homographs/polysemous terms.
        for term in ("pain", "chat", "gift", "die", "sale", "come", "bank", "spring", "seal", "bass", "qwertyxyz"):
            with self.subTest(term=term):
                self.assertEqual(value.lookup(term), [])

    def test_non_latin_and_mixed_terms_need_explicit_mapping_too(self):
        value = dealer({})
        for term in ("שלום", "明亮", "über", "école", "città", "alpha-beta", "x1", "two words"):
            with self.subTest(term=term):
                self.assertEqual(value.lookup(term), [])

    def test_curated_order_duplicates_and_limit_are_unchanged(self):
        values = ["zulu", "alpha", "zulu", "bravo", "hotel", "delta", "foxtrot", "echo", "charlie"]
        value = dealer({"ordered": values})
        self.assertEqual(value.lookup("ordered"), values[:8])
        self.assertEqual(value.lookup("ordered", 3), values[:3])
        self.assertEqual(value.lookup("ordered", 20), values)
        self.assertEqual(value.lookup("ordered", 0), [])
        self.assertEqual(values, value.dictionary["ordered"])

    def test_curated_string_and_multiword_key_are_preserved(self):
        value = dealer({"motor car": "automobile", "明亮": ["光亮", "敞亮"]})
        self.assertEqual(value.lookup(" motor\t  car "), ["automobile"])
        self.assertEqual(value.lookup("明亮"), ["光亮", "敞亮"])

    def test_returned_list_cannot_mutate_curated_dictionary(self):
        value = dealer({"engine": ["motor"]})
        result = value.lookup("engine")
        result.append("invented")
        self.assertEqual(value.lookup("engine"), ["motor"])

    def test_empty_and_invalid_tokens_keep_existing_behavior(self):
        value = dealer({})
        for term in (None, "", False, 17, [], {}):
            with self.subTest(term=term):
                self.assertEqual(value.lookup(term), [])

    def test_local_dictionary_loading_is_preserved(self):
        module = load_module("file_synonym", "rag/nlp/synonym.py")
        with patch("builtins.open", mock_open(read_data='{"ENGINE": ["motor", "power unit"]}')):
            value = module.Dealer()
        self.assertEqual(value.lookup("engine"), ["motor", "power unit"])

    def test_redis_dictionary_order_and_reload_gate_are_preserved(self):
        module = load_module("redis_synonym", "rag/nlp/synonym.py")
        redis = SimpleNamespace(get=lambda _: '{"engine": ["power unit", "motor", "power unit"]}')
        with patch("builtins.open", mock_open(read_data='{"engine": ["old"]}')):
            value = module.Dealer(redis)
        self.assertEqual(value.lookup("engine"), ["power unit", "motor", "power unit"])
        redis.get = lambda _: '{"engine": ["changed"]}'
        self.assertEqual(value.lookup("engine"), ["power unit", "motor", "power unit"])

    def test_original_query_terms_and_adjacent_phrases_remain(self):
        q = queryer({})
        expression, keywords = q.question("automobile engine wheel design")
        self.assertEqual(keywords, ["automobile", "engine", "wheel", "design"])
        for phrase in ('"automobile engine"', '"engine wheel"', '"wheel design"'):
            self.assertIn(phrase, expression.matching_text)
        self.assertNotIn('"automobile wheel"', expression.matching_text)
        self.assertEqual(expression.extra_options["original_query"], "automobile engine wheel design")

    def test_curated_terms_follow_original_terms_in_authored_order(self):
        q = queryer({"automobile": ["motor car", "car", "motorcar"]})
        expression, keywords = q.question("automobile engine wheel design")
        self.assertEqual(keywords, ["automobile", "engine", "wheel", "design", "motor car", "car", "motorcar"])
        self.assertLess(expression.matching_text.index('"motor car"'), expression.matching_text.index('"car"'))
        self.assertIn('"automobile engine"', expression.matching_text)

    def test_order_sensitive_adjacent_term_scoring_is_retained(self):
        q = queryer({})
        exact, reordered = q.token_similarity(["red", "blue", "green"], [["red", "blue", "green"], ["blue", "red", "green"]])
        self.assertGreater(exact, reordered)

    def test_english_recall_tradeoff_is_explicit_and_curated_mapping_restores_it(self):
        # Generic English paraphrase: automatic synonym recall is intentionally
        # not guaranteed. Do not mistake deterministic output for recall parity.
        unmapped = queryer({}).question("automobile engine wheel design")
        mapped = queryer({"automobile": ["car"]}).question("automobile engine wheel design")
        self.assertNotIn("car", unmapped[1])
        self.assertIn("car", mapped[1])
        self.assertIn('"car"', mapped[0].matching_text)

    def test_short_query_branch_uses_only_curated_expansions(self):
        q = queryer({"bank": ["savings bank"]})
        expression, keywords = q.question("bank deposit")
        self.assertIn("savings bank", keywords)
        self.assertIn("bank", keywords)
        self.assertNotIn("riverbank", expression.matching_text)

    def test_multiple_process_hash_seeds_preserve_query_keywords_and_scores(self):
        expected = None
        for seed in ("0", "1", "17", "54101", "random"):
            with self.subTest(seed=seed):
                env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
                output = subprocess.check_output([sys.executable, "-B", str(Path(__file__).resolve()), "--snapshot"], env=env, cwd=ROOT, timeout=30)
                if expected is None:
                    expected = output
                self.assertEqual(output, expected)


if __name__ == "__main__":
    if sys.argv[1:] == ["--snapshot"]:
        print(json.dumps(snapshot(), ensure_ascii=True, sort_keys=True))
    else:
        unittest.main()

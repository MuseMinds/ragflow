"""Resolve actual ordered flat-name templates; no ES race or qualification simulation."""

from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[4]


def first_mapping(field_name):
    """Apply the configured name predicates in order, including preceding regex rules."""
    templates = json.loads((ROOT / "conf/mapping.json").read_text(encoding="utf-8"))["mappings"]["dynamic_templates"]
    for entry in templates:
        if len(entry) != 1:
            raise AssertionError("Expected one named rule per template")
        rule = next(iter(entry.values()))
        if set(rule) - {"match", "match_pattern", "mapping"}:
            raise AssertionError("New template predicates require an explicit test update")
        pattern = rule["match"]
        kind = rule.get("match_pattern", "simple")
        if kind not in {"simple", "regex"}:
            raise AssertionError("Unsupported template match_pattern")
        matches = re.fullmatch(pattern, field_name) if kind == "regex" else fnmatchcase(field_name, pattern)
        if matches:
            return rule["mapping"]
    return None


class TestESVectorMapping(unittest.TestCase):
    def test_explicit_dimensions_cover_gemini_and_historical_vectors(self):
        for dimensions in (512, 768, 1024, 1536, 3072):
            for prefix in ("q", "embedding"):
                with self.subTest(dimensions=dimensions, prefix=prefix):
                    self.assertEqual(
                        first_mapping(f"{prefix}_{dimensions}_vec"),
                        {"type": "dense_vector", "index": True, "similarity": "cosine", "dims": dimensions},
                    )

    def test_no_catch_all_or_wrong_dimension_mapping(self):
        for field in ("q_2048_vec", "q_4096_vec", "q_30720_vec", "q_03072_vec", "q_3072_vector", "q_3072_vec_extra"):
            with self.subTest(field=field):
                self.assertIsNone(first_mapping(field))

    def test_nonvector_fields_keep_their_configured_mapping(self):
        expected = {
            "doc_id": {"type": "keyword", "similarity": "boolean", "store": True},
            "content_ltks": {"type": "text", "analyzer": "whitespace", "store": True},
            "content_with_weight": {"type": "text", "index": "false", "store": True},
            "position_int": {"type": "integer", "store": "true"},
            "q_3072_vec_id": {"type": "keyword", "similarity": "boolean", "store": True},
            "q_3072_vec_flt": {"type": "float", "store": True},
            "q_3072_vec_bin": {"type": "binary"},
        }
        for field, mapping in expected.items():
            with self.subTest(field=field):
                self.assertEqual(first_mapping(field), mapping)


if __name__ == "__main__":
    unittest.main()

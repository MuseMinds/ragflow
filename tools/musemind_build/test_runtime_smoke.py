"""Synthetic wheel provenance checks; no SDK import, Docker or network calls."""

import hashlib
import importlib.util
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("runtime_smoke", Path(__file__).with_name("runtime_smoke.py"))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
SOURCE = b"""class RAGFlow:
    def create_or_adopt_dataset_exact(self, dataset_id, provider_projection, *, contract_schema=None):
        return "musemind.ragflow-dataset-create-or-adopt/v3"
"""


class SdkProvenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.wheel_path = self.root / "ragflow_sdk-synthetic.whl"

    def write_wheel(self, source=SOURCE, member=module.SDK_SOURCE_MEMBER):
        with zipfile.ZipFile(self.wheel_path, "w") as wheel:
            wheel.writestr(member, source)
        return {module.SDK_SOURCE_MEMBER: hashlib.sha256(source).hexdigest()}

    def test_exact_wheel_source_and_explicit_contract_are_reported(self):
        expected = self.write_wheel()
        result = module.verify_sdk(self.root, expected)
        self.assertEqual(result["sdk_sha256"], hashlib.sha256(self.wheel_path.read_bytes()).hexdigest())
        self.assertEqual(result["sdk_source_files"], expected)
        self.assertEqual(result["sdk_dataset_contract_schema"], module.SDK_DATASET_CONTRACT_V3)

    def test_wheel_source_must_equal_protected_checkout(self):
        self.write_wheel(SOURCE + b"# altered packaging\n")
        with self.assertRaisesRegex(ValueError, "SDK_SOURCE_MISMATCH"):
            module.verify_sdk(self.root, {module.SDK_SOURCE_MEMBER: hashlib.sha256(SOURCE).hexdigest()})

    def test_sdk_proof_cannot_be_omitted(self):
        self.write_wheel()
        with self.assertRaisesRegex(ValueError, "SDK_SOURCE_EXPECTATION_INVALID"):
            module.verify_sdk(self.root, {})

    def test_absent_or_multiple_wheels_fail_closed(self):
        expected = {module.SDK_SOURCE_MEMBER: hashlib.sha256(SOURCE).hexdigest()}
        with self.assertRaisesRegex(ValueError, "SDK_CARDINALITY_INVALID"):
            module.verify_sdk(self.root, expected)
        self.write_wheel()
        (self.root / "ragflow_sdk-second.whl").write_bytes(self.wheel_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "SDK_CARDINALITY_INVALID"):
            module.verify_sdk(self.root, expected)

    def test_missing_source_member_fails_closed(self):
        expected = self.write_wheel(member="other.py")
        with self.assertRaisesRegex(ValueError, "SDK_SOURCE_CARDINALITY_INVALID"):
            module.verify_sdk(self.root, expected)

    def test_duplicate_source_member_fails_closed(self):
        expected = self.write_wheel()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.wheel_path, "a") as wheel:
                wheel.writestr(module.SDK_SOURCE_MEMBER, SOURCE)
        with self.assertRaisesRegex(ValueError, "SDK_SOURCE_CARDINALITY_INVALID"):
            module.verify_sdk(self.root, expected)

    def test_contract_keyword_must_remain_optional_and_keyword_only(self):
        for source in (
            SOURCE.replace(b"*, contract_schema=None", b"contract_schema=None"),
            SOURCE.replace(b"contract_schema=None", b"contract_schema"),
            SOURCE.replace(b"contract_schema=None", b'contract_schema="v2"'),
        ):
            with self.subTest(source=source):
                expected = self.write_wheel(source)
                with self.assertRaisesRegex(ValueError, "SDK_EXPLICIT_CONTRACT_KEYWORD_INVALID"):
                    module.verify_sdk(self.root, expected)

    def test_old_sdk_cannot_claim_v3(self):
        expected = self.write_wheel(SOURCE.replace(b"/v3", b"/v2"))
        with self.assertRaisesRegex(ValueError, "SDK_DATASET_V3_MISSING"):
            module.verify_sdk(self.root, expected)

    def test_missing_or_duplicate_entry_point_fails_closed(self):
        for source in (
            SOURCE.replace(b"create_or_adopt_dataset_exact", b"create_dataset"),
            SOURCE + SOURCE,
        ):
            with self.subTest(source=source):
                expected = self.write_wheel(source)
                with self.assertRaisesRegex(ValueError, "SDK_DATASET_ENTRY_POINT_INVALID"):
                    module.verify_sdk(self.root, expected)


if __name__ == "__main__":
    unittest.main()

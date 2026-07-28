import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "benchmark"))

from detect import build_report, d as parse_date, same_port
from evaluate_e2e import field_counts


class PipelineRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = json.loads(
            (ROOT / "samples" / "DEFECT-001.json").read_text(encoding="utf-8")
        )

    @patch.dict(os.environ, {"LLM_PROVIDER": "disabled"})
    def test_sample_detects_expected_defects_offline(self):
        report = build_report(
            self.case["case_id"],
            self.case["documents"],
            parse_date(self.case.get("presentation_date")),
        )
        predicted = {item["type"] for item in report["discrepancies"]}
        self.assertEqual(set(self.case["defect_types"]), predicted)

    def test_identical_documents_have_perfect_field_accuracy(self):
        for document in self.case["documents"].values():
            correct, total = field_counts(document, document)
            self.assertEqual(total, correct)

    def test_port_comparison_tolerates_one_ocr_character_only(self):
        self.assertTrue(same_port("NHAVA SHEVA, INDIA", "NAVA SHEVA, INDIA"))
        self.assertTrue(same_port("NHAVA SHEVA, INDIA", "NHAWA SHEVA, INDIA"))
        self.assertFalse(same_port("BUSAN, KOREA", "GWANGYANG, KOREA"))


if __name__ == "__main__":
    unittest.main()

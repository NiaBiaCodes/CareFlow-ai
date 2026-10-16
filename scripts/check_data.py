"""Run deterministic fixture and negative-case checks. No credentials needed."""
import copy
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import all_sources, load_json, patient_bundle, plan_request, validate_bundle


class FixtureChecks(unittest.TestCase):
    def setUp(self):
        self.dataset = load_json(ROOT / "data/dataset.json")
        self.happy = load_json(ROOT / "data/cases/happy.json")

    def test_fixed_scenarios(self):
        expectations = load_json(ROOT / "data/expectations.json")
        for case, expected in expectations.items():
            with self.subTest(case=case):
                out = plan_request(load_json(ROOT / "data/cases" / (case + ".json")))
                for key in ("patient_id", "status", "errors"):
                    self.assertEqual(out[key], expected[key])
                self.assertEqual(len(out["medication_changes"]), expected["medication_change_count"])
                self.assertFalse(out["submitted"])
                if out["status"] == "blocked":
                    self.assertEqual(out["form_fields"], {})

    def test_selected_patient_context_excludes_other_patient(self):
        bundle = patient_bundle(self.dataset, "SYN-P001")
        for key in ("encounters", "documents", "medication_snapshots", "requests", "pharmacy_status"):
            self.assertTrue(all(r["patient_id"] == "SYN-P001" for r in bundle[key]))
        with self.assertRaises(ValueError):
            patient_bundle(self.dataset, "SYN-P999")

    def test_medication_fact_and_citations(self):
        out = plan_request(self.happy)
        fact = out["medication_changes"][0]
        self.assertEqual(fact["medication"], "lisinopril")
        self.assertEqual(fact["before"]["dose"], "10 mg")
        self.assertEqual(fact["after"]["dose"], "20 mg")
        self.assertEqual(fact["source_record_ids"], ["MED-001-1", "MED-001-2"])
        source_ids = set(all_sources(self.happy))
        for ids in out["field_sources"].values():
            self.assertTrue(set(ids).issubset(source_ids))

    def test_unsigned_note_blocks(self):
        next(r for r in self.happy["documents"] if r["record_id"] == "NOTE-001-2")["signed"] = False
        self.assertIn("MISSING_SIGNED_NOTE", validate_bundle(self.happy))

    def test_wrong_service_referral_blocks(self):
        next(r for r in self.happy["documents"] if r["record_id"] == "REF-001")["service_code"] = "DEMO-OTHER"
        self.assertIn("REFERRAL_SERVICE_MISMATCH", validate_bundle(self.happy))

    def test_future_chart_record_blocks(self):
        self.happy["medication_snapshots"][-1]["effective_at"] = "2026-10-20T14:00:00Z"
        self.assertIn("FUTURE_SOURCE_RECORD", validate_bundle(self.happy))

    def test_duplicate_source_id_blocks(self):
        self.happy["documents"].append(copy.deepcopy(self.happy["documents"][0]))
        self.assertIn("DUPLICATE_RECORD_ID", validate_bundle(self.happy))

    def test_unavailable_pharmacy_is_explicit(self):
        result = plan_request(self.happy)
        self.assertIn("unavailable", result["limitations"][0])
        self.assertEqual(result["status"], "ready_for_review")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Select task evidence; fixtures supply scenario deltas, never the test oracle."""
import copy
import json

from core import all_sources, patient_bundle

from .config import ROOT
from .schemas import SCENARIO_PATIENTS

PATIENT_COLLECTIONS = ("encounters", "documents", "medication_snapshots", "pharmacy_status", "requests")


def assert_ownership(bundle, patient_id):
    if bundle["patient"]["patient_id"] != patient_id or any(
        row.get("patient_id") != patient_id
        for key in PATIENT_COLLECTIONS for row in bundle[key]
    ):
        raise ValueError("PATIENT_MISMATCH")
    needed = {r["provider_id"] for r in bundle["requests"]}
    if any(p["provider_id"] not in needed for p in bundle["providers"]):
        raise ValueError("UNRELATED_PROVIDER")


def select_context(repository, patient_id, scenario, demo_mode):
    bundle = repository.load_patient_context(patient_id)
    assert_ownership(bundle, patient_id)
    if scenario is None:
        return bundle
    if not demo_mode:
        raise ValueError("SCENARIOS_DISABLED")
    if SCENARIO_PATIENTS[scenario] != patient_id:
        raise ValueError("SCENARIO_PATIENT_MISMATCH")
    # Only differences from the original baseline are applied to this task copy.
    # Database changes remain authoritative; no scenario overwrites persistent charts.
    baseline = patient_bundle(json.loads((ROOT / "data/dataset.json").read_text()), patient_id)
    fixture = json.loads((ROOT / "data/cases" / f"{scenario}.json").read_text())
    result = copy.deepcopy(bundle)
    for key in ("patient", "policy"):
        for field, value in fixture[key].items():
            if value != baseline[key].get(field):
                result[key][field] = copy.deepcopy(value)
    for key in (*PATIENT_COLLECTIONS, "providers"):
        original = {r["record_id"]: r for r in baseline[key]}
        changed = {r["record_id"]: r for r in fixture[key]}
        removed = original.keys() - changed.keys()
        result[key] = [r for r in result[key] if r["record_id"] not in removed]
        for rid, row in changed.items():
            if row != original.get(rid):
                result[key] = [r for r in result[key] if r["record_id"] != rid]
                result[key].append(copy.deepcopy(row))
    return result


def permitted_sources(bundle, patient_id):
    # Contaminated task snapshots are retained privately for audit, never served.
    try:
        assert_ownership(bundle, patient_id)
    except ValueError:
        return {}
    return all_sources(bundle)

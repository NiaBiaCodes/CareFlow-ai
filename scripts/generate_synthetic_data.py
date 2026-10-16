"""Generate fixed, entirely fictional records. Run from any directory with Python 3.11+."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
(DATA / "cases").mkdir(exist_ok=True)


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


dataset = {
    "synthetic": True, "schema_version": "1.0", "snapshot_at": "2026-10-16T16:00:00Z",
    "description": "Invented fixtures for software testing. Not clinical guidance or payer policy.",
    "patients": [], "encounters": [], "documents": [], "medication_snapshots": [],
    "pharmacy_status": [], "requests": [],
    "providers": [{"record_id": "PROV-DEMO-001", "provider_id": "DEMO-PROVIDER-001", "name": "Demo Clinic", "synthetic": True}],
    "mock_policy": {
        "policy_id": "DEMO-POLICY-001", "synthetic": True,
        "description": "Fictional prior authorization completeness checklist. No coverage or medical necessity decision.",
        "required_request_fields": ["service_code", "requested_units", "planned_service_date", "diagnosis_code"],
        "required_documents": ["signed clinical note for the request encounter", "signed referral for the requested service"],
    },
}
for n, name, dob, meds in [
    (1, "Avery Demo", "1978-04-12", [
        [{"name": "lisinopril", "dose": "10 mg", "frequency": "daily"}, {"name": "metformin", "dose": "500 mg", "frequency": "twice daily"}],
        [{"name": "lisinopril", "dose": "20 mg", "frequency": "daily"}, {"name": "metformin", "dose": "500 mg", "frequency": "twice daily"}],
    ]),
    (2, "Morgan Demo", "1986-09-03", [
        [{"name": "amlodipine", "dose": "5 mg", "frequency": "daily"}],
        [{"name": "amlodipine", "dose": "5 mg", "frequency": "daily"}],
    ]),
]:
    pid = f"SYN-P{n:03d}"
    dataset["patients"].append({"record_id": f"PAT-{n:03d}", "patient_id": pid, "synthetic": True, "name": name,
        "date_of_birth": dob, "insurance_member_id": f"DEMO-MEMBER-{n:03d}", "payer_name": "Demo Health Plan"})
    for j, date in enumerate(["2026-09-30", "2026-10-14"]):
        eid, nid = f"ENC-{n:03d}-{j+1}", f"NOTE-{n:03d}-{j+1}"
        effective = date + "T14:00:00Z"
        dataset["encounters"].append({"record_id": eid, "patient_id": pid, "occurred_at": effective, "visit_type": "synthetic outpatient visit"})
        med_text = "; ".join(f"{m['name']} {m['dose']} {m['frequency']}" for m in meds[j])
        text = f"SYNTHETIC TEST NOTE. Chart medication reconciliation: {med_text}."
        if j == 1:
            text += " Persistent knee discomfort is recorded. Clinician requests a physical therapy evaluation under fictional service DEMO-PT-EVAL. This fixture makes no treatment recommendation."
        dataset["documents"].append({"record_id": nid, "patient_id": pid, "encounter_id": eid,
            "document_type": "clinical_note", "status": "final", "signed": True,
            "effective_at": effective, "recorded_at": date + "T14:20:00Z", "text": text})
        dataset["medication_snapshots"].append({"record_id": f"MED-{n:03d}-{j+1}", "patient_id": pid,
            "encounter_id": eid, "source_record_id": nid, "effective_at": effective,
            "recorded_at": date + "T14:20:00Z", "complete_reconciliation": True, "medications": meds[j]})
    ref = f"REF-{n:03d}"
    dataset["documents"].append({"record_id": ref, "patient_id": pid, "encounter_id": f"ENC-{n:03d}-2",
        "document_type": "referral", "status": "final", "signed": True,
        "effective_at": "2026-10-14T14:30:00Z", "recorded_at": "2026-10-14T14:35:00Z",
        "service_code": "DEMO-PT-EVAL", "text": "SYNTHETIC REFERRAL. Physical therapy evaluation requested by Demo Clinic. Six fictional units requested."})
    dataset["pharmacy_status"].append({"record_id": f"PHARM-{n:03d}", "patient_id": pid,
        "status": "not_connected", "recorded_at": "2026-10-16T15:00:00Z", "updates": []})
    dataset["requests"].append({"record_id": f"REQ-{n:03d}", "request_id": f"DEMO-PA-{n:03d}",
        "patient_id": pid, "encounter_id": f"ENC-{n:03d}-2", "provider_id": "DEMO-PROVIDER-001",
        "request_type": "prior_authorization", "service_code": "DEMO-PT-EVAL", "requested_units": 6,
        "planned_service_date": "2026-10-26", "diagnosis_code": "DEMO-MSK-001",
        "clinical_note_id": f"NOTE-{n:03d}-2", "referral_id": ref, "recorded_at": "2026-10-16T15:00:00Z"})
write(DATA / "dataset.json", dataset)

# Imports occur after writing the fixed dataset; generator and oracle are independent of planner results.
import sys
sys.path.insert(0, str(ROOT))
from core import patient_bundle

cases = {"happy": patient_bundle(dataset, "SYN-P001"), "corrected_referral": patient_bundle(dataset, "SYN-P002")}
cases["missing_referral"] = copy.deepcopy(cases["corrected_referral"])
cases["missing_referral"]["documents"] = [r for r in cases["missing_referral"]["documents"] if r["record_id"] != "REF-002"]
cases["wrong_patient"] = copy.deepcopy(cases["happy"])
cases["wrong_patient"]["documents"].append(copy.deepcopy(next(r for r in dataset["documents"] if r["record_id"] == "NOTE-002-2")))
cases["conflicting_medication"] = copy.deepcopy(cases["happy"])
conflict = copy.deepcopy(cases["happy"]["medication_snapshots"][-1])
conflict["record_id"] = "MED-001-CONFLICT"
conflict["medications"][0]["dose"] = "5 mg"
cases["conflicting_medication"]["medication_snapshots"].append(conflict)
cases["missing_member_id"] = copy.deepcopy(cases["happy"])
cases["missing_member_id"]["patient"]["insurance_member_id"] = None
for case, value in cases.items():
    write(DATA / "cases" / (case + ".json"), value)
write(DATA / "expectations.json", {
    "happy": {"patient_id": "SYN-P001", "status": "ready_for_review", "errors": [], "medication_change_count": 1},
    "corrected_referral": {"patient_id": "SYN-P002", "status": "ready_for_review", "errors": [], "medication_change_count": 0},
    "missing_referral": {"patient_id": "SYN-P002", "status": "blocked", "errors": ["MISSING_REFERRAL"], "medication_change_count": 0},
    "wrong_patient": {"patient_id": "SYN-P001", "status": "blocked", "errors": ["PATIENT_MISMATCH"], "medication_change_count": 0},
    "conflicting_medication": {"patient_id": "SYN-P001", "status": "blocked", "errors": ["CONFLICTING_MEDICATION_RECORDS"], "medication_change_count": 0},
    "missing_member_id": {"patient_id": "SYN-P001", "status": "blocked", "errors": ["MISSING_MEMBER_ID"], "medication_change_count": 0},
})

# Record IDs in the DB are safe underscore keys. Original source IDs stay in record_id.
tables = {"patients": "patient", "encounters": "encounter", "documents": "document", "medication_snapshots": "medication_snapshot", "pharmacy_status": "pharmacy_status", "requests": "admin_request", "providers": "provider"}
lines = ["-- Entirely synthetic fixtures. Re-running updates these fixed IDs only.", "BEGIN TRANSACTION;"]
for array, table in tables.items():
    for row in dataset[array]:
        key = row["record_id"].replace("-", "_")
        lines.append(f"UPSERT {table}:{key} CONTENT {json.dumps(row)};")
lines.append("UPSERT mock_policy:DEMO_POLICY_001 CONTENT " + json.dumps(dataset["mock_policy"]) + ";")
lines.append("COMMIT TRANSACTION;")
(DATA / "seed.surql").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("Generated 2 patients, 4 encounters, 6 documents, 4 medication snapshots and 6 scenario bundles.")

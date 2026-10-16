"""Offline reference logic for fictional CareFlow fixtures. No model or cloud calls."""
import copy
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def patient_bundle(dataset, patient_id):
    patients = [p for p in dataset["patients"] if p["patient_id"] == patient_id]
    if len(patients) != 1:
        raise ValueError("Unknown or duplicate patient")
    bundle = {
        "synthetic": True,
        "snapshot_at": dataset["snapshot_at"],
        "patient": copy.deepcopy(patients[0]),
        "policy": copy.deepcopy(dataset["mock_policy"]),
    }
    for key in ("encounters", "documents", "medication_snapshots", "pharmacy_status", "requests"):
        bundle[key] = copy.deepcopy([r for r in dataset[key] if r["patient_id"] == patient_id])
    provider_ids = {r["provider_id"] for r in bundle["requests"]}
    bundle["providers"] = copy.deepcopy([p for p in dataset["providers"] if p["provider_id"] in provider_ids])
    return bundle


def all_sources(bundle):
    rows = [bundle["patient"]]
    for key in ("encounters", "documents", "medication_snapshots", "pharmacy_status", "requests", "providers"):
        rows.extend(bundle[key])
    return {r["record_id"]: r for r in rows}


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_bundle(bundle):
    """Checks used by the example planner. Application authorization remains separate."""
    errors = []
    pid = bundle["patient"]["patient_id"]
    if not bundle.get("synthetic") or not bundle["patient"].get("synthetic"):
        errors.append("NON_SYNTHETIC_INPUT")
    for key in ("encounters", "documents", "medication_snapshots", "pharmacy_status", "requests"):
        for row in bundle[key]:
            if row["patient_id"] != pid:
                errors.append("PATIENT_MISMATCH")
    rows = [bundle["patient"]] + [r for k in ("encounters", "documents", "medication_snapshots", "pharmacy_status", "requests", "providers") for r in bundle[k]]
    ids = [r["record_id"] for r in rows]
    if len(ids) != len(set(ids)):
        errors.append("DUPLICATE_RECORD_ID")
    for row in rows:
        for key in ("effective_at", "recorded_at", "occurred_at"):
            if row.get(key) and at(row[key]) > at(bundle["snapshot_at"]):
                errors.append("FUTURE_SOURCE_RECORD")
    if len(bundle["requests"]) != 1:
        return sorted(set(errors + ["REQUEST_COUNT_INVALID"]))
    req = bundle["requests"][0]
    if not bundle["patient"].get("insurance_member_id"):
        errors.append("MISSING_MEMBER_ID")
    if not any(p["provider_id"] == req["provider_id"] for p in bundle["providers"]):
        errors.append("MISSING_PROVIDER")
    encounter_ids = {r["record_id"] for r in bundle["encounters"]}
    if req["encounter_id"] not in encounter_ids:
        errors.append("MISSING_ENCOUNTER")
    docs = {r["record_id"]: r for r in bundle["documents"]}
    for field, kind, code in (("clinical_note_id", "clinical_note", "MISSING_SIGNED_NOTE"), ("referral_id", "referral", "MISSING_REFERRAL")):
        doc = docs.get(req[field])
        if (not doc or doc["patient_id"] != pid or doc["document_type"] != kind
                or doc["status"] != "final" or not doc["signed"]
                or doc["encounter_id"] != req["encounter_id"]):
            errors.append(code)
    referral = docs.get(req["referral_id"])
    if referral and referral.get("service_code") != req["service_code"]:
        errors.append("REFERRAL_SERVICE_MISMATCH")
    for key in bundle["policy"]["required_request_fields"]:
        if req.get(key) is None or req.get(key) == "":
            errors.append("MISSING_REQUEST_FIELD_" + key.upper())
    snapshots_by_time = {}
    for row in bundle["medication_snapshots"]:
        note = docs.get(row["source_record_id"])
        if not note or note["patient_id"] != pid or note["encounter_id"] != row["encounter_id"]:
            errors.append("INVALID_MEDICATION_SOURCE")
        key = row["effective_at"]
        normalized = json.dumps(sorted(row["medications"], key=lambda m: m["name"]), sort_keys=True)
        if key in snapshots_by_time and snapshots_by_time[key] != normalized:
            errors.append("CONFLICTING_MEDICATION_RECORDS")
        snapshots_by_time[key] = normalized
    return sorted(set(errors))


def medication_changes(bundle):
    """Compare the two latest complete chart reconciliations by effective date."""
    rows = sorted(bundle["medication_snapshots"], key=lambda r: (at(r["effective_at"]), at(r["recorded_at"])))
    if len(rows) < 2:
        return {"changes": [], "limitation": "Two chart snapshots are required."}
    old, new = rows[-2:]
    before = {m["name"]: m for m in old["medications"]}
    after = {m["name"]: m for m in new["medications"]}
    changes = []
    for name in sorted(set(before) | set(after)):
        if before.get(name) == after.get(name):
            continue
        changes.append({
            "medication": name,
            "before": before.get(name), "after": after.get(name),
            "source_record_ids": [old["record_id"], new["record_id"]],
        })
    return {"changes": changes, "compared_dates": [old["effective_at"], new["effective_at"]]}


def plan_request(bundle):
    errors = validate_bundle(bundle)
    out = {
        "patient_id": bundle["patient"]["patient_id"],
        "status": "blocked" if errors else "ready_for_review",
        "errors": errors, "form_fields": {}, "field_sources": {},
        "medication_changes": [],
        "limitations": ["No pharmacy feed is connected; dispensing and fills are unavailable."],
        "submitted": False,
    }
    if errors:
        return out
    p, r = bundle["patient"], bundle["requests"][0]
    provider = next(x for x in bundle["providers"] if x["provider_id"] == r["provider_id"])
    out["form_fields"] = {
        "patient_id": p["patient_id"], "patient_name": p["name"], "date_of_birth": p["date_of_birth"],
        "insurance_member_id": p["insurance_member_id"], "provider_id": r["provider_id"],
        "service_code": r["service_code"], "requested_units": r["requested_units"],
        "planned_service_date": r["planned_service_date"], "diagnosis_code": r["diagnosis_code"],
        "clinical_note_id": r["clinical_note_id"], "referral_id": r["referral_id"],
    }
    for key in out["form_fields"]:
        if key in ("patient_id", "patient_name", "date_of_birth", "insurance_member_id"):
            source = p["record_id"]
        elif key == "provider_id":
            source = provider["record_id"]
        elif key in ("clinical_note_id", "referral_id"):
            source = r[key]
        else:
            source = r["record_id"]
        out["field_sources"][key] = [source]
    out["medication_changes"] = medication_changes(bundle)["changes"]
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="happy")
    args = parser.parse_args()
    if args.case not in load_json(ROOT / "data/expectations.json"):
        parser.error("Unknown fixture case")
    print(json.dumps(plan_request(load_json(ROOT / "data/cases" / (args.case + ".json"))), indent=2))

"""Fictional administrative completeness rules, never coverage advice."""
import re
from datetime import date

from core import at, plan_request

from .comparison import compare_medications
from .context import PATIENT_COLLECTIONS, assert_ownership


def nonempty(value):
    return type(value) is str and bool(value.strip())


def valid_date(value):
    try:
        return type(value) is str and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)) and bool(date.fromisoformat(value))
    except ValueError:
        return False


def validate(bundle, patient_id):
    try:
        assert_ownership(bundle, patient_id)
    except ValueError as exc:
        return [str(exc)]  # Stop before calculating or disclosing contaminated facts.
    errors = []
    p = bundle["patient"]
    if bundle.get("synthetic") is not True or p.get("synthetic") is not True or bundle["policy"].get("synthetic") is not True:
        errors.append("NON_SYNTHETIC_INPUT")
    try:
        snapshot = at(bundle["snapshot_at"])
        if snapshot.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return ["INVALID_SNAPSHOT_DATE"]
    if not nonempty(p.get("name")) or not re.fullmatch(r"SYN-P\d{3}", patient_id):
        errors.append("INVALID_PATIENT_IDENTITY")
    if not valid_date(p.get("date_of_birth")) or p["date_of_birth"] > snapshot.date().isoformat():
        errors.append("INVALID_DATE_OF_BIRTH")
    if not nonempty(p.get("insurance_member_id")):
        errors.append("MISSING_MEMBER_ID")
    rows = [p] + [r for k in (*PATIENT_COLLECTIONS, "providers") for r in bundle[k]]
    ids = [r.get("record_id") for r in rows]
    if any(not nonempty(rid) for rid in ids):
        errors.append("INVALID_RECORD_ID")
    elif len(ids) != len(set(ids)):
        errors.append("DUPLICATE_RECORD_ID")
    for row in rows:
        for key in ("effective_at", "recorded_at", "occurred_at"):
            if key in row:
                try:
                    timestamp = at(row[key])
                    if timestamp.tzinfo is None:
                        raise ValueError()
                    if timestamp > snapshot:
                        errors.append("FUTURE_SOURCE_RECORD")
                except (ValueError, TypeError, AttributeError):
                    errors.append("INVALID_SOURCE_DATE")
        if "effective_at" in row and "recorded_at" in row and "INVALID_SOURCE_DATE" not in errors:
            if at(row["recorded_at"]) < at(row["effective_at"]):
                errors.append("RECORDED_BEFORE_EFFECTIVE")
    for key, fields in {
        "encounters": ("occurred_at",),
        "documents": ("effective_at", "recorded_at", "encounter_id", "document_type", "status", "text"),
        "medication_snapshots": ("effective_at", "recorded_at", "encounter_id", "source_record_id"),
        "pharmacy_status": ("recorded_at", "status"),
    }.items():
        for row in bundle[key]:
            if any(not nonempty(row.get(field)) for field in fields):
                errors.append("INVALID_" + key.upper() + "_FIELDS")
    encounters = {r["record_id"]: r for r in bundle["encounters"]}
    docs = {r["record_id"]: r for r in bundle["documents"]}
    for row in bundle["documents"]:
        if type(row.get("signed")) is not bool:
            errors.append("INVALID_SIGNATURE_TYPE")
        if row.get("encounter_id") not in encounters:
            errors.append("INVALID_DOCUMENT_ENCOUNTER")
    for row in bundle["medication_snapshots"]:
        meds = row.get("medications")
        if type(row.get("complete_reconciliation")) is not bool:
            errors.append("INVALID_RECONCILIATION_TYPE")
        if type(meds) is not list or any(
            type(m) is not dict or set(m) != {"name", "dose", "frequency"}
            or any(not nonempty(v) for v in m.values()) for m in (meds if type(meds) is list else [])
        ):
            errors.append("INVALID_MEDICATION_FIELDS")
        elif len({m["name"] for m in meds}) != len(meds):
            errors.append("DUPLICATE_MEDICATION_NAME")
        encounter = encounters.get(row.get("encounter_id"))
        note = docs.get(row.get("source_record_id"))
        if (not encounter or not note or note.get("document_type") != "clinical_note"
                or note.get("signed") is not True or note.get("status") != "final"
                or note.get("encounter_id") != row.get("encounter_id")):
            errors.append("INVALID_MEDICATION_SOURCE")
        elif not errors and (at(row["effective_at"]) != at(encounter["occurred_at"])
                             or at(note["effective_at"]) != at(row["effective_at"])):
            errors.append("MEDICATION_ENCOUNTER_DATE_MISMATCH")
    if len(bundle["requests"]) != 1:
        return sorted(set(errors + ["REQUEST_COUNT_INVALID"]))
    req = bundle["requests"][0]
    for field in ("request_id", "encounter_id", "provider_id", "service_code", "planned_service_date",
                  "diagnosis_code", "clinical_note_id", "referral_id", "recorded_at"):
        if not nonempty(req.get(field)):
            errors.append("MISSING_REQUEST_FIELD_" + field.upper())
    if req.get("request_type") != "prior_authorization":
        errors.append("INVALID_REQUEST_TYPE")
    if type(req.get("requested_units")) is not int or req["requested_units"] <= 0:
        errors.append("INVALID_REQUESTED_UNITS")
    if not valid_date(req.get("planned_service_date")):
        errors.append("INVALID_PLANNED_SERVICE_DATE")
    elif req["planned_service_date"] < snapshot.date().isoformat():
        errors.append("PLANNED_SERVICE_DATE_IN_PAST")
    providers = [x for x in bundle["providers"] if x.get("provider_id") == req.get("provider_id")]
    if len(providers) != 1 or not nonempty(providers[0].get("name")) or providers[0].get("synthetic") is not True:
        errors.append("MISSING_PROVIDER")
    if req.get("encounter_id") not in encounters:
        errors.append("MISSING_ENCOUNTER")
    for field, kind, code in (("clinical_note_id", "clinical_note", "MISSING_SIGNED_NOTE"),
                              ("referral_id", "referral", "MISSING_REFERRAL")):
        doc = docs.get(req.get(field))
        if (not doc or doc.get("document_type") != kind or doc.get("status") != "final"
                or doc.get("signed") is not True or doc.get("encounter_id") != req.get("encounter_id")):
            errors.append(code)
    referral = docs.get(req.get("referral_id"))
    if referral and referral.get("service_code") != req.get("service_code"):
        errors.append("REFERRAL_SERVICE_MISMATCH")
    if not errors:
        comparison = compare_medications(bundle)
        if comparison["status"] == "blocked":
            errors.append("CONFLICTING_MEDICATION_RECORDS")
        elif comparison["status"] != "compared":
            errors.append("INSUFFICIENT_MEDICATION_RECORDS")
    return sorted(set(errors))


def evaluate(bundle, patient_id):
    errors = validate(bundle, patient_id)
    result = {"patient_id": patient_id, "status": "blocked" if errors else "ready_for_review",
              "errors": errors, "form_fields": {}, "field_sources": {}, "medication_changes": [],
              "limitations": ["Pharmacy information is unavailable; dispensing and fills are unknown."],
              "submitted": False, "comparison": None, "error_details": []}
    if errors and errors != ["PATIENT_MISMATCH"] and len(bundle["requests"]) == 1:
        request = bundle["requests"][0]
        messages = {
            "MISSING_REFERRAL": f"Required signed referral {request.get('referral_id')} for service {request.get('service_code')} and encounter {request.get('encounter_id')} is missing or invalid.",
            "MISSING_SIGNED_NOTE": f"Required signed final note {request.get('clinical_note_id')} for encounter {request.get('encounter_id')} is missing or invalid.",
            "MISSING_MEMBER_ID": "A nonempty insurance member ID is required on the patient record.",
            "CONFLICTING_MEDICATION_RECORDS": "Different complete medication reconciliations apply at the same effective instant; review the conflicting records.",
        }
        result["error_details"] = [messages[e] for e in errors if e in messages]
    if not errors:
        # Reuse the supplied field provenance after the stricter application checks.
        reference = plan_request(bundle)
        if reference["errors"]:
            result["errors"] = reference["errors"]
            result["status"] = "blocked"
            return result
        result.update(form_fields=reference["form_fields"], field_sources=reference["field_sources"],
                      medication_changes=reference["medication_changes"], comparison=compare_medications(bundle))
        result["medication_changes"] = result["comparison"]["changes"]
    elif not any(e in errors for e in ("PATIENT_MISMATCH", "CONFLICTING_MEDICATION_RECORDS")):
        # An administrative omission need not hide an otherwise valid chart comparison.
        structural = [e for e in errors if e not in {"MISSING_REFERRAL", "MISSING_MEMBER_ID"}]
        if not structural:
            result["comparison"] = compare_medications(bundle)
    return result

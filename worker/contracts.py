"""Exact fact contract shared by the worker and the authoritative server validator."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.comparison import compare_medications


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Medication(StrictModel):
    name: str
    dose: str
    frequency: str


class Fact(StrictModel):
    name: str
    before: Medication | None
    after: Medication | None
    source_record_ids: list[str]


class Answer(StrictModel):
    patient_id: str
    status: Literal["answered", "insufficient_records", "blocked"]
    compared_dates: list[str]
    medications: list[Fact]
    pharmacy_status: Literal["unavailable"]
    fills: Literal["unknown"]
    pharmacy_source_ids: list[str]
    missing_information: list[Literal["Pharmacy fills unknown", "Two complete reconciliations required", "Conflicting medication records"]]


def assert_patient(bundle, patient_id):
    if bundle.get("synthetic") is not True or bundle["patient"].get("synthetic") is not True:
        raise ValueError("NON_SYNTHETIC_INPUT")
    if bundle["patient"]["patient_id"] != patient_id:
        raise ValueError("PATIENT_MISMATCH")
    for key in ("encounters", "documents", "medication_snapshots", "pharmacy_status", "requests"):
        if any(row.get("patient_id") != patient_id for row in bundle[key]):
            raise ValueError("PATIENT_MISMATCH")
    providers = {r["provider_id"] for r in bundle["requests"]}
    if any(p["provider_id"] not in providers for p in bundle["providers"]):
        raise ValueError("UNRELATED_PROVIDER")


def fact_contract(bundle):
    """Facts are a check oracle, not a substitute model response."""
    assert_patient(bundle, bundle["patient"]["patient_id"])
    comparison = compare_medications(bundle)
    status = {"compared": "answered", "blocked": "blocked", "insufficient_records": "insufficient_records"}[comparison["status"]]
    missing = ["Pharmacy fills unknown"]
    if status != "answered":
        missing.append("Conflicting medication records" if status == "blocked" else "Two complete reconciliations required")
    facts = [{"name": f["medication"], "before": f["before"], "after": f["after"],
              "source_record_ids": f["source_record_ids"]} for f in comparison["changes"] + comparison["unchanged"]]
    return {"patient_id": bundle["patient"]["patient_id"], "status": status,
            "compared_dates": comparison["compared_dates"], "medications": sorted(facts, key=lambda f: f["name"]),
            "pharmacy_status": "unavailable", "fills": "unknown",
            "pharmacy_source_ids": sorted(r["record_id"] for r in bundle["pharmacy_status"]),
            "missing_information": missing}


def validate_answer(raw, bundle, patient_id):
    assert_patient(bundle, patient_id)
    answer = Answer.model_validate(raw).model_dump()
    expected = fact_contract(bundle)
    answer["medications"].sort(key=lambda f: f["name"])
    # Free clinical prose is deliberately excluded: unsupported claims cannot hide
    # behind an otherwise valid citation. Values, dates, and exact citation sets agree.
    for fact in answer["medications"]:
        fact["source_record_ids"].sort()
    for fact in expected["medications"]:
        fact["source_record_ids"].sort()
    answer["pharmacy_source_ids"].sort()
    if answer != expected:
        raise ValueError("UNSUPPORTED_MODEL_FACTS_OR_CITATIONS")
    return answer

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

SCENARIO_PATIENTS = {
    "happy": "SYN-P001",
    "missing_referral": "SYN-P002",
    "corrected_referral": "SYN-P002",
    "wrong_patient": "SYN-P001",
    "conflicting_medication": "SYN-P001",
    "missing_member_id": "SYN-P001",
}
Scenario = Literal["happy", "missing_referral", "corrected_referral", "wrong_patient",
                   "conflicting_medication", "missing_member_id"]
QUESTION = "What changed in this patient's charted medications between the previous and latest encounters?"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateTask(Input):
    patient_id: StrictStr = Field(pattern=r"^SYN-P\d{3}$")
    scenario: Scenario | None = None
    question: Literal[QUESTION] = QUESTION
    task_type: Literal["prior_authorization"] = "prior_authorization"


class FormValues(Input):
    patient_id: StrictStr
    patient_name: StrictStr
    date_of_birth: StrictStr
    insurance_member_id: StrictStr
    provider_id: StrictStr
    service_code: StrictStr
    requested_units: StrictInt = Field(gt=0)
    planned_service_date: StrictStr
    diagnosis_code: StrictStr
    clinical_note_id: StrictStr
    referral_id: StrictStr


class SaveDraft(Input):
    task_id: StrictStr
    patient_id: StrictStr
    context_hash: StrictStr
    expected_revision: StrictInt = Field(ge=0)
    values: FormValues

import json
import os

from .contracts import Answer, assert_patient, validate_answer


def answer_question(manifest):
    from openai import OpenAI
    bundle, patient_id = manifest["context"], manifest["patient_id"]
    assert_patient(bundle, patient_id)
    model = os.environ["LLM_MODEL"]  # No guessed model ID or silent fallback.
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=60, max_retries=0)
    response = client.responses.parse(
        model=model, store=False, max_output_tokens=3000,
        instructions=("Entirely synthetic record comparison. Use only this patient's supplied records. "
                      "Compare complete reconciliations at the two latest DISTINCT effective instants. "
                      "Return every medication, including unchanged medications; before/after must preserve exact chart strings. "
                      "Cite both medication snapshot IDs per fact, not note IDs; include every agreeing duplicate at those times. "
                      "Return compared_dates using the exact effective_at timestamp strings, including time and timezone, in ascending order. "
                      "Pharmacy status is unavailable and fills unknown; pharmacy_source_ids must cite all supplied pharmacy_status record IDs. "
                      "missing_information must include 'Pharmacy fills unknown'. No diagnoses, treatment, or dispensing inference."),
        input=json.dumps({"question": manifest["question"], "patient_id": patient_id,
                          "snapshot_at": bundle["snapshot_at"], "medication_snapshots": bundle["medication_snapshots"],
                          "supporting_notes": [r for r in bundle["documents"] if r["document_type"] == "clinical_note"],
                          "pharmacy_status": bundle["pharmacy_status"]}), text_format=Answer,
    )
    if response.output_parsed is None:
        raise ValueError("MODEL_REFUSAL_OR_INCOMPLETE_OUTPUT")
    validated = validate_answer(response.output_parsed.model_dump(), bundle, patient_id)
    return {"answer": validated, "model": model, "response_id": response.id}

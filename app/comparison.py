"""Deterministic comparison of complete synthetic reconciliations."""
from collections import defaultdict

from datetime import datetime


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compare_medications(bundle):
    groups = defaultdict(list)
    for row in bundle["medication_snapshots"]:
        if row.get("complete_reconciliation") is True:
            groups[at(row["effective_at"])].append(row)
    times = sorted(groups)
    out = {"label": "Synthetic record comparison", "status": "insufficient_records",
           "compared_dates": [], "source_record_ids": [], "reconciliations": [],
           "changes": [], "unchanged": [],
           "limitation": "Pharmacy information is unavailable; dispensing and fills are unknown."}
    for rows in groups.values():
        signatures = {tuple(sorted((m["name"], m["dose"], m["frequency"])
                                  for m in row["medications"])) for row in rows}
        if len(signatures) > 1:
            return {**out, "status": "blocked", "error": "CONFLICTING_MEDICATION_RECORDS"}
    if len(times) < 2:
        return out
    pair = [groups[t] for t in times[-2:]]
    old, new = [sorted(rows, key=lambda r: r["record_id"])[0] for rows in pair]
    citations = [row["record_id"] for rows in pair for row in sorted(rows, key=lambda r: r["record_id"])]
    out.update(status="compared", compared_dates=[old["effective_at"], new["effective_at"]],
               source_record_ids=citations, reconciliations=[
                   {"effective_at": r["effective_at"], "recorded_at": r["recorded_at"],
                    "source_record_ids": [x["record_id"] for x in rows],
                    "note_source_ids": sorted({x["source_record_id"] for x in rows}),
                    "medications": r["medications"]} for r, rows in zip((old, new), pair)])
    before, after = [{m["name"]: m for m in row["medications"]} for row in (old, new)]
    for name in sorted(before.keys() | after.keys()):
        fact = {"medication": name, "before": before.get(name), "after": after.get(name),
                "source_record_ids": citations}
        out["unchanged" if before.get(name) == after.get(name) else "changes"].append(fact)
    return out

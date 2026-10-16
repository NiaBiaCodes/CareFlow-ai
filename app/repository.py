"""SurrealDB repositories. All application values travel as RPC query bindings."""
import base64
import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class StorageUnavailable(RuntimeError):
    pass


class ConcurrentWrite(RuntimeError):
    pass


class Repository(Protocol):
    def list_patients(self): ...
    def load_patient_context(self, patient_id): ...
    def create_task(self, task, events): ...
    def get_task(self, task_id): ...
    def list_tasks(self, patient_id=None): ...
    def get_drafts(self, task_id): ...
    def get_events(self, task_id): ...
    def save_draft(self, task, draft, event, expected_revision): ...


class SurrealClient:
    def __init__(self, settings):
        self.settings = settings

    def query(self, sql, variables=None):
        cfg = self.settings
        if not cfg.password:
            raise StorageUnavailable("Set SURREAL_PASSWORD in .env, start SurrealDB, and run scripts/seed_surreal.py.")
        token = base64.b64encode(f"{cfg.username}:{cfg.password}".encode()).decode()
        request = Request(cfg.surreal_url + "/rpc", method="POST", headers={
            "Authorization": "Basic " + token, "Surreal-NS": cfg.namespace,
            "Surreal-DB": cfg.database, "Accept": "application/json", "Content-Type": "application/json",
        }, data=json.dumps({"id": 1, "method": "query", "params": [sql, variables or {}]}).encode())
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.load(response)
            if "error" in payload:
                raise StorageUnavailable("SurrealDB rejected the query. Check database configuration and server logs.")
            statements = payload["result"]
            failures = [str(row.get("result")) for row in statements if row.get("status") != "OK"]
            # A transaction may report cancelled statements before its actual failure.
            # Inspect every result so the root conflict is not hidden by cancellation.
            if any("CAREFLOW_CONCURRENT_WRITE" in failure or "read or write conflict" in failure.lower()
                   for failure in failures):
                raise ConcurrentWrite("Draft changed; reload the task before saving.")
            if failures:
                raise StorageUnavailable("SurrealDB rejected a statement. Check the server logs; no fallback storage is used.")
            return [row.get("result") for row in statements]
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            raise StorageUnavailable("SurrealDB is unavailable. Check its connection, credentials, and seed; no fallback storage is used.") from exc


def clean(row):
    return {k: v for k, v in row.items() if k != "id"}


class SurrealPatientRepository:
    def __init__(self, settings):
        self.client = SurrealClient(settings)

    def list_patients(self):
        rows = self.client.query("SELECT patient_id, name FROM patient ORDER BY patient_id;")[0]
        if not rows:
            raise StorageUnavailable("CareFlow baseline is missing. Run scripts/seed_surreal.py.")
        return [clean(r) for r in rows]

    def load_patient_context(self, patient_id):
        # A read transaction gives a consistent task snapshot. Every patient table is filtered.
        results = self.client.query("""
            BEGIN TRANSACTION;
            SELECT * FROM patient WHERE patient_id = $pid;
            SELECT * FROM encounter WHERE patient_id = $pid;
            SELECT * FROM document WHERE patient_id = $pid;
            SELECT * FROM medication_snapshot WHERE patient_id = $pid;
            SELECT * FROM pharmacy_status WHERE patient_id = $pid;
            SELECT * FROM admin_request WHERE patient_id = $pid;
            SELECT * FROM provider WHERE provider_id IN
                (SELECT VALUE provider_id FROM admin_request WHERE patient_id = $pid);
            SELECT * FROM mock_policy WHERE policy_id = 'DEMO-POLICY-001';
            SELECT * FROM careflow_metadata WHERE name = 'baseline';
            COMMIT TRANSACTION;
        """, {"pid": patient_id})
        # Transaction control statements return NONE; select results retain statement order.
        selects = [item for item in results if isinstance(item, list)]
        patient, encounters, docs, meds, pharmacy, requests, providers, policies, meta = selects
        if not patient:
            raise KeyError(patient_id)
        if len(patient) != 1 or len(policies) != 1 or len(meta) != 1:
            raise StorageUnavailable("CareFlow baseline is missing or duplicated. Run scripts/seed_surreal.py.")
        return {"synthetic": True, "snapshot_at": meta[0]["snapshot_at"],
                "patient": clean(patient[0]), "policy": clean(policies[0]),
                **{key: [clean(r) for r in rows] for key, rows in zip(
                    ("encounters", "documents", "medication_snapshots", "pharmacy_status", "requests", "providers"),
                    (encounters, docs, meds, pharmacy, requests, providers))}}

    def seed(self, sql, snapshot_at):
        self.client.query(sql)
        self.client.query("UPSERT careflow_metadata:baseline CONTENT $meta;", {
            "meta": {"name": "baseline", "snapshot_at": snapshot_at}})


class SurrealWorkflowRepository:
    def __init__(self, settings):
        self.client = SurrealClient(settings)

    def create_task(self, task, events):
        self.client.query("""
            BEGIN TRANSACTION;
            CREATE type::thing('workflow_run', $tid) CONTENT $task;
            FOR $event IN $events { CREATE type::thing('workflow_event', $event.event_id) CONTENT $event; };
            COMMIT TRANSACTION;
        """, {"tid": task["task_id"], "task": task, "events": events})

    def get_task(self, task_id):
        rows = self.client.query("SELECT * FROM type::thing('workflow_run', $tid);", {"tid": task_id})[0]
        if not rows:
            raise KeyError(task_id)
        return clean(rows[0])

    def list_tasks(self, patient_id=None):
        rows = self.client.query("""SELECT task_id, patient_id, scenario, status, draft_revision,
            created_at, updated_at FROM workflow_run
            WHERE $pid = NULL OR patient_id = $pid ORDER BY created_at DESC;""", {"pid": patient_id})[0]
        return [clean(r) for r in rows]

    def get_drafts(self, task_id):
        return [clean(r) for r in self.client.query(
            "SELECT * FROM payer_draft WHERE task_id = $tid ORDER BY revision;", {"tid": task_id})[0]]

    def get_events(self, task_id):
        return [clean(r) for r in self.client.query(
            "SELECT * FROM workflow_event WHERE task_id = $tid ORDER BY sequence;", {"tid": task_id})[0]]

    def save_draft(self, task, draft, event, expected_revision):
        # Compare-and-swap and append in one transaction; races cannot create stray drafts/events.
        self.client.query("""
            BEGIN TRANSACTION;
            LET $current = (SELECT * FROM type::thing('workflow_run', $tid))[0];
            IF $current.draft_revision != $expected OR $current.context_hash != $hash
                OR $current.patient_id != $pid OR $current.status NOT IN ['ready_for_review', 'draft_saved'] {
                THROW 'CAREFLOW_CONCURRENT_WRITE';
            };
            UPDATE type::thing('workflow_run', $tid) CONTENT $task;
            CREATE type::thing('payer_draft', $did) CONTENT $draft;
            CREATE type::thing('workflow_event', $eid) CONTENT $event;
            COMMIT TRANSACTION;
        """, {"tid": task["task_id"], "pid": task["patient_id"], "hash": task["context_hash"],
              "expected": expected_revision, "task": task, "did": draft["draft_id"],
              "draft": draft, "eid": event["event_id"], "event": event})


class SurrealRepository(SurrealPatientRepository, SurrealWorkflowRepository):
    """Application facade sharing one configured client across the two repositories."""

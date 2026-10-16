"""Anchor-only form automation. Direct HTTP is limited to preflight/audit callbacks."""
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx


def end_session(client, session_id):
    client.sessions.delete_session(session_id, timeout=15)
    for _ in range(3):
        try:
            data = client.sessions.get_session(session_id, timeout=10).data
            if data and str(data.status).lower() in {"ended", "terminated", "deleted", "closed", "completed"}:
                return True
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404: return True
            raise
        time.sleep(0.5)
    return False


def journal(session_id, status):
    Path("/tmp/careflow/browser-journal.json").write_text(json.dumps({"session_id": session_id, "cleanup": status}))


def callback(manifest, kind, session_id, values=None):
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        body = {"kind": kind, "session_id": session_id}
        if values is not None: body["values"] = values
        response = client.post(manifest["payer_url"] + f'/mock-payer/requests/{manifest["task_id"]}/events',
            headers={"Authorization": "Bearer " + manifest["portal_token"]}, json=body)
        response.raise_for_status()


def drive(page, manifest):
    """Small testable browser contract; every write is a DOM click, never an API replacement."""
    url = manifest["payer_url"] + "/mock-payer/requests/" + manifest["task_id"]
    page.set_default_timeout(20000)
    response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    if response is None or response.status != 200: raise ValueError("PAYER_PAGE_UNREACHABLE")
    if page.locator('#payer-form').get_attribute('data-task') != manifest["task_id"]:
        raise ValueError("BROWSER_TASK_MISMATCH")
    if page.locator('[name="patient_id"]').input_value() != manifest["patient_id"]:
        raise ValueError("BROWSER_PATIENT_MISMATCH")
    if manifest["operation"] == "draft":
        for key, value in manifest["values"].items(): page.locator(f'[name="{key}"]').fill(str(value))
    for key, value in manifest["values"].items():
        if page.locator(f'[name="{key}"]').input_value() != str(value):
            raise ValueError("BROWSER_FIELD_MISMATCH")
    callback(manifest, "fields_verified", manifest["session_id"], manifest["values"])
    if manifest["operation"] == "draft":
        page.get_by_test_id("save-draft").click()
        page.get_by_test_id("draft-status").filter(has_text="Saved draft revision").wait_for(timeout=30000)
        page.reload(wait_until="domcontentloaded")
        if "Saved draft revision" not in page.get_by_test_id("draft-status").inner_text():
            raise ValueError("DRAFT_ACKNOWLEDGMENT_MISSING")
        return {"verified_values": manifest["values"], "acknowledgment": page.get_by_test_id("draft-status").inner_text()}
    if manifest["operation"] == "submit":
        if (page.locator('#payer-form').get_attribute('data-draft-hash') != manifest["draft_hash"]
                or int(page.locator('#payer-form').get_attribute('data-revision')) != manifest["draft_revision"]):
            raise ValueError("APPROVED_DRAFT_CHANGED")
        page.get_by_test_id("submit-mock").click()
        page.get_by_test_id("mock-receipt").filter(has_text="Simulated receipt").wait_for(timeout=30000)
        page.reload(wait_until="domcontentloaded")
        receipt_id = page.get_by_test_id("mock-receipt").get_attribute("data-receipt-id")
        if not receipt_id: raise ValueError("MOCK_RECEIPT_MISSING")
        return {"verified_values": manifest["values"], "receipt_id": receipt_id}
    raise ValueError("UNSUPPORTED_BROWSER_OPERATION")


def browser_task(manifest):
    from anchorbrowser import Anchorbrowser
    if urlparse(manifest["payer_url"]).scheme != "https": raise ValueError("PUBLIC_HTTPS_PORTAL_REQUIRED")
    headers = {"Authorization": "Bearer " + manifest["portal_token"]}
    # Verify remote reachability before allocating a browser. No draft is saved here.
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as check:
            response = check.get(manifest["payer_url"] + f'/mock-payer/requests/{manifest["task_id"]}', headers=headers)
            if response.status_code != 200: raise ValueError("PORTAL_PREFLIGHT_FAILED")
    except httpx.ConnectError:
        raise ValueError("PORTAL_CONNECTION_FAILED_CHECK_DAYTONA_EGRESS") from None
    client = Anchorbrowser(api_key=os.environ["ANCHOR_API_KEY"], max_retries=0)
    session_id = None
    outcome = {}
    try:
        session = client.sessions.create_session(session={"recording": {"active": True},
            "timeout": {"max_duration": 5, "idle_timeout": 1}, "tags": ["careflow", manifest["task_id"]]}, timeout=45)
        if not session.data or not session.data.id: raise ValueError("ANCHOR_SESSION_ID_MISSING")
        session_id = session.data.id
        journal(session_id, "pending")
        callback(manifest, "browser_created", session_id)
        manifest = {**manifest, "session_id": session_id}
        with client.browser.connect(session_id) as browser:
            context = browser.contexts[0]
            origin = urlparse(manifest["payer_url"]).netloc
            def restrict(route):
                target = urlparse(route.request.url)
                if target.scheme == "https" and target.netloc == origin: route.continue_()
                else: route.abort()
            context.route("**/*", restrict)
            context.set_extra_http_headers(headers)
            outcome = drive(context.pages[0], manifest)
        return {**outcome, "session_id": session_id, "recording_reference": "anchor-session:" + session_id}
    finally:
        if session_id:
            closed = False
            try: closed = end_session(client, session_id)
            finally:
                journal(session_id, "ended" if closed else "cleanup_pending")
                try: callback(manifest, "browser_closed" if closed else "browser_cleanup_pending", session_id)
                except Exception: pass  # Journal remains downloadable by the server.

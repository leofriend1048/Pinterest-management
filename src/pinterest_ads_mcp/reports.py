"""Pinterest's async analytics reports: create, wait, download, flatten."""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from .client import Client

PATH = "/ad_accounts/{ad_account_id}/reports"
DEAD = ("FAILED", "EXPIRED", "CANCELLED", "DOES_NOT_EXIST")


class ReportPending(Exception):
    """The report did not finish inside the wait window."""

    def __init__(self, token: str, state: str | None):
        self.token = token
        self.state = state
        super().__init__(f"Report {token} is still {state}")


def run(api: Client, body: dict, wait_seconds: int = 180, sleep=time.sleep) -> Any:
    """Create a report, poll until it is ready, and return its parsed content."""
    body = dict(body)
    body.setdefault("report_format", "JSON")
    created = api.request("POST", PATH, body=body)
    token = created.get("token")
    if not token:
        return created
    deadline = time.time() + max(10, min(wait_seconds, 600))
    delay = 2.0
    while True:
        status = api.request("GET", PATH, query={"token": token})
        state = status.get("report_status")
        if state == "FINISHED":
            break
        if state in DEAD:
            raise ValueError(f"Report ended with status {state}: {json.dumps(status)}")
        if time.time() + delay > deadline:
            raise ReportPending(token, state)
        sleep(delay)
        delay = min(delay * 1.5, 10)
    url = status.get("url")
    if not url:
        return status
    resp = api.download(url)
    if body["report_format"] == "CSV":
        return list(csv.DictReader(io.StringIO(resp.text)))
    return resp.json()


def flatten(result: Any) -> list[dict]:
    """JSON reports come back as {entity_id: [rows]}; return just the rows."""
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        rows: list[dict] = []
        for value in result.values():
            if isinstance(value, list):
                rows.extend(row for row in value if isinstance(row, dict))
        return rows
    return []

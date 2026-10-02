"""Analyst approval gate: nothing leaves the system without an approval record.

An approval binds a case, an action and (optionally) the exact payload the analyst saw.
`require` refuses if there is no approval, or if the payload changed after approval.
"""

import hashlib
import json
from pathlib import Path


class NotApproved(PermissionError):
    pass


def digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class ApprovalGate:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _file(self, case_id: str) -> Path:
        return self.root / f"{case_id}.approvals.jsonl"

    def approvals(self, case_id: str) -> list[dict]:
        f = self._file(case_id)
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]

    def approve(self, case_id: str, action: str, analyst: str, at: str, payload=None, note: str = "") -> dict:
        record = {
            "case_id": case_id,
            "action": action,
            "analyst": analyst,
            "approved_at": at,
            "payload_digest": digest(payload) if payload is not None else None,
            "note": note,
        }
        f = self._file(case_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def require(self, case_id: str, action: str, payload=None) -> dict:
        for record in reversed(self.approvals(case_id)):
            if record["action"] != action:
                continue
            if record["payload_digest"] is None or record["payload_digest"] == digest(payload):
                return record
            raise NotApproved(f"{action} on {case_id}: payload differs from what the analyst approved")
        raise NotApproved(f"{action} on {case_id}: no analyst approval")

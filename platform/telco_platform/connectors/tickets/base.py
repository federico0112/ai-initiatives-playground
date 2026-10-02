"""Ticket store interface and the file-backed machinery every simulated backend shares.

A case is a dict in TMF621 shape (spec S6 / O9): `case_id`, `ticket_type`, `severity`,
`status`, `sub_status`, `creation_date`, `related_objects`, `related_parties`, `notes`, plus
whatever fields the prototype adds (`figures`, `recommendation`, `hypotheses`...).

Each backend stores records in its own system's shape (a Jira issue, a Salesforce Case),
so swapping a simulated backend for the real API changes the transport, not the mapping.
"""

import json
from pathlib import Path
from typing import Protocol

from ...citations import Record, Source, make_cite

STATUSES = {"acknowledged", "inProgress", "pending", "held", "resolved", "closed", "cancelled", "rejected"}
SEVERITIES = {"low", "medium", "high", "critical"}
REQUIRED = ("ticket_type", "severity", "status", "sub_status", "creation_date")


class TicketSystem(Protocol):
    def create(self, case: dict) -> Record: ...
    def get(self, case_id: str) -> Record: ...
    def update(self, case_id: str, patch: dict, at: str, actor: str) -> Record: ...
    def add_note(self, case_id: str, author: str, at: str, text: str) -> Record: ...
    def search(self, **filters) -> list[Record]: ...


def validate_case(case: dict) -> None:
    missing = [f for f in REQUIRED if not case.get(f)]
    if missing:
        raise ValueError(f"case missing {missing}")
    if case["status"] not in STATUSES:
        raise ValueError(f"unknown TMF621 status {case['status']!r}")
    if case["severity"] not in SEVERITIES:
        raise ValueError(f"unknown severity {case['severity']!r}")


class FileTicketStore:
    """Stores one external-shaped JSON file per case plus an append-only history file.

    Subclasses set `system_dir` and implement `to_external`, `from_external` and `external_id`.
    """

    system_dir = "cases"

    def __init__(self, root: str | Path, id_prefix: str = "CASE", cite_system: str = "cases"):
        self.root = Path(root) / self.system_dir
        self.id_prefix = id_prefix
        self.cite_system = cite_system

    # mapping hooks
    def to_external(self, case: dict) -> dict:
        return case

    def from_external(self, record: dict) -> dict:
        return record

    def external_id(self, case_id: str) -> str:
        return case_id

    # storage
    def _file(self, case_id: str) -> Path:
        return self.root / f"{self.external_id(case_id)}.json"

    def _history(self, case_id: str) -> Path:
        return self.root / f"{self.external_id(case_id)}.history.jsonl"

    def _record(self, case: dict) -> Record:
        return Record(make_cite(self.cite_system, "case", case["case_id"]), case, Source(str(self._file(case["case_id"]))))

    def _write(self, case: dict, event: dict) -> Record:
        validate_case(case)
        self.root.mkdir(parents=True, exist_ok=True)
        self._file(case["case_id"]).write_text(json.dumps(self.to_external(case), indent=2, sort_keys=True) + "\n")
        with self._history(case["case_id"]).open("a") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
        return self._record(case)

    def _next_id(self) -> str:
        n = len(list(self.root.glob("*.history.jsonl"))) + 1 if self.root.exists() else 1
        return f"{self.id_prefix}-{n:04d}"

    def create(self, case: dict) -> Record:
        case = {"notes": [], "related_objects": [], "related_parties": [], **case}
        case.setdefault("case_id", self._next_id())
        if self._file(case["case_id"]).exists():
            raise ValueError(f"{case['case_id']} already exists")
        return self._write(case, {"at": case["creation_date"], "event": "created", "status": case["status"],
                                  "sub_status": case["sub_status"]})

    def get(self, case_id: str) -> Record:
        f = self._file(case_id)
        if not f.exists():
            raise KeyError(case_id)
        return self._record(self.from_external(json.loads(f.read_text())))

    def history(self, case_id: str) -> list[dict]:
        return [json.loads(line) for line in self._history(case_id).read_text().splitlines() if line.strip()]

    def update(self, case_id: str, patch: dict, at: str, actor: str) -> Record:
        if "case_id" in patch and patch["case_id"] != case_id:
            raise ValueError("case_id cannot change")
        case = {**self.get(case_id).data, **patch}
        return self._write(case, {"at": at, "event": "updated", "actor": actor, "fields": sorted(patch)})

    def add_note(self, case_id: str, author: str, at: str, text: str) -> Record:
        case = self.get(case_id).data
        case["notes"] = [*case.get("notes", []), {"author": author, "at": at, "text": text}]
        return self._write(case, {"at": at, "event": "note", "actor": author})

    def search(self, **filters) -> list[Record]:
        if not self.root.exists():
            return []
        out = []
        for f in sorted(self.root.glob("*.json")):
            case = self.from_external(json.loads(f.read_text()))
            if all(case.get(k) == v for k, v in filters.items()):
                out.append(self._record(case))
        return out

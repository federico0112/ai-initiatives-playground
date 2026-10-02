"""Simulated Jira: stores each case as a Jira REST issue (`GET /rest/api/3/issue/{key}` shape).

Core case fields map onto standard Jira fields; the full case rides in one custom field,
as a real deployment would configure. A real backend replaces file I/O with REST calls
and keeps `to_external` / `from_external` unchanged.
"""

import json

from ..base import register_connector
from .base import FileTicketStore

CASE_FIELD = "customfield_10500"  # placeholder id for the "Case detail (JSON)" custom field
STATUS_TO_JIRA = {
    "acknowledged": "Open",
    "inProgress": "In Progress",
    "pending": "Waiting for Support",
    "held": "On Hold",
    "resolved": "Resolved",
    "closed": "Closed",
    "cancelled": "Cancelled",
    "rejected": "Declined",
}
SEVERITY_TO_PRIORITY = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Highest"}


@register_connector("tickets", "jira")
class JiraSimTickets(FileTicketStore):
    system_dir = "jira/issues"

    def __init__(self, root, id_prefix: str = "CASE", cite_system: str = "cases", project_key: str = "OPS"):
        super().__init__(root, id_prefix, cite_system)
        self.project_key = project_key

    def external_id(self, case_id: str) -> str:
        return f"{self.project_key}-{case_id.rsplit('-', 1)[-1].lstrip('0') or '0'}"

    def to_external(self, case: dict) -> dict:
        return {
            "key": self.external_id(case["case_id"]),
            "fields": {
                "project": {"key": self.project_key},
                "issuetype": {"name": case["ticket_type"]},
                "summary": case.get("name") or f"{case['ticket_type']} {case['case_id']}",
                "status": {"name": STATUS_TO_JIRA[case["status"]]},
                "priority": {"name": SEVERITY_TO_PRIORITY[case["severity"]]},
                "labels": [case["sub_status"]],
                "created": case["creation_date"],
                "comment": {"comments": [{"author": {"displayName": n["author"]}, "created": n["at"], "body": n["text"]}
                                         for n in case.get("notes", [])]},
                CASE_FIELD: json.dumps(case, sort_keys=True),
            },
        }

    def from_external(self, issue: dict) -> dict:
        return json.loads(issue["fields"][CASE_FIELD])

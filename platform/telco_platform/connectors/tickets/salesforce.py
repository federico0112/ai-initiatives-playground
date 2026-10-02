"""Simulated Salesforce: stores each case as a Salesforce `Case` sObject.

Standard Case fields carry the core values; the full case rides in one custom long-text
field. A real backend swaps file I/O for the REST API (`/services/data/vXX.X/sobjects/Case`)
and keeps the mapping.
"""

import json

from ..base import register_connector
from .base import FileTicketStore

CASE_FIELD = "Case_Detail__c"
STATUS_TO_SF = {
    "acknowledged": "New",
    "inProgress": "Working",
    "pending": "Waiting on Customer",
    "held": "On Hold",
    "resolved": "Resolved",
    "closed": "Closed",
    "cancelled": "Closed",
    "rejected": "Closed",
}
SEVERITY_TO_PRIORITY = {"low": "Low", "medium": "Medium", "high": "High", "critical": "High"}


@register_connector("tickets", "salesforce")
class SalesforceSimTickets(FileTicketStore):
    system_dir = "salesforce/Case"

    def external_id(self, case_id: str) -> str:
        return f"{int(case_id.rsplit('-', 1)[-1]):08d}"  # CaseNumber

    def to_external(self, case: dict) -> dict:
        return {
            "attributes": {"type": "Case"},
            "CaseNumber": self.external_id(case["case_id"]),
            "Subject": case.get("name") or f"{case['ticket_type']} {case['case_id']}",
            "Type": case["ticket_type"],
            "Status": STATUS_TO_SF[case["status"]],
            "Priority": SEVERITY_TO_PRIORITY[case["severity"]],
            "Origin": case.get("origin", "Agent"),
            "CreatedDate": case["creation_date"],
            "IsClosed": case["status"] in ("closed", "cancelled", "rejected"),
            CASE_FIELD: json.dumps(case, sort_keys=True),
        }

    def from_external(self, sobject: dict) -> dict:
        return json.loads(sobject[CASE_FIELD])

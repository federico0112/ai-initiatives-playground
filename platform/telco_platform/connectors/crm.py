"""Simulated Salesforce CRM, read-only: who a carrier or customer is, and whom to contact.

Records are stored as Salesforce sObjects: `salesforce/Account/<Id>.json` and
`salesforce/Contact/<Id>.json` (Contact.AccountId links them, Contact.Role__c is the
escalation role such as "NOC L1" or "Account Manager").
"""

import json

from ..citations import Record, Source, make_cite
from ..sim.overlay import DataRoot
from .base import register_connector


@register_connector("crm", "salesforce")
class SalesforceSimCRM:
    def __init__(self, data_root: DataRoot, system: str = "crm"):
        self.data = data_root
        self.system = system

    def _load(self, rel: str, object_type: str) -> Record:
        p = self.data.require(rel)
        obj = json.loads(p.read_text())
        return Record(make_cite(self.system, object_type, obj["Id"]), obj, Source(str(p)))

    def get_account(self, account_id: str) -> Record:
        return self._load(f"salesforce/Account/{account_id}.json", "account")

    def contacts(self, account_id: str, role: str | None = None) -> list[Record]:
        out = []
        for rel in self.data.list("salesforce/Contact"):
            rec = self._load(rel, "contact")
            if rec.data.get("AccountId") == account_id and role in (None, rec.data.get("Role__c")):
                out.append(rec)
        return out

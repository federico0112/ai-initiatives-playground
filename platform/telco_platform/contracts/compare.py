"""Compare terms parsed from the signed PDF with the structured agreement record (the CLM side).

A mismatch is evidence in its own right (the PDF says 7 days' notice, the record says 5),
so each one can be written to the ledger as a rules `check` citing both sides.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .extract import Term


@dataclass
class Mismatch:
    name: str
    pdf_value: object
    record_value: object
    pdf_cite: str

    def ledger_entry(self, case_id: str, at: str, step: int, record_cite: str) -> dict:
        return {
            "case_id": case_id,
            "at": at,
            "step": step,
            "actor": {"kind": "rules", "name": "contracts.compare", "version": "1"},
            "kind": "check",
            "claim_class": "fact",
            "text": f"Signed contract and agreement record disagree on {self.name}: "
                    f"PDF says {self.pdf_value}, record says {self.record_value}.",
            "cites": [self.pdf_cite, record_cite],
        }


def _norm(v):
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return str(v).strip().lower()


def compare_terms(terms: list[Term], record: dict, field_map: dict[str, str] | None = None) -> list[Mismatch]:
    """field_map maps term name -> record field; by default they share a name. Missing record fields count as a mismatch."""
    field_map = field_map or {}
    out = []
    for t in terms:
        rec_value = record.get(field_map.get(t.name, t.name))
        if rec_value is None or _norm(rec_value) != _norm(t.value):
            out.append(Mismatch(t.name, t.value, rec_value, t.cite_id))
    return out

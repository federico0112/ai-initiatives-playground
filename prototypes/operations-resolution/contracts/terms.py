"""A2P SMS SLA terms: what prototype two reads from a supplier's signed contract.

`SLA_RULES` feed telco_platform.contracts.extract.extract_terms over the clauses parsed from the
PDF; `sla_record` flattens the structured agreement record (the CLM side) into the same names so
telco_platform.contracts.compare.compare_terms can check one against the other.
"""

from telco_platform.contracts.extract import TermRule
from telco_platform.sim.clock import parse_duration

_WORDS = {"five": 5, "ten": 10, "twenty": 20}


def _minutes(text: str) -> int:
    n, unit = text.split()
    return int(n) * (60 if unit.startswith("hour") else 1)


def _count(text: str) -> int:
    return int(text) if text.isdigit() else _WORDS[text.lower()]


SLA_RULES = [
    TermRule("delivery_target_pct", r"at least (\d+(?:\.\d+)?)%"),
    TermRule("dlr_coverage_target_pct", r"final receipt for at least (\d+(?:\.\d+)?)%"),
    TermRule("p1_raw_delivery_below_pct", r"P1[^.]*?below (\d+)%"),
    TermRule("p1_for_minutes", r"P1[^.]*?below \d+% for (\d+) (?:consecutive )?minutes", convert=int),
    TermRule("p1_response_minutes", r"P1[^.]*?response (?:within )?(\d+ (?:minutes?|hours?))", convert=_minutes),
    TermRule("p1_restoration_minutes", r"P1[^.]*?restoration (?:within )?(\d+ (?:minutes?|hours?))", convert=_minutes),
    TermRule("escalation_min_samples", r"at least (\w+) Supplier message IDs", convert=_count),
    TermRule("maintenance_notice_business_days", r"at least (\d+) business days", convert=int),
    TermRule("service_credit_pct", r"credits (\d+)% of"),
]


def sla_record(agreement: dict) -> dict:
    """The agreement record's SLA terms under the SLA_RULES names; empty when the agreement has no SLA."""
    sla = agreement.get("sla")
    if not sla:
        return {}
    out = {"delivery_target_pct": sla["delivery_target_pct"]}
    p1 = next((p for p in sla.get("priorities", []) if p["priority"] == "P1"), None)
    if p1:
        out |= {"p1_raw_delivery_below_pct": p1["raw_delivery_below_pct"], "p1_for_minutes": p1["for_minutes"],
                "p1_response_minutes": int(parse_duration(p1["response"]).total_seconds() // 60),
                "p1_restoration_minutes": int(parse_duration(p1["restoration"]).total_seconds() // 60)}
    for name in ("dlr_coverage_target_pct", "escalation_min_samples", "maintenance_notice_business_days"):
        if sla.get(name) is not None:
            out[name] = sla[name]
    if sla.get("service_credit"):
        out["service_credit_pct"] = sla["service_credit"]["pct_of_day_charges"]
    return out

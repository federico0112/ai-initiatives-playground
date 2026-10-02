"""Base agreement fields both prototypes require (TMF651-shaped, flattened).

Each prototype adds its own required fields (rate terms, SLA terms) through `extra`.
"""

BASE_REQUIRED = (
    "agreement_id",
    "document_number",
    "name",
    "agreement_type",
    "status",
    "version",
    "period_start",
    "buyer_party",
    "seller_party",
    "currency",
    "clauses",
)
STATUSES = {"active", "expired", "terminated"}


def validate_agreement(agreement: dict, extra: tuple[str, ...] = ()) -> list[str]:
    """Return a list of problems; empty means the record passes."""
    problems = [f"missing {f}" for f in (*BASE_REQUIRED, *extra) if agreement.get(f) in (None, "", [])]
    if agreement.get("status") and agreement["status"] not in STATUSES:
        problems.append(f"unknown status {agreement['status']!r}")
    clause_ids = [c.get("clause_id") for c in agreement.get("clauses") or []]
    if len(clause_ids) != len(set(clause_ids)):
        problems.append("duplicate clause_id")
    return problems

"""Turn parsed clauses into structured terms, each citing the clause it came from.

Each prototype declares its own rules (wholesale voice terms, SMS SLA terms) in its
`contracts/terms.py`; this module only applies them. A rule names the clause to read
(or None to search all clauses), a regex with one capture group, and a converter.
"""

import re
from dataclasses import dataclass
from typing import Callable

from ..citations import Record


@dataclass(frozen=True)
class TermRule:
    name: str
    pattern: str
    clause_id: str | None = None
    convert: Callable[[str], object] = str


@dataclass
class Term:
    name: str
    value: object
    cite_id: str
    evidence: str
    method: str = "rule"


def extract_terms(clauses: list[Record], rules: list[TermRule]) -> tuple[list[Term], list[str]]:
    """Return (terms found, names of rules that matched nothing)."""
    terms, missing = [], []
    for rule in rules:
        candidates = [c for c in clauses if rule.clause_id in (None, c.data["clause_id"])]
        for c in candidates:
            m = re.search(rule.pattern, f"{c.data['title']}. {c.data['text']}", re.IGNORECASE)
            if m:
                terms.append(Term(rule.name, rule.convert(m.group(1)), c.cite_id, m.group(0)))
                break
        else:
            missing.append(rule.name)
    return terms, missing

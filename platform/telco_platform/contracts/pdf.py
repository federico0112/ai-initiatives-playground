"""Parse a contract PDF into numbered clauses, each a cited record.

A clause starts on a line that begins with its number ("6.3 Rate increase notice. ...")
and runs until the next numbered line. Text before the first clause is the preamble.
Each clause cites `sim://<system>/agreement/<agreement_id>#clause-<n>`, the same anchor
the structured agreement record uses, so a statement can point at the exact clause.

Text PDFs only (pypdf). Scanned or table-heavy contracts need an OCR/layout backend
such as docling, which rag-agent already uses; that slots in behind `extract_pages`.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

from ..citations import Record, Source, make_cite

_CLAUSE = re.compile(r"^(\d+(?:\.\d+)+)\s+(\S.*)$")


@dataclass
class ParsedContract:
    agreement_id: str
    preamble: str
    clauses: list[Record] = field(default_factory=list)

    def clause(self, clause_id: str) -> Record:
        for c in self.clauses:
            if c.data["clause_id"] == clause_id:
                return c
        raise KeyError(f"clause {clause_id} not in {self.agreement_id}")


def extract_pages(pdf_path: str | Path) -> list[str]:
    return [page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages]


def parse_contract(pdf_path: str | Path, agreement_id: str, system: str = "contracts") -> ParsedContract:
    preamble: list[str] = []
    clauses: list[dict] = []
    for page_no, text in enumerate(extract_pages(pdf_path), start=1):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _CLAUSE.match(line)
            if m:
                clauses.append({"clause_id": m.group(1), "page": page_no, "lines": [m.group(2)]})
            elif clauses:
                clauses[-1]["lines"].append(line)
            else:
                preamble.append(line)

    records = []
    for c in clauses:
        body = " ".join(c["lines"])
        title, sep, rest = body.partition(". ")
        if not sep or len(title) > 80:
            title, rest = "", body
        records.append(Record(
            cite_id=make_cite(system, "agreement", agreement_id, fragment=f"clause-{c['clause_id']}"),
            data={"clause_id": c["clause_id"], "title": title, "text": rest.strip(), "page": c["page"]},
            source=Source(str(pdf_path), c["page"]),
        ))
    return ParsedContract(agreement_id, " ".join(preamble), records)

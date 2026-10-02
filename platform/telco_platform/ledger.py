"""Append-only evidence ledger, one JSONL file per case.

Entries are never edited; a correction is a new entry with `supersedes`. The writer
enforces the rule both prototypes share: agents interpret numbers, they never introduce
them. Only a `rules` actor may write a `calc`, and an agent entry that states a number
(a decimal with two or more places, or a percentage) must cite a calc whose result or
one of whose inputs is that number.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .citations import make_cite, parse_cite

KINDS = {"record_ref", "check", "calc", "statement", "decision", "request", "approval"}
ACTOR_KINDS = {"rules", "agent", "analyst", "system"}
CLAIM_CLASSES = {"fact", "calculation", "interpretation", "missing_evidence"}
REQUIRED = ("case_id", "at", "step", "actor", "kind", "text", "cites")
# A percentage, or a decimal with two or more places ("1,820.00", "0.0185", "7.41%").
# "clause 6.3" and dates are not numbers in this sense.
NUMBER_RE = re.compile(r"(?<![\w.,-])\d[\d,]*(?:\.\d+)?(?=\s?%)|(?<![\w.,-])\d[\d,]*\.\d{2,}")


class LedgerError(ValueError):
    pass


def _num(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def numbers_in(text: str) -> list[Decimal]:
    return [_num(m.group(0)) for m in NUMBER_RE.finditer(text)]


class Ledger:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _file(self, case_id: str) -> Path:
        return self.root / f"{case_id}.jsonl"

    def read(self, case_id: str) -> list[dict]:
        f = self._file(case_id)
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]

    def append(self, entry: dict) -> dict:
        missing = [k for k in REQUIRED if k not in entry]
        if missing:
            raise LedgerError(f"missing fields: {missing}")
        case_id = entry["case_id"]
        existing = self.read(case_id)
        self._validate(entry, existing)
        entry = dict(entry)
        entry["entry_id"] = f"LED-{case_id.rsplit('-', 1)[-1]}-{len(existing) + 1:04d}"
        f = self._file(case_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def _validate(self, entry: dict, existing: list[dict]) -> None:
        kind, actor = entry["kind"], entry["actor"]
        if kind not in KINDS:
            raise LedgerError(f"unknown kind {kind!r}")
        if actor.get("kind") not in ACTOR_KINDS:
            raise LedgerError(f"unknown actor kind {actor.get('kind')!r}")
        claim = entry.get("claim_class")
        if claim is not None and claim not in CLAIM_CLASSES:
            raise LedgerError(f"unknown claim_class {claim!r}")
        for c in entry["cites"] + entry.get("contradicts", []):
            parse_cite(c)
        if not entry["cites"] and claim != "missing_evidence":
            raise LedgerError("cites may be empty only for missing_evidence statements")
        if entry.get("supersedes") and entry["supersedes"] not in {e["entry_id"] for e in existing}:
            raise LedgerError(f"supersedes unknown entry {entry['supersedes']}")

        if kind == "calc":
            if actor["kind"] != "rules":
                raise LedgerError("only a rules actor may write a calc")
            calc = entry.get("calc") or {}
            for k in ("calc_id", "formula", "inputs", "result"):
                if k not in calc:
                    raise LedgerError(f"calc missing {k}")
            return
        if "calc" in entry:
            raise LedgerError("calc payload only allowed on kind=calc")

        stated = numbers_in(entry["text"]) if actor["kind"] == "agent" else []
        if not stated:
            return
        calcs = {e["calc"]["calc_id"]: e["calc"] for e in existing if e["kind"] == "calc"}
        cited = set()
        for c in entry["cites"]:
            p = parse_cite(c)
            if p.system == "ledger" and p.object_type == "calc" and p.object_id in calcs:
                calc = calcs[p.object_id]
                for v in [calc["result"], *calc["inputs"].values()]:
                    try:
                        cited.add(_num(str(v)))
                    except InvalidOperation:
                        pass  # a cite_id input, not a number
        unbacked = [n for n in stated if n not in cited]
        if unbacked:
            raise LedgerError(f"numbers not backed by a cited calc: {[str(n) for n in unbacked]}")


def calc_cite(calc_id: str) -> str:
    return make_cite("ledger", "calc", calc_id)

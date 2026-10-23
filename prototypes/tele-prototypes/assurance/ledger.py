"""Evidence ledger (simulated-systems.md, section L): append-only JSONL per case.

The writer enforces the two rules every step relies on:
- an amount is only produced by a `calc` entry written by a `rules` actor;
- any other entry may mention an amount only if that exact figure is the result of a calc it
  cites, and must cite at least one record unless it is a `missing_evidence` statement.
"""
import json
import re

from .store import q2

KINDS = {"record_ref", "check", "calc", "statement", "decision", "request", "approval"}
CLAIM_CLASSES = {"fact", "calculation", "interpretation", "missing_evidence"}
# Two-decimal figures (amounts, percentages). Rates carry four decimals and dates have none.
AMOUNT_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})*\.\d{2}(?![\d])|(?<![\d.,])\d+\.\d{2}(?![\d])")


class LedgerError(ValueError):
    pass


class Ledger:
    def __init__(self, systems, case_id, clock):
        self.case_id = case_id
        self.clock = clock
        self.rel = f"ledger/{case_id}.jsonl"
        self.path = systems.root.write_path(self.rel)
        self.entries = []
        self.calcs = {}
        self._num = case_id.split("-")[-1]

    def get_calc(self, calc_id):
        return self.calcs.get(calc_id)

    def calc_cite(self, calc_id):
        return f"sim://ledger/calc/{calc_id}"

    def append(self, step, actor, kind, text, cites, claim_class=None, calc=None, supersedes=None, **extra):
        if kind not in KINDS:
            raise LedgerError(f"unknown kind {kind}")
        if kind == "calc" and actor["kind"] != "rules":
            raise LedgerError("only a rules actor may write a calc")
        if kind == "statement" and claim_class not in CLAIM_CLASSES:
            raise LedgerError("a statement needs a claim_class")
        if not cites and claim_class != "missing_evidence":
            raise LedgerError(f"entry without citations: {text}")
        if kind != "calc":
            allowed = {str(q2(c["result"])) for cid in cites
                       if cid.startswith("sim://ledger/calc/") and (c := self.calcs.get(cid.rsplit("/", 1)[1]))}
            for m in AMOUNT_RE.findall(text):
                if m.replace(",", "") not in allowed:
                    raise LedgerError(f"amount {m} is not the result of a cited calc: {text}")
        entry = {"entry_id": f"LED-{self._num}-{len(self.entries) + 1:04d}", "case_id": self.case_id,
                 "at": self.clock.iso(), "step": step, "actor": actor, "kind": kind, "text": text,
                 "cites": list(cites)}
        if claim_class:
            entry["claim_class"] = claim_class
        if calc:
            entry["calc"] = calc
        if supersedes:
            entry["supersedes"] = supersedes
        entry.update(extra)
        self.entries.append(entry)
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def calc(self, step, actor, name, formula, inputs, result, unit, text, cites):
        """Records a deterministic result. `result` is a Decimal; returns the calc cite_id."""
        calc_id = f"CALC-{self._num}-{len(self.calcs) + 1:03d}"
        calc = {"calc_id": calc_id, "name": name, "formula": formula, "inputs": inputs,
                "result": str(result), "unit": unit}
        self.calcs[calc_id] = calc
        self.append(step, actor, "calc", text, cites, calc=calc)
        return self.calc_cite(calc_id)

    def result(self, calc_cite):
        return self.calcs[calc_cite.rsplit("/", 1)[1]]["result"]

    def read(self):
        return list(self.entries)

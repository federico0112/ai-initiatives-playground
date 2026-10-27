#!/usr/bin/env python3
"""Scores the prototype against the answer keys (dataset/answer-keys/<id>.json).

Only this file reads the answer keys; the steps never do. Besides the expected figures and
decisions it checks the evidence rules: every ledger citation resolves to a record or a calc, and
nothing in the `must_not` lists happens.

    python evaluate.py              # the base scenario, exit 1 on any failure
    python evaluate.py --packet base  # print the dispute packet for one scenario
"""
import json
import os
import sys

from assurance.runner import DATASET, list_scenarios, run_scenario
from assurance.store import DataRoot, Systems

KEYS = os.path.join(DATASET, "answer-keys")


def load_key(scenario_id):
    return json.load(open(os.path.join(KEYS, f"{scenario_id}.json")))


def _causes(p):
    return p.get("causes") or []


def unresolved_cites(result):
    """Ledger citations that resolve to nothing."""
    if not result["ledger"]:
        return []
    systems = Systems(DataRoot(os.path.join(DATASET, "sim-data", result["scenario"]), result["workspace"]))

    class _L:
        calcs = {e["calc"]["calc_id"]: e["calc"] for e in result["ledger"] if e["kind"] == "calc"}

        def get_calc(self, cid):
            return self.calcs.get(cid)

    led = _L()
    portal = {s["cite_id"] for s in result["submissions"]} | {r["cite_id"] for r in result["responses"]}
    bad = []
    for e in result["ledger"]:
        for c in e["cites"]:
            if c not in portal and systems.resolve(c, led) is None:
                bad.append((e["entry_id"], c))
    return bad


def evaluate(result, key):
    """Returns [(name, ok, detail)]."""
    out = []

    def eq(name, got, want):
        out.append((name, str(got) == str(want), f"got {got}, want {want}"))

    first, last = (result["passes"][0], result["passes"][-1]) if result["passes"] else ({}, {})
    figs = last.get("figures") or {}
    rec = last.get("recommendation") or {}

    eq("case opens", result["case"] is not None, True)
    c, k = result["case"], key["case"]
    for f in ("case_id", "correlation_id", "severity", "dispute_deadline", "payment_due"):
        eq(f"case.{f}", c[f], k[f])
    for f, want in key["figures"].items():
        eq(f"figures.{f}", figs.get(f), want)
    v = first["validation"]
    eq("step 2 not blocking", v["blocking"], False)
    eq("usage duplicate supplier_msg_id", v["duplicate_supplier_msg_ids"], key["step2_data_quality"]["duplicate_supplier_msg_ids_in_usage"])
    for reason, r in key["step2_data_quality"]["rejects"].items():
        eq(f"rejects.{reason}", v["rejects_by_reason"].get(reason, {}).get("count"), r["count"])
    for f in ("delivered", "undelivered", "expired", "no_receipt"):
        eq(f"receipts.{f}", v["delivery_receipts"][f], key["step2_data_quality"]["delivery_receipts"][f])
    exc = [(r["line_no"], r["network_group"], e["type"], r["invoiced"], r["expected"], r["variance"])
           for r in last["reconciliation"] for e in r["exceptions"]]
    eq("step 3 exceptions", exc, [(e["invoice_line"], e["network_group"], e["exception_type"], e["invoiced"],
                                   e["expected"], e["variance"]) for e in key["step3_exceptions"]])
    eq("step 3 matches", [f"line {r['line_no']} {r['network_group']} {r['invoiced']}" for r in last["reconciliation"]
                          if r["status"] == "match"], key["step3_matches"])
    for kc in key["step4_causes"]:
        got = next((c for c in _causes(last) if c["group"] == kc["group"]), None)
        eq(f"step 4 {kc['group']} claim classes", got and got["claim_class"], kc["claim_class"])
    mx = next(c for c in _causes(last) if c["group"] == "MX-ALT")
    cited = {x for e in result["ledger"] if e["entry_id"] in mx["statements"] for x in e["cites"]}
    missing = [c for c in key["step4_causes"][0]["cites"] if c not in cited]
    out.append(("step 4 MX-ALT cites the answer key records", not missing, f"missing {missing}"))
    co = next(c for c in _causes(last) if c["group"] == "CO-AND")
    eq("step 4 CO-AND evidence", (co["evidence"]["rejects"], co["evidence"]["segments"], co["evidence"]["binds"]),
       (10103, 12000, ["SMPP-NB-07"]))
    eq("decision", rec["decision"], key["step6_decision"]["decision"])
    eq("decision amount", rec["amount"], key["step6_decision"]["amount"])
    out.append(("internal action fixes the connection map",
                any("SMPP-NB-07" in a and "2026-09-21" in a and "10103" in a for a in rec["internal_actions"]),
                rec["internal_actions"]))
    sub = result["submissions"][0]
    for f, want in key["step7_submission"].items():
        eq(f"submission.{f}", sub.get(f), want)
    eq("one claim only", len(result["submissions"]), 1)
    eq("carrier credit", result["responses"][-1]["credit_amount"], "1820.00")
    out.append(("must not: dispute CO-AND", all("Colombia" not in s["product"] for s in result["submissions"]), ""))

    # Evidence rules
    eq("final status", result["status"], "resolved")
    out.append(("no BILLING_BASIS claim (undelivered messages are billable)",
                all(s["reason_code"] != "BILLING_BASIS" for s in result["submissions"]), ""))
    bad = unresolved_cites(result)
    out.append(("every ledger citation resolves", not bad, f"{len(bad)} unresolved, e.g. {bad[:3]}"))
    return out


def main(argv):
    if argv[:1] == ["--packet"]:
        print(run_scenario(argv[1])["packet"] or "(no packet: the case did not reach the analyst gate)")
        return 0
    ids = argv or list_scenarios()
    failed = 0
    for sid in ids:
        result = run_scenario(sid)
        checks = evaluate(result, load_key(sid))
        bad = [c for c in checks if not c[1]]
        failed += len(bad)
        print(f"{'PASS' if not bad else 'FAIL'} {sid}: {len(checks) - len(bad)}/{len(checks)} checks, status {result['status']}")
        for name, _ok, detail in bad:
            print(f"     FAIL {name}: {detail}")
    print(f"\n{failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

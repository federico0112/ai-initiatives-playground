"""Scenario runner (simulated-systems.md section 8): owns the sim clock, delivers staged files when
the case asks for evidence, runs steps 1-6, stops at the analyst gate (step 7), then submits the
approved claims to the S7 stub and plays its scripted responses.

A run is a pure function of (scenario, analyst decisions): the web app replays a scenario with the
decisions taken so far instead of keeping server-side state.
"""
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import yaml

from . import steps
from .store import D, DataRoot, Systems, money, parse_dt, q2

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.normpath(os.path.join(HERE, "..", "dataset"))
DEFAULT_THRESHOLDS = {
    "open_case_variance_pct_of_invoice": 2.0,          # confirmed by Federico 2026-10-02
    "request_evidence_unresolved_pct_of_invoice": 1.0,  # confirmed by Federico 2026-10-02
    "evidence_min_days_before_deadline": 3,            # closer than this, dispute the supported part and reserve the rest
}
MAX_EVIDENCE_ROUNDS = 3


class Clock:
    def __init__(self, start):
        self.now = start

    def iso(self):
        return self.now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def advance_to(self, t):
        self.now = max(self.now, t)


def duration(s):
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?)?", s)
    return timedelta(days=int(m.group(1) or 0), hours=int(m.group(2) or 0))


def list_scenarios(dataset_dir=DATASET):
    sim = os.path.join(dataset_dir, "sim-data")
    if not os.path.isdir(sim):
        return []
    names = [n for n in os.listdir(sim) if os.path.exists(os.path.join(sim, n, "scenario.yaml"))]
    return sorted(names, key=lambda n: (n != "base", n))


def load_scenario(scenario_id, dataset_dir=DATASET):
    scn = yaml.safe_load(open(os.path.join(dataset_dir, "sim-data", scenario_id, "scenario.yaml")))
    for ev in scn["events"]:
        if True in ev:  # YAML 1.1 reads an unquoted `on:` key as boolean true
            ev["on"] = ev.pop(True)
    return scn


def _dt(v):
    return v if isinstance(v, datetime) else parse_dt(str(v))


def run_scenario(scenario_id, decisions=None, dataset_dir=DATASET, workspace=None):
    """Runs one scenario end to end.

    decisions: None auto-approves the recommendation at the analyst gate. A list is consumed in
    order ({"action": "approve"|"edit"|"return", "amount", "reason", "analyst"}); when it runs out
    the run stops at the gate with status `analyst_review`.
    """
    scn = load_scenario(scenario_id, dataset_dir)
    thresholds = dict(DEFAULT_THRESHOLDS, **(scn.get("thresholds") or {}))
    clock = Clock(_dt(scn["sim_start"]))
    ws = workspace or tempfile.mkdtemp(prefix=f"assurance-{scenario_id}-")
    scn_dir = os.path.join(dataset_dir, "sim-data", scenario_id)
    systems = Systems(DataRoot(scn_dir, ws))
    ctx = steps.CaseContext(systems, clock, thresholds)
    timeline = []
    out = {"scenario": scenario_id, "sim_start": clock.iso(), "thresholds": thresholds, "timeline": timeline,
           "passes": [], "submissions": [], "responses": [], "packet": None, "workspace": ws}

    def log(text):
        timeline.append({"at": clock.iso(), "text": text})

    deliveries = [e for e in scn["events"] if e.get("action") == "deliver_invoice"]
    clock.advance_to(_dt(deliveries[0]["at"]))
    invoice_id = deliveries[0]["invoice_id"]
    log(f"Invoice {invoice_id} delivered to the carrier invoice inbox")

    # ---- step 1
    sc = steps.screen_invoice(ctx, invoice_id)
    out["screening"] = {k: str(sc[k]) for k in ("invoiced", "expected", "variance", "variance_pct", "threshold")}
    out["screening"]["cites"] = [sc["invoice"]["cite_id"], sc["run"]["cite_id"]]
    out["invoice"] = {"invoice_id": invoice_id, "currency": sc["invoice"]["currency"],
                      "carrier": sc["invoice"]["seller_party"]["name"],
                      "buyer": sc["invoice"]["buyer_party"]["name"],
                      "period_start": sc["invoice"]["period_start"], "period_end": sc["invoice"]["period_end"],
                      "agreement_ref": sc["invoice"]["agreement_ref"]}
    if not sc["opens"]:
        out["status"] = "no_case"
        log(f"Variance {sc['variance_pct']}% of invoiced is within the {sc['threshold']}% tolerance; no case opened")
        return _finish(out, ctx)
    steps.open_case(ctx, sc)
    log(f"Step 1: case {ctx.case['case_id']} opened, variance {money(sc['variance'])} ({sc['variance_pct']}% of invoiced)")

    # ---- steps 2-6, re-entered after each evidence delivery
    for _round in range(MAX_EVIDENCE_ROUNDS + 1):
        p = {"at": clock.iso()}
        v = ctx.validation = steps.validate(ctx)
        p["validation"] = v
        if v["blocking"]:
            blocked = [c["check"] for c in v["checks"] if c["status"] == "block"]
            log(f"Step 2: blocking data issue ({', '.join(blocked)})")
            rec = {"decision": "request_evidence", "evidence_items": v["requests"], "source": "step2",
                   "rationale": "Step 2 found a blocking data issue: " + "; ".join(
                       f"{c['check']}: {c['text']}" for c in v["checks"] if c["status"] == "block"),
                   "created_at": clock.iso()}
            ctx.case["recommendation"] = rec
        else:
            log("Step 2: data validated" + (" (non-blocking flags: " + ", ".join(
                c["check"] for c in v["checks"] if c["status"] == "flag") + ")"))
            p["reconciliation"] = recon = steps.reconcile(ctx)
            log("Step 3: " + ", ".join(f"line {r['line_no']} {r['status']}" for r in recon if r["line_no"]))
            ctx.detail_findings = {}
            p["causes"] = causes = steps.investigate(ctx, recon)
            if ctx.detail_findings:
                p["detail_findings"] = ctx.detail_findings
            log("Step 4: " + (", ".join(f"{c['group']} {c['classification']}" for c in causes) or "no causes"))
            p["figures"], ids = steps.size_exposure(ctx, causes)
            log(f"Step 5: supported {money(p['figures']['supported'])}, explained {money(p['figures']['explained_carrier_favour'])}, "
                f"unresolved {money(p['figures']['unresolved'])}")
            rec = steps.recommend(ctx, p["figures"], ids, causes)
            log(f"Step 6: recommend {rec['decision']}" + (f" {money(rec['amount'])}" if rec.get("amount") else ""))
        p["recommendation"] = dict(rec)
        out["passes"].append(p)
        if rec["decision"] != "request_evidence":
            break
        if not _request_evidence(ctx, scn, scn_dir, rec["evidence_items"], log):
            out["status"] = "awaiting_evidence"
            return _finish(out, ctx)
    else:
        out["status"] = "awaiting_evidence"
        return _finish(out, ctx)

    # ---- step 7: analyst gate
    pending = None if decisions is None else list(decisions)
    while True:
        ctx.set_status("pending", "analyst_review", "waiting for analyst sign-off")
        if pending is not None and not pending:
            out["status"] = "analyst_review"
            log("Step 7: waiting for the analyst")
            return _finish(out, ctx)
        d = {"action": "approve", "analyst": "demo.analyst", "reason": "auto-approved as recommended"} \
            if pending is None else pending.pop(0)
        if d["action"] != "return":
            break
        note = ctx.ledger.append(7, {"kind": "analyst", "name": d.get("analyst", "analyst"), "version": "-"}, "approval",
                                 f"Analyst returned the case: {d.get('reason', '')}", [ctx.case["cite_id"]], decision="return")
        ctx.case["notes"].append({"author": d.get("analyst", "analyst"), "at": clock.iso(), "text": d.get("reason", "")})
        ctx.set_status("inProgress", "returned", "returned by analyst")
        log("Step 7: analyst returned the case with notes; step 6 revises")
        rec = steps.recommend(ctx, p["figures"], ids, causes, notes=note)
        out["passes"][-1]["recommendation"] = dict(rec)
    _approve(ctx, out, d, rec, causes, scn, log)
    return _finish(out, ctx)


def run_invoice(invoice, dataset_dir=DATASET, reference="base", received_at=None, workspace=None):
    """Checks one uploaded invoice against the reference data and reports what it finds.

    The invoice is written into the run workspace, which the data root reads before the reference
    folder, so steps 1-6 run against it exactly as they do for a seeded scenario. There is no
    scripted carrier here: the run stops at the analyst gate, or earlier if it needs evidence.
    """
    inv_id = invoice.get("invoice_id") or "UPLOAD"
    ws = workspace or tempfile.mkdtemp(prefix="assurance-upload-")
    ref_dir = os.path.join(dataset_dir, "sim-data", reference)
    root = DataRoot(ref_dir, ws)
    with open(root.write_path(f"invoices/inbox/{inv_id}/invoice.json"), "w") as f:
        json.dump(invoice, f, indent=1)
    clock = Clock(_dt(received_at) if received_at else datetime.now(timezone.utc).replace(microsecond=0))
    with open(root.write_path(f"invoices/inbox/{inv_id}/envelope.json"), "w") as f:
        json.dump({"message_id": f"<upload-{inv_id}>", "from": "upload", "to": "assurance",
                   "received_at": clock.iso(), "subject": f"Uploaded invoice {inv_id}",
                   "attachments": ["invoice.json"]}, f, indent=1)

    systems = Systems(root)
    ctx = steps.CaseContext(systems, clock, dict(DEFAULT_THRESHOLDS))
    timeline = []
    out = {"scenario": inv_id, "uploaded": True, "reference": reference, "sim_start": clock.iso(),
           "thresholds": ctx.thresholds, "timeline": timeline, "passes": [], "submissions": [],
           "responses": [], "packet": None, "workspace": ws}

    def log(text):
        timeline.append({"at": clock.iso(), "text": text})

    log(f"Invoice {inv_id} uploaded and read into the inbox")
    sc = steps.screen_invoice(ctx, inv_id)
    out["screening"] = {k: str(sc[k]) for k in ("invoiced", "expected", "variance", "variance_pct", "threshold")}
    out["screening"]["cites"] = [sc["invoice"]["cite_id"], sc["run"]["cite_id"]] if sc["run"] else [sc["invoice"]["cite_id"]]
    out["invoice"] = {"invoice_id": inv_id, "currency": sc["invoice"]["currency"],
                      "carrier": sc["invoice"]["seller_party"]["name"],
                      "buyer": sc["invoice"]["buyer_party"]["name"],
                      "period_start": sc["invoice"]["period_start"], "period_end": sc["invoice"]["period_end"],
                      "agreement_ref": sc["invoice"]["agreement_ref"]}
    if not sc["opens"]:
        out["status"] = "no_case"
        log(f"Variance {sc['variance_pct']}% of invoiced is within the {sc['threshold']}% tolerance; no case opened")
        return _finish(out, ctx)
    steps.open_case(ctx, sc)
    log(f"Step 1: case {ctx.case['case_id']} opened, variance {money(sc['variance'])} ({sc['variance_pct']}% of invoiced)")

    p = {"at": clock.iso()}
    v = ctx.validation = steps.validate(ctx)
    p["validation"] = v
    if v["blocking"]:
        blocked = [c["check"] for c in v["checks"] if c["status"] == "block"]
        log(f"Step 2: blocking data issue ({', '.join(blocked)})")
        p["recommendation"] = {"decision": "request_evidence", "evidence_items": v["requests"], "source": "step2",
                               "rationale": "Step 2 found a blocking data issue: " + "; ".join(
                                   f"{c['check']}: {c['text']}" for c in v["checks"] if c["status"] == "block"),
                               "created_at": clock.iso()}
        ctx.case["recommendation"] = p["recommendation"]
        out["passes"].append(p)
        out["status"] = "awaiting_evidence"
        return _finish(out, ctx)

    log("Step 2: data validated" + (" (non-blocking flags: " + ", ".join(
        c["check"] for c in v["checks"] if c["status"] == "flag") + ")"))
    p["reconciliation"] = recon = steps.reconcile(ctx)
    log("Step 3: " + ", ".join(f"line {r['line_no']} {r['status']}" for r in recon if r["line_no"]))
    ctx.detail_findings = {}
    p["causes"] = causes = steps.investigate(ctx, recon)
    if ctx.detail_findings:
        p["detail_findings"] = ctx.detail_findings
    log("Step 4: " + (", ".join(f"{c['group']} {c['classification']}" for c in causes) or "no causes"))
    p["figures"], ids = steps.size_exposure(ctx, causes)
    log(f"Step 5: supported {money(p['figures']['supported'])}, explained {money(p['figures']['explained_carrier_favour'])}, "
        f"unresolved {money(p['figures']['unresolved'])}")
    rec = steps.recommend(ctx, p["figures"], ids, causes)
    log(f"Step 6: recommend {rec['decision']}" + (f" {money(rec['amount'])}" if rec.get("amount") else ""))
    p["recommendation"] = dict(rec)
    out["passes"].append(p)

    if rec["decision"] == "request_evidence":
        ctx.set_status("pending", "awaiting_evidence", "evidence requested")
        out["status"] = "awaiting_evidence"
        log("Evidence is needed before the claim can be sized; the case waits for it")
        return _finish(out, ctx)
    ctx.set_status("pending", "analyst_review", "waiting for analyst sign-off")
    out["status"] = "analyst_review"
    log("Step 7: waiting for the analyst")
    return _finish(out, ctx)


def _request_evidence(ctx, scn, scn_dir, items, log):
    """Records the requests, then delivers whatever the scenario scripts for them. Returns True if
    at least one request was fulfilled (the case re-enters step 2)."""
    case, led, clock = ctx.case, ctx.ledger, ctx.clock
    ctx.set_status("pending", "awaiting_evidence", "evidence requested")
    fulfilled = False
    for item in items:
        req = dict(item, request_id=f"REQ-{case['case_id'][-4:]}-{len(case['evidence_requests']) + 1:02d}",
                   sent_at=clock.iso(), due_at=None, fulfilled_at=None, fulfilled_by_cite_ids=[])
        case["evidence_requests"].append(req)
        led.append(2 if case["recommendation"].get("source") == "step2" else 6, steps.rules("evidence-request"), "request",
                   f"Evidence requested from {item['to']}: {item['item']}" +
                   (f" for {item['network_group']} {item['period']}" if item.get("network_group") else "") + ".",
                   [case["cite_id"]], request=req["request_id"])
        log(f"Evidence requested from {item['to']}: {item['item']}" + (f" ({item['network_group']})" if item.get("network_group") else ""))
        for ev in scn["events"]:
            m = ev.get("match") or {}
            if ev.get("on") == "evidence_request" and all(req.get(k) == v for k, v in m.items()):
                at = parse_dt(req["sent_at"]) + duration(ev["after"])
                src, dst = os.path.join(scn_dir, ev["from"]), ctx.sys.root.write_path(ev["path"])
                shutil.copyfile(src, dst)
                clock.advance_to(at)
                parts = ev["path"].split("/")
                cite = (f"sim://invoices/invoice/{parts[2]}/detail" if ev["path"].endswith("message_detail.csv.gz")
                        else f"sim://{ev['path']}")
                req.update(fulfilled_at=clock.iso(), fulfilled_by_cite_ids=[cite])
                led.append(2, steps.rules("scenario-runner"), "record_ref",
                           f"Requested evidence received: {ev['path']}; the case re-enters step 2.", [case["cite_id"]],
                           request=req["request_id"], delivered=ev["path"])
                log(f"Evidence received: {ev['path']}")
                fulfilled = True
    ctx.save_case("evidence requested")
    return fulfilled


def _approve(ctx, out, d, rec, causes, scn, log):
    case, led, clock = ctx.case, ctx.ledger, ctx.clock
    analyst = d.get("analyst", "analyst")
    actor = {"kind": "analyst", "name": analyst, "version": "-"}
    supported = D(case["figures"]["supported"]["value"])
    amount_cite = rec.get("amount_calc_id")
    approved = supported if rec["decision"] in ("partial_dispute", "full_dispute") else None
    edits = None
    if d["action"] == "edit":
        approved = q2(D(str(d["amount"])))
        if approved > supported or approved < 0:
            raise ValueError(f"approved amount {approved} must be between 0.00 and the supported {supported}")
        amount_cite = led.calc(7, steps.rules("analyst-edit"), "approved_amount", "analyst input",
                               {"analyst": analyst, "supported": rec["amount_calc_id"]}, approved, ctx.sc["invoice"]["currency"],
                               f"Analyst set the approved amount to {money(approved)}.", [rec["amount_calc_id"]])
        edits = {"approved_amount": {"from": str(supported), "to": str(approved)}}
    decided = {"decision": rec["decision"], "approved_amount": str(approved) if approved is not None else None, "edits": edits,
               "reason": d.get("reason", ""), "analyst": analyst, "decided_at": clock.iso()}
    case["analyst_decision"] = decided
    led.append(7, actor, "approval", f"Analyst approved {rec['decision'].replace('_', ' ')}" +
               (f" of {money(approved)}" if approved is not None else "") + f". {d.get('reason', '')}".rstrip(),
               [case["cite_id"]] + ([amount_cite] if amount_cite else []), decision=d["action"])
    log(f"Step 7: analyst {analyst} approved {rec['decision']}" + (f" {money(approved)}" if approved is not None else ""))
    subs = steps.build_submissions(ctx, causes, approved, analyst) if approved else []
    packet_rel = f"cases/{case['case_id']}/dispute-packet-{case['case_id']}.md"
    if not subs:
        case.update(status="resolved", sub_status="approved", resolution_date=clock.iso())
        out["packet"] = steps.render_packet(ctx, causes, decided, [])
        open(ctx.sys.root.write_path(packet_rel), "w").write(out["packet"])
        case["attachments"].append({"name": os.path.basename(packet_rel), "path": packet_rel, "cite_id": case["cite_id"]})
        ctx.save_case("approved for payment")
        out["status"] = "approved_for_payment"
        return
    for s in subs:
        sub = ctx.sys.portal.submit(s)
        out["submissions"].append(sub)
        led.append(7, actor, "record_ref", f"Claim {sub['claim_id']} ({sub['reason_code']}) submitted to the carrier dispute portal.",
                   [sub["cite_id"], s["amount_calc_id"]])
    case["carrier_claim_id"] = ", ".join(s["claim_id"] for s in out["submissions"])
    out["packet"] = steps.render_packet(ctx, causes, decided, out["submissions"])
    open(ctx.sys.root.write_path(packet_rel), "w").write(out["packet"])
    case["attachments"].append({"name": os.path.basename(packet_rel), "path": packet_rel, "cite_id": case["cite_id"]})
    case.update(status="inProgress", sub_status="submitted", status_change_reason="submitted to carrier")
    ctx.save_case("submitted to carrier")
    log(f"Dispute submitted: {', '.join(s['claim_id'] for s in out['submissions'])}")

    # Scripted carrier responses, per claim. A scripted credit_amount is the case total; it is
    # allocated to the claims in submission order.
    submitted_at = clock.now
    for ev in sorted([e for e in scn["events"] if e.get("on") == "portal_submit"], key=lambda e: duration(e["after"])):
        clock.advance_to(submitted_at + duration(ev["after"]))
        resp = ev["response"]
        credit_left = D(str(resp["credit_amount"])) if resp.get("credit_amount") else None
        for sub in out["submissions"]:
            r = {"claim_id": sub["claim_id"], "status": resp["status"], "outcome": resp.get("outcome"),
                 "credit_amount": None, "credit_from": None, "credit_through": None,
                 "message": f"Claim {sub['claim_id']} {resp['status']}", "responded_at": clock.iso(), "attachments": []}
            if credit_left is not None:
                credit = min(D(sub["disputed_amount"]), credit_left)
                credit_left -= credit
                r.update(credit_amount=str(credit), credit_from=sub["from_date"], credit_through=sub["through_date"])
            r = ctx.sys.portal.add_response(r)
            out["responses"].append(r)
            cites = [r["cite_id"]]
            text = f"Carrier response on {sub['claim_id']}: {resp['status']}"
            if r["credit_amount"]:
                c = led.calc(7, steps.rules("portal-response"), f"credit_{sub['claim_id']}", "carrier response credit_amount",
                             {"response": r["cite_id"]}, D(r["credit_amount"]), ctx.sc["invoice"]["currency"],
                             f"Carrier credits {money(r['credit_amount'])} on {sub['claim_id']}.", [r["cite_id"]])
                cites.append(c)
                text += f", {resp.get('outcome')}, credit {money(r['credit_amount'])}"
            led.append(7, steps.rules("portal-response"), "record_ref", text + ".", cites)
            log(text)
        if resp["status"] == "resolved":
            case.update(status="resolved", sub_status="carrier_responded", resolution_date=clock.iso(),
                        status_change_reason=f"carrier resolved: {resp.get('outcome')}")
        else:
            case.update(sub_status="carrier_responded", status_change_reason=f"carrier {resp['status']}")
        ctx.save_case(case["status_change_reason"])
    out["status"] = "resolved" if case["status"] == "resolved" else "submitted"


def _finish(out, ctx):
    out["case"] = ctx.case
    out["ledger"] = ctx.ledger.read() if ctx.ledger else []
    out["sim_end"] = ctx.clock.iso()
    if ctx.case:
        out["case_history"] = ctx.sys.cases.history(ctx.case["case_id"])
    return out

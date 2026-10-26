"""The seven steps of the invoice dispute flow (flow-diagram.html).

Steps 1, 2, 3 and 5 are `rules`: deterministic code that writes every amount as a ledger calc.
Steps 4 and 6 are the `agent` steps: they classify causes and pick a decision, and their
statements may only quote amounts from calcs they cite (the ledger enforces this). The agent's
reasoning is a fixed policy here so every scenario is reproducible to the cent; a model can later
write the same statements through the same ledger checks. Step 7 (the analyst gate) is in
runner.py because it waits for a person.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from .ledger import Ledger
from .store import D, SUBMIT_OK, money, q2

ENGINE = "0.1.0"
INVOICE_REQUIRED = ["invoice_id", "issue_date", "due_date", "seller_party", "buyer_party", "account_id",
                    "agreement_ref", "period_start", "period_end", "currency", "line_count", "subtotal",
                    "tax_total", "total"]
LINE_REQUIRED = ["line_no", "destination", "service", "period_start", "period_end", "messages", "segments",
                 "rate", "amount"]
AGREEMENT_REQUIRED = ["agreement_id", "document_number", "agreement_type", "status", "version", "period_start",
                      "buyer_party", "seller_party", "currency", "billing_cycle", "billing_timezone",
                      "billing_unit", "billing_basis", "rounding", "rate_schedule_ref",
                      "rate_notice_days_increase", "payment_terms_days", "dispute_window_days",
                      "pay_undisputed_required", "dispute_evidence_required", "clauses"]
# Rejects that were never billable by the carrier, so they can never explain a volume gap.
NON_BILLABLE_REJECTS = {"DUPLICATE", "SUBMIT_FAILED"}


def rules(name):
    return {"kind": "rules", "name": name, "version": ENGINE}


def agent(name):
    return {"kind": "agent", "name": name, "version": ENGINE}


def days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def pct(part, whole):
    return q2(D(part) / D(whole) * 100) if D(whole) else D("0.00")


class CaseContext:
    """Everything one case run shares: adapters, ledger, sim clock, thresholds and the records
    attached at step 1."""

    def __init__(self, systems, clock, thresholds):
        self.sys, self.clock, self.thresholds = systems, clock, thresholds
        self.ledger = None
        self.case = None
        self.detail_findings = {}

    def cite(self, *ids):
        return [i for i in ids if i]

    def save_case(self, text):
        self.sys.cases.save(self.case, self.clock.iso(), text)

    def set_status(self, status, sub_status, reason):
        self.case.update(status=status, sub_status=sub_status, status_change_reason=reason)
        self.save_case(reason)


# ====================================================================== step 1: open case (rules)
def screen_invoice(ctx, invoice_id):
    """Loads the five sources for an invoice and decides whether a case opens. Returns the
    screening figures; when a case opens they are written to its ledger as calcs."""
    s = ctx.sys
    inv = s.invoices.get_invoice(invoice_id)
    agr = s.contracts.find_by_ref(inv["agreement_ref"])
    carrier = inv["seller_party"]["party_id"]
    run = s.rating.get_run(carrier, inv["period_start"], inv["period_end"])
    lines = s.rating.lines(run["run_id"]) if run else []
    invoiced = D(inv["total"])
    expected = sum((D(l["amount"]) for l in lines), D("0.00"))
    variance = invoiced - expected
    var_pct = pct(abs(variance), invoiced)
    threshold = D(str(ctx.thresholds["open_case_variance_pct_of_invoice"]))
    return {"invoice": inv, "agreement": agr, "carrier_id": carrier, "run": run, "rated_lines": lines,
            "invoiced": invoiced, "expected": expected, "variance": variance, "variance_pct": var_pct,
            "variance_pct_of_expected": pct(abs(variance), expected), "threshold": threshold,
            "opens": var_pct > threshold}


def open_case(ctx, sc):
    s, inv, agr, run = ctx.sys, sc["invoice"], sc["agreement"], sc["run"]
    case_id = s.cases.next_id()
    ctx.ledger = led = Ledger(s, case_id, ctx.clock)
    ctx.sc = sc
    issue = date.fromisoformat(inv["issue_date"])
    act = rules("step1-open-case")
    led.append(1, act, "record_ref", f"Invoice {inv['invoice_id']} received from {inv['seller_party']['name']} "
               f"for {inv['period_start']} to {inv['period_end']}.", [inv["cite_id"]])
    c_inv = led.calc(1, act, "invoiced", "invoice.total", {"invoice": inv["cite_id"]}, sc["invoiced"], inv["currency"],
                     f"Invoiced total {money(sc['invoiced'])}.", [inv["cite_id"]])
    c_exp = led.calc(1, act, "expected", f"sum(amount) over rating run {run['run_id']} ({len(sc['rated_lines'])} lines)",
                     {"rating_run": run["cite_id"]}, sc["expected"], inv["currency"],
                     f"Expected total from our rating run {money(sc['expected'])}.", [run["cite_id"]])
    c_var = led.calc(1, act, "variance", "invoiced - expected", {"invoiced": c_inv, "expected": c_exp},
                     sc["variance"], inv["currency"], f"Variance {money(sc['variance'])}.", [c_inv, c_exp])
    c_pct = led.calc(1, act, "variance_pct_of_invoice", "|variance| / invoiced x 100", {"variance": c_var, "invoiced": c_inv},
                     sc["variance_pct"], "%", f"Variance is {sc['variance_pct']}% of the invoiced total.", [c_var, c_inv])
    c_pcte = led.calc(1, act, "variance_pct_of_expected", "|variance| / expected x 100", {"variance": c_var, "expected": c_exp},
                      sc["variance_pct_of_expected"], "%", f"Variance is {sc['variance_pct_of_expected']}% of expected.",
                      [c_var, c_exp])
    led.append(1, act, "check", f"Variance {sc['variance_pct']}% of invoiced is above the "
               f"{sc['threshold']}% case threshold, so a case opens.", [c_pct], result="pass")
    deadline = issue + timedelta(days=agr["dispute_window_days"])
    severity = "high" if sc["variance_pct"] >= 5 else "medium"
    ctx.case = {
        "case_id": case_id, "correlation_id": f"{sc['carrier_id']}:{inv['invoice_id']}", "carrier_claim_id": None,
        "ticket_type": "invoice_discrepancy", "severity": severity, "status": "acknowledged", "sub_status": "opened",
        "status_change_reason": "variance above case threshold", "creation_date": ctx.clock.iso(),
        "dispute_deadline": deadline.isoformat(), "payment_due": inv["due_date"],
        "target_resolution_date": deadline.isoformat(), "resolution_date": None,
        "thresholds": dict(ctx.thresholds),
        "related_objects": [{"role": "invoice", "cite_id": inv["cite_id"]},
                            {"role": "agreement", "cite_id": agr["cite_id"]},
                            {"role": "rating_run", "cite_id": run["cite_id"]}]
                           + [{"role": "deck", "cite_id": f"sim://rates/deck_history/{d}"} for d in run["decks_used"]],
        "related_parties": [{"role": "carrier", "party_id": sc["carrier_id"], "name": inv["seller_party"]["name"]},
                            {"role": "owner", "party_id": inv["buyer_party"]["party_id"], "name": inv["buyer_party"]["name"]}],
        "figures": {"invoiced": {"value": str(sc["invoiced"]), "calc_id": c_inv},
                    "expected": {"value": str(sc["expected"]), "calc_id": c_exp},
                    "variance": {"value": str(sc["variance"]), "calc_id": c_var},
                    "variance_pct_of_invoice": {"value": str(sc["variance_pct"]), "calc_id": c_pct}},
        "recommendation": None, "analyst_decision": None, "evidence_requests": [], "notes": [], "attachments": [],
    }
    ctx.calc_ids = {"invoiced": c_inv, "expected": c_exp, "variance": c_var, "variance_pct": c_pct, "variance_pct_of_expected": c_pcte}
    led.append(1, act, "record_ref", f"Case {case_id} opened, severity {severity}; dispute deadline "
               f"{deadline} (issue date plus {agr['dispute_window_days']} days, clause 8.1).",
               [inv["cite_id"], s.contracts.clause_cite(agr["agreement_id"], "8.1")])
    ctx.save_case("case opened")
    return ctx.case


# ====================================================================== step 2: validate (rules)
def validate(ctx):
    """Runs every check in simulated-systems.md section 5. Returns {blocking, checks, requests}."""
    s, sc, led = ctx.sys, ctx.sc, ctx.ledger
    inv, agr, run = s.invoices.get_invoice(sc["invoice"]["invoice_id"]), sc["agreement"], sc["run"]
    sc["invoice"] = inv
    act = rules("step2-validate")
    ctx.set_status("inProgress", "validating", "validating attached records")
    checks, requests = [], []
    p_start, p_end = date.fromisoformat(inv["period_start"]), date.fromisoformat(inv["period_end"])

    def check(name, status, text, cites, request=None):
        led.append(2, act, "check", f"{name}: {text}", cites or [inv["cite_id"]], result=status, check=name)
        checks.append({"check": name, "status": status, "text": text})
        if request and status == "block":
            requests.append(request)

    # Required fields
    missing = [f"invoice.{f}" for f in INVOICE_REQUIRED if inv.get(f) in (None, "")]
    missing += [f"invoice line {l['line_no']}.{f}" for l in inv["lines"] for f in LINE_REQUIRED if l.get(f) in (None, "")]
    missing_agr = [f"agreement.{f}" for f in AGREEMENT_REQUIRED if agr.get(f) in (None, "")]
    check("Required fields", "block" if missing or missing_agr else "pass",
          "missing " + ", ".join(missing + missing_agr) if missing or missing_agr else "all required invoice and agreement fields present",
          [inv["cite_id"], agr["cite_id"]],
          {"to": "carrier", "item": "corrected invoice", "reason": "missing fields"} if missing else
          {"to": "internal_team", "item": "complete agreement record", "reason": "missing fields"})

    # Period alignment
    run_ok = run and run["status"] == "complete" and run["period_start"] <= inv["period_start"] and run["period_end"] >= inv["period_end"]
    check("Period alignment", "pass" if run_ok else "block",
          f"rating run {run['run_id']} is {run['status']} for {run['period_start']} to {run['period_end']}" if run else "no rating run covers the invoice period",
          [inv["cite_id"]] + ([run["cite_id"]] if run else []),
          {"to": "internal_team", "item": f"complete rating run for {inv['period_start']} to {inv['period_end']}"})

    # Currency
    cur = agr["currency"]
    bad_cur = [x for x in [inv["currency"]] if x != cur]
    decks = {h["deck_id"]: h for h in s.rates.list_decks(agr["rate_schedule_ref"])}
    deck_rows = {d: s.rates.get_deck_rows(d) for d in decks}
    bad_cur += [r["cite_id"] for rows in deck_rows.values() for r in rows or [] if r["currency"] != cur]
    bad_cur += [l["cite_id"] for l in sc["rated_lines"] if l["currency"] != cur]
    check("Currency", "block" if bad_cur else "pass",
          f"mismatches with agreement currency {cur}: {len(bad_cur)}" if bad_cur else f"invoice, deck rows and rated lines all in {cur}",
          [inv["cite_id"], agr["cite_id"]] + bad_cur[:20], {"to": "carrier", "item": "corrected invoice", "reason": "currency"})

    # Units
    bad_units = [l["cite_id"] for l in inv["lines"] if not isinstance(l["segments"], int) or l["segments"] < l["messages"]]
    check("Units", "block" if bad_units else "pass",
          f"{len(bad_units)} invoice lines with non-integer segments or fewer segments than messages" if bad_units
          else f"every line bills whole {agr['billing_unit']}s, at least one per message",
          bad_units or [inv["cite_id"], s.contracts.clause_cite(agr["agreement_id"], "4.2")],
          {"to": "carrier", "item": "corrected invoice", "reason": "units"})

    # Totals: invoice line arithmetic, lines -> subtotal -> total
    bad_lines = []
    for l in inv["lines"]:
        recomputed = q2(D(l["rate"]) * l["segments"])
        if recomputed != D(l["amount"]):
            c = led.calc(2, act, f"line_{l['line_no']}_recomputed", f"{l['segments']} x {l['rate']}",
                         {"segments": l["segments"], "rate": l["rate"], "line": l["cite_id"]}, recomputed, cur,
                         f"Invoice line {l['line_no']} recomputes to {money(recomputed)}.", [l["cite_id"]])
            c_st = led.calc(2, act, f"line_{l['line_no']}_stated", "invoice line amount", {"line": l["cite_id"]},
                            D(l["amount"]), cur, f"Invoice line {l['line_no']} states {money(l['amount'])}.", [l["cite_id"]])
            bad_lines.append((l, c, c_st))
    lines_sum = sum((D(l["amount"]) for l in inv["lines"]), D("0.00"))
    c_sum = led.calc(2, act, "invoice_lines_sum", f"sum of {len(inv['lines'])} line amounts",
                     {l["cite_id"]: l["amount"] for l in inv["lines"]}, lines_sum, cur,
                     f"Invoice lines sum to {money(lines_sum)}.", [l["cite_id"] for l in inv["lines"]])
    c_sub = led.calc(2, act, "invoice_subtotal_stated", "invoice.subtotal", {"invoice": inv["cite_id"]}, D(inv["subtotal"]),
                     cur, f"Invoice states subtotal {money(inv['subtotal'])}.", [inv["cite_id"]])
    c_tot = led.calc(2, act, "invoice_subtotal_plus_tax", "invoice.subtotal + invoice.tax_total", {"invoice": inv["cite_id"]},
                     D(inv["subtotal"]) + D(inv["tax_total"]), cur,
                     f"Subtotal plus tax is {money(D(inv['subtotal']) + D(inv['tax_total']))}.", [inv["cite_id"]])
    sums_ok = abs(lines_sum - D(inv["subtotal"])) <= D("0.01") and abs(D(inv["subtotal"]) + D(inv["tax_total"]) - D(inv["total"])) <= D("0.01")
    if bad_lines or not sums_ok:
        detail = f"lines sum to {money(lines_sum)} but subtotal says {money(inv['subtotal'])}" if not sums_ok else "line amounts do not recompute"
        for l, c, _st in bad_lines:
            detail += f"; line {l['line_no']} is {l['segments']} segments x {l['rate']} = {money(led.result(c))}, not {money(l['amount'])}"
        check("Totals", "block", detail, [c_sum, c_sub, c_tot, inv["cite_id"]] + [x for b in bad_lines for x in b[1:]],
              {"to": "carrier", "item": "corrected invoice", "reason": detail})
    else:
        check("Totals", "pass", f"invoice lines sum to subtotal {money(lines_sum)} and total {money(inv['total'])}",
              [c_sum, c_sub, c_tot])
    bad_rated = [l["cite_id"] for l in sc["rated_lines"] if q2(D(l["rate"]) * int(l["billable_segments"])) != D(l["amount"])]
    check("Totals (rating run)", "block" if bad_rated else "pass",
          f"{len(bad_rated)} rated lines do not equal segments x rate" if bad_rated else f"all {len(sc['rated_lines'])} rated lines equal segments x rate",
          bad_rated[:20] or [run["cite_id"]], {"to": "internal_team", "item": f"corrected rating run {run['run_id']}"})

    # Totals: message records -> daily summary -> rating volumes
    gmap = s.rates.group_map(agr["rate_schedule_ref"])
    roll = s.usage.rollup(sc["carrier_id"], p_start, p_end, gmap)
    daily = s.usage.daily_summary(sc["carrier_id"], p_start, p_end)
    ctx.daily = {(r["date_utc"], r["network_group"]): r for r in daily}
    keys = ("messages", "billable_segments", "delivered", "undelivered", "expired", "no_receipt")
    ds = {k: [int(r[f]) for f in keys] for k, r in ctx.daily.items()}
    off = [ctx.daily[k]["cite_id"] for k in ds if ds[k] != roll["by_day_group"].get(k)]
    off += [f"sim://usage/daily/{k[0]}/{k[1]}" for k in roll["by_day_group"] if k not in ds]
    check("Totals (usage)", "block" if off else "pass",
          f"{len(off)} daily summary rows differ from the message records" if off else
          f"daily summary equals the roll-up of {roll['messages']} message records exactly ({len(ds)} day-network rows)",
          off[:20] or [r["cite_id"] for r in daily[:1]], {"to": "internal_team", "item": "mediation re-run of the daily summary"})
    rated_dg = defaultdict(int)
    for l in sc["rated_lines"]:
        rated_dg[(l["date_local"], l["network_group"])] += int(l["billable_segments"])
    off_r = [ctx.daily[k]["cite_id"] for k in ds if rated_dg.get(k, 0) != ds[k][1]]
    check("Totals (rating vs usage)", "block" if off_r else "pass",
          f"{len(off_r)} day-network rows where rated segments differ from usage" if off_r else
          "rated segments equal usage segments for every day and network",
          off_r[:20] or [run["cite_id"]], {"to": "internal_team", "item": f"re-rate {run['run_id']} from current usage"})

    # Duplicates
    line_nos = [l["line_no"] for l in inv["lines"]]
    dup_lines = len(line_nos) != len(set(line_nos))
    check("Duplicates (invoice)", "block" if dup_lines else "pass",
          "repeated invoice line numbers" if dup_lines else "invoice line numbers are unique", [inv["cite_id"]],
          {"to": "carrier", "item": "corrected invoice", "reason": "duplicate lines"})
    check("Duplicates (usage)", "flag" if roll["duplicates"] else "pass",
          f"{roll['duplicates']} repeated supplier_msg_id in our message records" if roll["duplicates"]
          else f"no repeated supplier_msg_id in {roll['messages']} message records",
          [r["cite_id"] for r in daily[:1]])

    # Required records and effective dates
    missing_decks = [d for d in sorted(set(run["decks_used"]) | set(decks)) if deck_rows.get(d) is None]
    if missing_decks:
        notices = [decks[d]["notice_id"] for d in missing_decks if d in decks and decks[d]["notice_id"]]
        item = "rate deck " + " and ".join(missing_decks) + ("".join(f" and notice {n}" for n in notices))
        check("Required records", "block", f"deck history and rating run reference {', '.join(missing_decks)} "
              "but the deck file is not in the rate deck store",
              [run["cite_id"]] + [decks[d]["cite_id"] for d in missing_decks if d in decks],
              {"to": "internal_team", "item": item, "reason": "deck file missing"})
    else:
        check("Required records", "pass", f"all {len(decks)} decks in the load history are on file",
              [h["cite_id"] for h in decks.values()])
    bad_eff = []
    for l in sc["rated_lines"]:
        cr = s.rates.contracted_rate(agr, l["mcc_mnc"], date.fromisoformat(l["date_local"]))
        if not cr or cr["row"]["deck_id"] != l["deck_id"] or D(cr["row"]["rate"]) != D(l["rate"]):
            bad_eff.append(l["cite_id"])
    check("Effective dates", "block" if bad_eff else "pass",
          f"{len(bad_eff)} rated lines use a deck row that is not the contracted rate on their date"
          + (" (the deck they name is missing)" if missing_decks else "") if bad_eff
          else "every rated line uses the deck row the contracted-rate rule selects (clause 6.3)",
          bad_eff[:20] or [run["cite_id"], s.contracts.clause_cite(agr["agreement_id"], "6.3")],
          None if missing_decks else {"to": "internal_team", "item": f"re-rate {run['run_id']} with the contracted rates"})

    # Rejects (never blocking)
    cmap = s.usage.connection_map()
    rej = [r for r in s.usage.rejects(sc["carrier_id"], p_start, p_end, cmap) if r["reprocessed"] != "true"]
    ctx.rejects, ctx.cmap = rej, cmap
    by_reason = defaultdict(list)
    for r in rej:
        by_reason[r["reject_reason"]].append(r)
    if rej:
        summary = ", ".join(f"{k} {len(v)}" for k, v in sorted(by_reason.items()))
        check("Rejects", "flag", f"{len(rej)} unreprocessed rejects on this carrier's binds ({summary}); passed to step 4",
              [r["cite_id"] for r in rej])
    else:
        check("Rejects", "pass", "no unreprocessed rejects on this carrier's binds", [inv["cite_id"]])

    # Delivery receipts (never blocking)
    nd = sum(v[3] + v[4] + v[5] for v in ds.values())
    check("Delivery receipts", "flag" if nd else "pass",
          f"{nd} messages without a DELIVRD receipt; billing basis is {agr['billing_basis']} (clause 4.2), "
          "so they are billable and must not be disputed",
          [s.contracts.clause_cite(agr["agreement_id"], "4.2")] + [r["cite_id"] for r in daily[:1]])

    blocking = any(c["status"] == "block" for c in checks)
    # Same item requested by several checks is one request
    uniq = []
    for r in requests:
        if all((r["to"], r["item"]) != (u["to"], u["item"]) for u in uniq):
            uniq.append(r)
    return {"blocking": blocking, "checks": checks, "requests": uniq,
            "rejects_by_reason": {k: {"count": len(v), "segments": sum(int(r["segments"]) for r in v)} for k, v in by_reason.items()},
            "delivery_receipts": {"delivered": sum(v[2] for v in ds.values()), "undelivered": sum(v[3] for v in ds.values()),
                                  "expired": sum(v[4] for v in ds.values()), "no_receipt": sum(v[5] for v in ds.values())},
            "duplicate_supplier_msg_ids": roll["duplicates"]}


# ====================================================================== step 3: reconcile (rules)
def _line_mccs(ctx, line, deck_by_mcc):
    if line.get("mcc_mnc"):
        return line["mcc_mnc"].split("|")
    return sorted(m for m, r in deck_by_mcc.items() if r["destination"] == line["destination"])


def reconcile(ctx):
    s, sc, led = ctx.sys, ctx.sc, ctx.ledger
    inv, agr = sc["invoice"], sc["agreement"]
    act = rules("step3-reconcile")
    ctx.set_status("inProgress", "reconciling", "reconciling invoice lines against usage and contracted rates")
    deck_by_mcc = {}
    for h in s.rates.list_decks(agr["rate_schedule_ref"]):
        for r in s.rates.get_deck_rows(h["deck_id"]) or []:
            deck_by_mcc.setdefault(r["mcc_mnc"], r)
    rated_by_group = defaultdict(list)
    for l in sc["rated_lines"]:
        rated_by_group[l["network_group"]].append(l)
    cur = inv["currency"]
    rows, billed_groups = [], set()
    for line in inv["lines"]:
        mccs = _line_mccs(ctx, line, deck_by_mcc)
        groups = {deck_by_mcc[m]["network_group"] for m in mccs if m in deck_by_mcc}
        ls, le = date.fromisoformat(line["period_start"]), date.fromisoformat(line["period_end"])
        row = {"line_no": line["line_no"], "destination": line["destination"], "mcc_mnc": mccs, "service": line["service"],
               "period": [line["period_start"], line["period_end"]], "billed_rate": line["rate"],
               "billed_segments": line["segments"], "invoiced": line["amount"], "exceptions": [], "line_cite": line["cite_id"]}
        if len(groups) != 1:
            c = led.calc(3, act, f"line_{line['line_no']}_unmapped", "invoice line amount", {"line": line["cite_id"]},
                         D(line["amount"]), cur, f"Line {line['line_no']} ({line['destination']}) maps to no single network; "
                         f"{money(line['amount'])} unmatched.", [line["cite_id"]])
            row.update(network_group=None, expected="0.00", variance=line["amount"], status="exception")
            row["exceptions"].append({"type": "UNMAPPED_LINE", "amount": c, "line_no": line["line_no"]})
            rows.append(row)
            continue
        group = groups.pop()
        billed_groups.add(group)
        rated = [l for l in rated_by_group[group] if ls.isoformat() <= l["date_local"] <= le.isoformat()]
        our_seg = sum(int(l["billable_segments"]) for l in rated)
        expected = sum((D(l["amount"]) for l in rated), D("0.00"))
        rated_cites = [l["cite_id"] for l in rated]
        c_exp = led.calc(3, act, f"line_{line['line_no']}_expected", f"sum(amount) of {len(rated)} rated lines for {group}",
                         {"rated_lines": rated_cites}, expected, cur,
                         f"Expected for line {line['line_no']} ({group}, {line['period_start']} to {line['period_end']}): "
                         f"{money(expected)} on {our_seg} segments.", [line["cite_id"]] + rated_cites)
        variance = D(line["amount"]) - expected
        c_var = led.calc(3, act, f"line_{line['line_no']}_variance", "invoiced - expected",
                         {"invoiced": line["cite_id"], "expected": c_exp}, variance, cur,
                         f"Line {line['line_no']} variance {money(variance)}.", [line["cite_id"], c_exp])
        R = D(line["rate"])
        off_rate = [l for l in rated if D(l["rate"]) != R]
        rate_amt = q2(sum((int(l["billable_segments"]) * (R - D(l["rate"])) for l in off_rate), D("0")))
        vol_seg = line["segments"] - our_seg
        vol_amt = q2(vol_seg * R)
        row.update(network_group=group, our_segments=our_seg, expected=str(expected), variance=str(variance),
                   expected_calc=c_exp, variance_calc=c_var, status="match" if variance == 0 else "exception")
        if off_rate:
            row["exceptions"].append(_rate_exception(ctx, act, line, group, mccs, off_rate, rate_amt))
        if vol_seg:
            day_cites = [ctx.daily[k]["cite_id"] for k in sorted(ctx.daily) if k[1] == group and ls.isoformat() <= k[0] <= le.isoformat()]
            c = led.calc(3, act, f"line_{line['line_no']}_volume", f"({line['segments']} - {our_seg}) x {line['rate']}",
                         {"billed_segments": line["cite_id"], "our_segments": rated_cites, "rate": line["cite_id"]}, vol_amt, cur,
                         f"Line {line['line_no']} bills {vol_seg} segments more than our usage: {money(vol_amt)} at the billed rate.",
                         [line["cite_id"]] + day_cites)
            row["exceptions"].append({"type": "VOLUME", "amount": c, "line_no": line["line_no"], "group": group,
                                      "segments": vol_seg, "messages": line["messages"] - sum(int(l["messages"]) for l in rated),
                                      "mcc_mnc": mccs, "period": row["period"], "rate": line["rate"]})
        residual = variance - rate_amt - vol_amt
        if residual:
            c = led.calc(3, act, f"line_{line['line_no']}_residual", "variance - rate part - volume part",
                         {"variance": c_var}, residual, cur, f"Line {line['line_no']} has a rounding residual of {money(residual)}.", [c_var])
            row["exceptions"].append({"type": "ROUNDING", "amount": c, "line_no": line["line_no"], "group": group})
        led.append(3, act, "check", f"Line {line['line_no']} {group}: " +
                   ("matches our usage and contracted rates." if row["status"] == "match" else
                    "exceptions " + ", ".join(e["type"] for e in row["exceptions"]) + "."),
                   [line["cite_id"], c_var], result=row["status"])
        rows.append(row)
    for group, rated in sorted(rated_by_group.items()):
        if group not in billed_groups:
            amt = sum((D(l["amount"]) for l in rated), D("0.00"))
            c = led.calc(3, act, f"unbilled_{group}", f"sum(amount) of {len(rated)} rated lines", {"rated_lines": [l["cite_id"] for l in rated]},
                         -amt, cur, f"{group} has rated usage of {money(amt)} that no invoice line bills.", [l["cite_id"] for l in rated])
            rows.append({"line_no": None, "network_group": group, "status": "exception", "invoiced": "0.00", "expected": str(amt),
                         "variance": str(-amt), "exceptions": [{"type": "UNBILLED", "amount": c, "group": group}]})
    total_line_var = sum((D(r["variance"]) for r in rows), D("0.00"))
    led.append(3, act, "check", "Line variances add up to the case variance." if total_line_var == ctx.sc["variance"]
               else "Line variances do not add up to the case variance.", [ctx.calc_ids["variance"]],
               result="pass" if total_line_var == ctx.sc["variance"] else "flag")
    return rows


def _rate_exception(ctx, act, line, group, mccs, off_rate, rate_amt):
    s, led, agr = ctx.sys, ctx.ledger, ctx.sc["agreement"]
    R = D(line["rate"])
    dates = sorted({l["date_local"] for l in off_rate})
    seg = sum(int(l["billable_segments"]) for l in off_rate)
    correct = sorted({l["rate"] for l in off_rate})
    used_rows = sorted({f"sim://rates/deck/{l['deck_id']}/row/{l['mcc_mnc']}" for l in off_rate})
    day_cites = [ctx.daily[(d, group)]["cite_id"] for d in dates if (d, group) in ctx.daily]
    # A deck row at the billed rate that the contract only allows later in the period?
    etype, cites = "RATE_MISMATCH", [line["cite_id"]] + used_rows
    clause = "6.1"
    for h in s.rates.list_decks(agr["rate_schedule_ref"]):
        for r in s.rates.get_deck_rows(h["deck_id"]) or []:
            if r["mcc_mnc"] in mccs and D(r["rate"]) == R and r["change_flag"] in ("INCREASE", "NEW"):
                cr = s.rates.contracted_rate(agr, r["mcc_mnc"], date.fromisoformat(line["period_end"]))
                if cr and cr["row"]["cite_id"] == r["cite_id"] and dates[-1] < cr["valid_from"].isoformat():
                    etype, clause = "RATE_BEFORE_EFFECTIVE", "6.3"
                    cites = [line["cite_id"], r["cite_id"]] + used_rows + ([cr["notice"]["cite_id"]] if cr["notice"] else [])
                    valid_from = cr["valid_from"].isoformat()
    cites.append(s.contracts.clause_cite(agr["agreement_id"], clause))
    c = led.calc(3, act, f"line_{line['line_no']}_rate", f"{seg} segments x ({line['rate']} - {'/'.join(correct)})",
                 {"segments": [l["cite_id"] for l in off_rate], "billed_rate": line["cite_id"], "contracted_rate": used_rows},
                 rate_amt, line.get("currency", ctx.sc["invoice"]["currency"]),
                 f"Line {line['line_no']} bills {seg} segments from {dates[0]} to {dates[-1]} at {line['rate']} where the "
                 f"contracted rate is {'/'.join(correct)}: {money(rate_amt)}.", cites + day_cites + [l["cite_id"] for l in off_rate])
    exc = {"type": etype, "amount": c, "line_no": line["line_no"], "group": group, "segments": seg,
           "from_date": dates[0], "through_date": dates[-1], "billed_rate": line["rate"],
           "correct_rate": correct[0] if len(correct) == 1 else None, "cites": cites + day_cites,
           "destination": line["destination"], "mcc_mnc": mccs}
    if etype == "RATE_BEFORE_EFFECTIVE":
        exc["valid_from"] = valid_from
    return exc


# ====================================================================== step 4: investigate causes (agent)
def investigate(ctx, recon):
    """Classifies each exception into causes; every statement is classed and cited. Amounts come
    from step 3 calcs or from rules calcs the agent asks for (rejects and carrier detail roll-ups)."""
    s, sc, led = ctx.sys, ctx.sc, ctx.ledger
    agr, inv = sc["agreement"], sc["invoice"]
    me = agent("cause-analyst")
    ctx.set_status("inProgress", "investigating", "classifying causes of each exception")
    clause = lambda c: s.contracts.clause_cite(agr["agreement_id"], c)
    causes = []
    for row in recon:
        for exc in row["exceptions"]:
            t = exc["type"]
            if t in ("RATE_BEFORE_EFFECTIVE", "RATE_MISMATCH"):
                if t == "RATE_BEFORE_EFFECTIVE":
                    notice = next((c for c in exc["cites"] if c.startswith("sim://rates/notice/")), None)
                    n = s.resolve(notice) if notice else None
                    fact = (f"The {exc['billed_rate']} rate for {exc['destination']} was noticed on {n['sent_at']} and under "
                            f"clause 6.3 applies from {exc['valid_from']}; invoice line {exc['line_no']} applies it from "
                            f"{row['period'][0]}.")
                else:
                    fact = (f"Invoice line {exc['line_no']} bills {exc['destination']} at {exc['billed_rate']}; the contracted "
                            f"rate on {exc['from_date']} to {exc['through_date']} is {exc['correct_rate']}.")
                e1 = led.append(4, me, "statement", fact, exc["cites"], claim_class="fact")
                e2 = led.append(4, me, "statement", f"Re-rating those {exc['segments']} segments at the contracted rate gives "
                                f"a supported dispute of {money(led.result(exc['amount']))}.", [exc["amount"]], claim_class="calculation")
                causes.append({"group": exc["group"], "line_no": exc["line_no"], "exception": t, "classification": "supported",
                               "claim_class": ["fact", "calculation"], "amount": exc["amount"], "reason_code": t,
                               "statements": [e1["entry_id"], e2["entry_id"]], "statement": fact,
                               "dispute": {"destination": exc["destination"], "mcc_mnc": exc["mcc_mnc"],
                                           "billed_rate": exc["billed_rate"], "correct_rate": exc["correct_rate"],
                                           "from_date": exc["from_date"], "through_date": exc["through_date"]}})
            elif t == "VOLUME" and exc["segments"] > 0:
                detail = s.invoices.get_carrier_detail(inv["invoice_id"])
                if detail is not None:
                    causes += _volume_from_detail(ctx, me, row, exc, detail)
                else:
                    causes += _volume_from_rejects(ctx, me, row, exc)
            else:
                e = led.append(4, me, "statement", f"Line {exc.get('line_no')} {t.lower().replace('_', ' ')} of "
                               f"{money(led.result(exc['amount']))} has no record that explains it.", [exc["amount"]],
                               claim_class="missing_evidence")
                causes.append({"group": exc.get("group"), "line_no": exc.get("line_no"), "exception": t,
                               "classification": "unresolved", "claim_class": ["missing_evidence"], "amount": exc["amount"],
                               "statements": [e["entry_id"]], "statement": e["text"],
                               "evidence_needed": {"to": "carrier", "item": "line explanation", "line_no": exc.get("line_no")}})
    # Distractors the agent must not turn into disputes
    v = ctx.validation
    nd = sum(v["delivery_receipts"][k] for k in ("undelivered", "expired", "no_receipt"))
    if nd:
        led.append(4, me, "statement", f"{nd} messages without a DELIVRD receipt are billable on both sides because billing "
                   "is on submit; they are not a cause and are not disputed.", [clause("4.2"), clause("7.1")], claim_class="fact")
    dropped = {k: x for k, x in v["rejects_by_reason"].items() if k in NON_BILLABLE_REJECTS}
    if dropped:
        led.append(4, me, "statement", "Rejects " + ", ".join(f"{k} ({x['count']})" for k, x in sorted(dropped.items())) +
                   " were never billable by the carrier, so mediation dropped them correctly; they explain no gap.",
                   [clause("4.4")] + [r["cite_id"] for r in ctx.rejects if r["reject_reason"] in NON_BILLABLE_REJECTS],
                   claim_class="fact")
    return causes


def _volume_from_rejects(ctx, me, row, exc):
    s, led, agr = ctx.sys, ctx.ledger, ctx.sc["agreement"]
    act = rules("step4-rejects-rollup")
    ps, pe = exc["period"]
    billable = [r for r in ctx.rejects if r["reject_reason"] not in NON_BILLABLE_REJECTS and r["submit_status"] == SUBMIT_OK
                and r["supplier_msg_id"] and r["mcc_mnc"] in exc["mcc_mnc"] and ps <= r["submitted_at_utc"][:10] <= pe]
    seg = sum(int(r["segments"]) for r in billable)
    out = []
    explained_seg = min(seg, exc["segments"])
    if explained_seg:
        binds = sorted({r["smpp_connection"] for r in billable})
        cmap = {c["connection_id"]: c for c in ctx.cmap}
        first, last = min(r["submitted_at_utc"][:10] for r in billable), max(r["submitted_at_utc"][:10] for r in billable)
        reasons = sorted({r["reject_reason"] for r in billable})
        c = led.calc(4, act, f"line_{exc['line_no']}_explained_by_rejects", f"{explained_seg} segments x {exc['rate']}",
                     {"rejects": [r["cite_id"] for r in billable], "rate": row["line_cite"]}, q2(explained_seg * D(exc["rate"])),
                     ctx.sc["invoice"]["currency"],
                     f"{len(billable)} unreprocessed {'/'.join(reasons)} rejects to {exc['group']} carry {seg} segments, "
                     f"worth {money(q2(explained_seg * D(exc['rate'])))} at the billed rate.",
                     [row["line_cite"]] + [r["cite_id"] for r in billable])
        late = [b for b in binds if b in cmap and cmap[b]["valid_from"] > first]
        why = (f" Bind {', '.join(late)} only enters our connection map on {cmap[late[0]]['valid_from']}, so mediation "
               f"could not attribute that traffic." if late else "")
        text = (f"Mediation rejected {len(billable)} {exc['group']} messages / {seg} billable segments "
                f"({'/'.join(reasons)}, {first} to {last}, not reprocessed) that the carrier accepted and billed: "
                f"{money(led.result(c))} is legitimate and our expected total is understated.{why}")
        cites = [row["line_cite"], c] + [cmap[b]["cite_id"] for b in late] + \
                [s.contracts.clause_cite(agr["agreement_id"], "2.1"), s.contracts.clause_cite(agr["agreement_id"], "4.2")]
        e = led.append(4, me, "statement", text, cites, claim_class="fact")
        actions = [f"add {b} to connection_map from {first} and reprocess the {len(billable)} rejects" for b in late] or \
                  [f"reprocess the {len(billable)} {exc['group']} rejects"]
        out.append({"group": exc["group"], "line_no": exc["line_no"], "exception": "VOLUME", "classification": "carrier_favour",
                    "claim_class": ["fact"], "amount": c, "statements": [e["entry_id"]], "statement": text,
                    "internal_actions": actions, "evidence": {"rejects": len(billable), "segments": seg, "binds": binds}})
    rest = exc["segments"] - explained_seg
    if rest:
        c = led.calc(4, act, f"line_{exc['line_no']}_unexplained_volume", f"{rest} segments x {exc['rate']}",
                     {"volume": exc["amount"]}, q2(rest * D(exc["rate"])), ctx.sc["invoice"]["currency"],
                     f"{rest} billed segments on line {exc['line_no']} have no matching record on our side: "
                     f"{money(q2(rest * D(exc['rate'])))}.", [exc["amount"], row["line_cite"]])
        e = led.append(4, me, "statement", f"No usage or reject record explains {rest} of the segments billed on line "
                       f"{exc['line_no']} ({money(led.result(c))}); the carrier's message detail is needed to tell billing "
                       "errors from traffic we did not record.", [c], claim_class="missing_evidence")
        out.append({"group": exc["group"], "line_no": exc["line_no"], "exception": "VOLUME", "classification": "unresolved",
                    "claim_class": ["missing_evidence"], "amount": c, "statements": [e["entry_id"]], "statement": e["text"],
                    "evidence_needed": {"to": "carrier", "item": "message_detail", "network_group": exc["group"],
                                        "period": exc["period"][0][:7]}})
    return out


def _volume_from_detail(ctx, me, row, exc, detail):
    """Carrier message detail (MDR) against our records on supplier_msg_id (spec S5)."""
    s, led, sc = ctx.sys, ctx.ledger, ctx.sc
    agr, cur = sc["agreement"], sc["invoice"]["currency"]
    act = rules("step4-detail-match")
    ps, pe = (date.fromisoformat(x) for x in exc["period"])
    rows = [r for r in detail if r["mcc_mnc"] in exc["mcc_mnc"]]
    ours = {m["supplier_msg_id"] for m in s.usage.messages(sc["carrier_id"], ps, pe, set(exc["mcc_mnc"]))}
    our_binds = {c["connection_id"]: c for c in ctx.cmap if c["supplier_id"] == sc["carrier_id"]}
    seen, dup, own, matched, other = set(), [], [], [], []
    for r in rows:
        sid = r["supplier_msg_id"]
        if sid in seen:
            dup.append(r)
        elif sid in ours:
            matched.append(r)
        elif r["our_connection_id"] in our_binds:
            own.append(r)
        else:
            other.append(r)
        seen.add(sid)
    seg = lambda rs: sum(int(r["segments"]) for r in rs)
    rate = D(exc["rate"])
    ctx.detail_findings[exc["line_no"]] = {
        "rows": len(rows), "matched": {"messages": len(matched), "billable_segments": seg(matched)},
        "own_bind": {"messages": len(own), "billable_segments": seg(own), "binds": sorted({r["our_connection_id"] for r in own})},
        "duplicates": {"messages": len(dup), "billable_segments": seg(dup)},
        "unexplained": {"messages": len(other), "billable_segments": seg(other)}}
    led.append(4, act, "check", f"Carrier detail has {len(rows)} rows for line {exc['line_no']}: {len(matched)} match our "
               f"records on supplier_msg_id ({seg(matched)} segments), {len(own)} are on our binds but absent from our usage, "
               f"{len(dup)} repeat another row's supplier_msg_id, {len(other)} are unexplained.",
               [row["line_cite"]] + [r["cite_id"] for r in rows[:1]], result="pass" if seg(matched) == row["our_segments"] else "flag")
    out = []
    if own:
        binds = sorted({r["our_connection_id"] for r in own})
        first = min(r["submit_time_local"][:10] for r in own)
        c = led.calc(4, act, f"line_{exc['line_no']}_own_bind", f"{seg(own)} segments x {exc['rate']}",
                     {"detail_rows": [r["cite_id"] for r in own]}, q2(seg(own) * rate), cur,
                     f"{len(own)} carrier rows / {seg(own)} segments on our bind(s) {', '.join(binds)} are missing from our "
                     f"usage: {money(q2(seg(own) * rate))}.", [row["line_cite"]] + [r["cite_id"] for r in own])
        late = [b for b in binds if our_binds[b]["valid_from"] > first]
        why = f" {', '.join(late)} only enters our connection map on {our_binds[late[0]]['valid_from']}." if late else ""
        text = (f"{len(own)} messages / {seg(own)} segments were sent on our own bind(s) {', '.join(binds)} and are chargeable "
                f"under clause 2.1, so {money(led.result(c))} is in the carrier's favour.{why}")
        e = led.append(4, me, "statement", text, [c, row["line_cite"], s.contracts.clause_cite(agr["agreement_id"], "2.1")]
                       + [our_binds[b]["cite_id"] for b in binds], claim_class="fact")
        out.append({"group": exc["group"], "line_no": exc["line_no"], "exception": "VOLUME", "classification": "carrier_favour",
                    "claim_class": ["fact"], "amount": c, "statements": [e["entry_id"]], "statement": text,
                    "internal_actions": [f"add {b} to connection_map from {first} and recover its traffic into usage" for b in late]})
    if dup:
        c = led.calc(4, act, f"line_{exc['line_no']}_duplicates", f"{seg(dup)} segments x {exc['rate']}",
                     {"detail_rows": [r["cite_id"] for r in dup]}, q2(seg(dup) * rate), cur,
                     f"{len(dup)} carrier rows / {seg(dup)} segments repeat another row's supplier_msg_id: "
                     f"{money(q2(seg(dup) * rate))}.", [row["line_cite"]] + [r["cite_id"] for r in dup])
        text = (f"{len(dup)} carrier rows repeat a supplier_msg_id the carrier already billed; each message is "
                f"chargeable once, so {money(led.result(c))} is a supported duplicate-billing dispute.")
        e1 = led.append(4, me, "statement", text, [c, row["line_cite"], s.contracts.clause_cite(agr["agreement_id"], "4.2")]
                        + [r["cite_id"] for r in dup], claim_class="fact")
        e2 = led.append(4, me, "statement", f"The duplicate rows sum to {money(led.result(c))} at the billed rate.", [c],
                        claim_class="calculation")
        out.append({"group": exc["group"], "line_no": exc["line_no"], "exception": "VOLUME", "classification": "supported",
                    "claim_class": ["fact", "calculation"], "amount": c, "reason_code": "DUPLICATE",
                    "statements": [e1["entry_id"], e2["entry_id"]], "statement": text,
                    "dispute": {"destination": row["destination"], "mcc_mnc": exc["mcc_mnc"], "billed_rate": exc["rate"],
                                "correct_rate": None, "from_date": exc["period"][0], "through_date": exc["period"][1],
                                "supplier_msg_ids": len(dup)}})
    if other:
        c = led.calc(4, act, f"line_{exc['line_no']}_unexplained_detail", f"{seg(other)} segments x {exc['rate']}",
                     {"detail_rows": [r["cite_id"] for r in other]}, q2(seg(other) * rate), cur,
                     f"{len(other)} carrier rows match nothing on our side: {money(q2(seg(other) * rate))}.",
                     [row["line_cite"]] + [r["cite_id"] for r in other])
        e = led.append(4, me, "statement", f"{len(other)} carrier rows ({money(led.result(c))}) are not ours and are not "
                       "on our binds; the records do not show whether they are billing errors.", [c], claim_class="interpretation")
        out.append({"group": exc["group"], "line_no": exc["line_no"], "exception": "VOLUME", "classification": "unresolved",
                    "claim_class": ["interpretation"], "amount": c, "statements": [e["entry_id"]], "statement": e["text"]})
    return out


# ====================================================================== step 5: size exposure (rules)
def size_exposure(ctx, causes):
    led, sc = ctx.ledger, ctx.sc
    act = rules("step5-size-exposure")
    cur = sc["invoice"]["currency"]
    ctx.set_status("inProgress", "sizing", "sizing supported and unresolved amounts")
    pick = lambda cls: [c for c in causes if c["classification"] == cls]
    total = lambda cs: sum((D(led.result(c["amount"])) for c in cs), D("0.00"))
    sup, fav = pick("supported"), pick("carrier_favour")
    c_sup = led.calc(5, act, "supported", " + ".join(c["amount"].rsplit("/", 1)[1] for c in sup) or "0",
                     {c["amount"]: led.result(c["amount"]) for c in sup}, total(sup), cur,
                     f"Supported dispute amount {money(total(sup))} (facts and calculations only).", [c["amount"] for c in sup] or [ctx.calc_ids["variance"]])
    c_fav = led.calc(5, act, "explained_carrier_favour", " + ".join(c["amount"].rsplit("/", 1)[1] for c in fav) or "0",
                     {c["amount"]: led.result(c["amount"]) for c in fav}, total(fav), cur,
                     f"Explained in the carrier's favour {money(total(fav))}.", [c["amount"] for c in fav] or [ctx.calc_ids["variance"]])
    unresolved = sc["variance"] - total(sup) - total(fav)
    c_unr = led.calc(5, act, "unresolved", "variance - supported - explained_carrier_favour",
                     {"variance": ctx.calc_ids["variance"], "supported": c_sup, "explained": c_fav}, unresolved, cur,
                     f"Unresolved {money(unresolved)}.", [ctx.calc_ids["variance"], c_sup, c_fav])
    c_upct = led.calc(5, act, "unresolved_pct_of_invoice", "unresolved / invoiced x 100",
                      {"unresolved": c_unr, "invoiced": ctx.calc_ids["invoiced"]}, pct(unresolved, sc["invoiced"]), "%",
                      f"Unresolved is {pct(unresolved, sc['invoiced'])}% of the invoiced total.", [c_unr, ctx.calc_ids["invoiced"]])
    payable = sc["invoiced"] - total(sup)
    c_pay = led.calc(5, act, "undisputed_payable", "invoiced - supported", {"invoiced": ctx.calc_ids["invoiced"], "supported": c_sup},
                     payable, cur, f"Undisputed payable {money(payable)}.", [ctx.calc_ids["invoiced"], c_sup])
    from_causes = total(pick("unresolved"))
    led.append(5, act, "check", "Unresolved amount equals the sum of unresolved causes." if from_causes == unresolved else
               "Part of the variance is not covered by any classified cause; it stays unresolved.", [c_unr],
               result="pass" if from_causes == unresolved else "flag")
    figures = {"invoiced": str(sc["invoiced"]), "expected": str(sc["expected"]), "variance": str(sc["variance"]),
               "variance_pct_of_invoice": str(sc["variance_pct"]),
               "variance_pct_of_expected": str(sc["variance_pct_of_expected"]), "supported": str(total(sup)),
               "explained_carrier_favour": str(total(fav)), "unresolved": str(unresolved),
               "unresolved_pct_of_invoice": str(pct(unresolved, sc["invoiced"])), "undisputed_payable": str(payable)}
    ids = {"supported": c_sup, "explained_carrier_favour": c_fav, "unresolved": c_unr,
           "unresolved_pct_of_invoice": c_upct, "undisputed_payable": c_pay}
    ctx.case["figures"].update({k: {"value": figures[k], "calc_id": v} for k, v in ids.items()})
    ctx.save_case("exposure sized")
    return figures, ids


# ====================================================================== step 6: recommend (agent)
def recommend(ctx, figures, ids, causes, notes=None):
    s, led, sc, case = ctx.sys, ctx.ledger, ctx.sc, ctx.case
    agr = sc["agreement"]
    me = agent("recommender")
    th = ctx.thresholds
    clause = lambda c: s.contracts.clause_cite(agr["agreement_id"], c)
    supported, unresolved = D(figures["supported"]), D(figures["unresolved"])
    variance = D(figures["variance"])
    min_amt = D(agr.get("dispute_min_amount") or "0")
    deadline = date.fromisoformat(case["dispute_deadline"])
    days_left = (deadline - ctx.clock.now.date()).days
    material = D(figures["unresolved_pct_of_invoice"]) > D(str(th["request_evidence_unresolved_pct_of_invoice"])) and \
        (min_amt == 0 or unresolved >= min_amt)
    asked = {(r["to"], r["item"], r.get("network_group")) for r in case["evidence_requests"]}
    wanted = []
    for c in causes:
        need = c.get("evidence_needed")
        if c["classification"] == "unresolved" and need and (need["to"], need["item"], need.get("network_group")) not in asked:
            if need not in wanted:
                wanted.append(need)
    why, cites = [], []
    if material and wanted and days_left >= th["evidence_min_days_before_deadline"]:
        decision, amount = "request_evidence", None
        why.append(f"Unresolved {money(unresolved)} is {figures['unresolved_pct_of_invoice']}% of the invoice, above the "
                   f"{th['request_evidence_unresolved_pct_of_invoice']}% evidence threshold, and {days_left} days remain "
                   f"before the {deadline} dispute deadline.")
        cites += [ids["unresolved"], ids["unresolved_pct_of_invoice"], clause("8.1")]
    else:
        if supported > 0 and supported >= min_amt:
            decision = "full_dispute" if supported >= variance else "partial_dispute"
            amount = ids["supported"]
            why.append(f"Supported amount {money(supported)} rests on facts and calculations only and is at least the "
                       f"contract minimum dispute (clause 8.2).")
            cites += [ids["supported"], clause("8.2")]
        else:
            decision, amount = "approve", None
            why.append("No supported dispute amount at or above the contract minimum (clause 8.2), so the invoice is "
                       "approved for payment.")
            cites += [ids["supported"], clause("8.2")]
        if D(figures["explained_carrier_favour"]) > 0:
            why.append(f"{money(figures['explained_carrier_favour'])} of the variance is explained in the carrier's favour "
                       "by our own records and is paid, not disputed.")
            cites.append(ids["explained_carrier_favour"])
        if unresolved and not material:
            why.append(f"Unresolved {money(unresolved)} is {figures['unresolved_pct_of_invoice']}% of the invoice, below the "
                       f"{th['request_evidence_unresolved_pct_of_invoice']}% evidence threshold; it is recorded, not disputed.")
            cites += [ids["unresolved"], ids["unresolved_pct_of_invoice"]]
        elif unresolved and material:
            why.append(f"Unresolved {money(unresolved)} stays material but no further evidence can be obtained in time, "
                       "so it is reserved rather than disputed.")
            cites += [ids["unresolved"], clause("8.1")]
        if decision != "approve":
            why.append(f"Pay the undisputed {money(figures['undisputed_payable'])} by {case['payment_due']} (clause 8.3).")
            cites += [ids["undisputed_payable"], clause("8.3")]
    if notes:
        why.append(f"Revised after the analyst returned the case ({notes['entry_id']}).")
        cites.append(case["cite_id"])
    actions = [a for c in causes for a in c.get("internal_actions", [])]
    rationale = " ".join(why)
    entry = led.append(6, me, "decision", f"Recommend {decision.replace('_', ' ')}. {rationale}", cites,
                       decision=decision)
    rec = {"decision": decision, "amount_calc_id": amount, "amount": led.result(amount) if amount else None,
           "rationale": rationale, "cites": cites, "created_at": ctx.clock.iso(), "agent": me["name"],
           "entry_id": entry["entry_id"], "internal_actions": actions, "evidence_items": wanted if decision == "request_evidence" else []}
    for a in actions:
        led.append(6, me, "statement", f"Internal action: {a}.", [c["amount"] for c in causes if a in c.get("internal_actions", [])],
                   claim_class="interpretation")
    case["recommendation"] = rec
    ctx.save_case(f"recommended {decision}")
    return rec


# ====================================================================== step 7 helpers: submissions and packet
def build_submissions(ctx, causes, approved, analyst):
    """One S7 claim per reason code (one product and one bill period per dispute). An analyst
    edit lowers the total; it is allocated to claims in order."""
    sc, led = ctx.sc, ctx.ledger
    inv = sc["invoice"]
    remaining = D(approved)
    subs = []
    for c in [c for c in causes if c["classification"] == "supported"]:
        amt = min(D(led.result(c["amount"])), remaining)
        remaining -= amt
        if amt <= 0:
            continue
        d = c["dispute"]
        mcc = "/".join(f"{m[:3]}-{m[3:]}" for m in d["mcc_mnc"])
        subs.append({"our_reference": ctx.case["case_id"], "account_id": inv["account_id"], "invoice_id": inv["invoice_id"],
                     "product": f"A2P SMS - {d['destination']} ({mcc})", "bill_period": inv["period_start"][:7],
                     "disputed_amount": str(q2(amt)), "reason_code": c["reason_code"], "billed_rate": d["billed_rate"],
                     "correct_rate": d["correct_rate"], "from_date": d["from_date"], "through_date": d["through_date"],
                     "amount_calc_id": c["amount"], "supporting_files": [f"dispute-packet-{ctx.case['case_id']}.md"],
                     "submitted_by": analyst, "submitted_at": ctx.clock.iso()})
    return subs


def render_packet(ctx, causes, decision, submissions):
    """The case packet: dispute (or payment approval) plus the audit record compiled from the ledger."""
    case, sc, led = ctx.case, ctx.sc, ctx.ledger
    inv, f = sc["invoice"], case["figures"]
    rec = case["recommendation"]
    title = "Dispute packet" if submissions else "Payment approval"
    out = [f"# {title} {case['case_id']}: {inv['seller_party']['name']} invoice {inv['invoice_id']}", "",
           f"- Account {inv['account_id']}, agreement {sc['agreement']['document_number']}, period {inv['period_start']} to {inv['period_end']}",
           f"- Dispute deadline {case['dispute_deadline']}, payment due {case['payment_due']}",
           f"- Analyst decision: **{decision['decision']}** by {decision['analyst']} at {decision['decided_at']}"
           + (f", approved amount {money(decision['approved_amount'])}" if decision.get("approved_amount") else ""),
           "", "## Figures", "", "| Figure | Amount | Calc |", "|---|---:|---|"]
    for k in ("invoiced", "expected", "variance", "supported", "explained_carrier_favour", "unresolved", "undisputed_payable"):
        if k in f:
            out.append(f"| {k.replace('_', ' ')} | {money(f[k]['value'])} | {f[k]['calc_id'].rsplit('/', 1)[1]} |")
    if submissions:
        out += ["", "## Claims", "", "| Claim | Reason | Product | Period | Billed | Correct | Amount |", "|---|---|---|---|---|---|---:|"]
        for s in submissions:
            out.append(f"| {s.get('claim_id', 'pending')} | {s['reason_code']} | {s['product']} | {s['from_date']} to "
                       f"{s['through_date']} | {s['billed_rate']} | {s['correct_rate'] or 'not billable'} | {money(s['disputed_amount'])} |")
    out += ["", "## Causes", ""]
    for c in causes:
        out.append(f"- **{c['classification'].replace('_', ' ')}** ({', '.join(c['claim_class'])}), line {c['line_no']} "
                   f"{c['group']}: {c['statement']} [{c['amount'].rsplit('/', 1)[1]}]")
    out += ["", "## Recommendation", "", rec["rationale"], ""]
    if rec["internal_actions"]:
        out += ["Internal actions:", ""] + [f"- {a}" for a in rec["internal_actions"]] + [""]
    out += ["## Audit record (from the evidence ledger)", "", "| Entry | Step | Actor | Kind | Text | Cites |", "|---|---|---|---|---|---|"]
    for e in led.read():
        cites = ", ".join(e["cites"][:4]) + (f" (+{len(e['cites']) - 4} more)" if len(e["cites"]) > 4 else "")
        out.append(f"| {e['entry_id']} | {e['step']} | {e['actor']['kind']}:{e['actor']['name']} | {e['kind']} | "
                   f"{e['text'].replace('|', '/')} | {cites} |")
    return "\n".join(out) + "\n"

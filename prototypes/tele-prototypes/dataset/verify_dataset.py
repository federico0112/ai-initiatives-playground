#!/usr/bin/env python3
"""Independent check of the generated data: reads only the files (never the generator's
in-memory state) and re-derives every figure in answer-keys/. Exit code 1 on any failure."""
import csv
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, "sim-data")
INV = "NB-INV-2026-09-0412"
DLR = ["DELIVRD", "UNDELIV", "EXPIRED", ""]
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def q2(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def path(scn, rel):
    p = os.path.join(SIM, scn, rel)
    return p if os.path.exists(p) else None


def rows(scn, rel):
    return list(csv.DictReader(open(path(scn, rel))))


def deck_rows(scn):
    hist = rows(scn, "rates/deck_history.csv")
    out = []
    for h in hist:
        p = path(scn, f"rates/decks/{h['deck_id']}.csv")
        if p is None:
            continue
        for r in csv.DictReader(open(p)):
            r.update(deck_id=h["deck_id"], received_at=h["received_at"], notice_id=h["notice_id"])
            out.append(r)
    return hist, out


def contracted_rate(scn, mcc_mnc, d):
    """Spec S2 rule: exact MCC-MNC, later received wins, INCREASE only after notice days."""
    agr = json.load(open(path(scn, "contracts/agreements/AGR-NB-2025-014.json")))
    _h, drs = deck_rows(scn)
    cands = [r for r in drs if r["mcc_mnc"] == mcc_mnc and date.fromisoformat(r["effective_date"]) <= d]
    cands.sort(key=lambda r: r["received_at"], reverse=True)
    for r in cands:
        if r["change_flag"] == "INCREASE":
            n = json.load(open(path(scn, f"rates/notices/{r['notice_id']}.json")))
            sent = datetime.fromisoformat(n["sent_at"].replace("Z", "+00:00"))
            earliest = sent + timedelta(days=agr["rate_notice_days_increase"])
            first_full = earliest.date() + (timedelta(0) if earliest.time() == datetime.min.time() else timedelta(1))
            if d < first_full:
                continue
        return Decimal(r["rate"]), r["deck_id"]
    return None, None


def invoice(scn):
    return json.load(open(path(scn, f"invoices/inbox/{INV}/invoice.json")))


def rating_total(scn):
    return sum(Decimal(r["amount"]) for r in rows(scn, "rating/runs/RUN-2026-09/lines.csv"))


# ------------------------------------------------------------------ base
print("== base")
_h, drs = deck_rows("base")
mm2g = {r["mcc_mnc"]: r["network_group"] for r in drs if r["deck_id"] == "NB-SMS-2026-08-01"}
agg = defaultdict(lambda: [0] * 6)
smids = set()
dup_ids = bad_status = 0
for fn in sorted(os.listdir(os.path.join(SIM, "base/usage/messages"))):
    with gzip.open(os.path.join(SIM, "base/usage/messages", fn), "rt") as f:
        for r in csv.DictReader(f):
            a = agg[(r["submitted_at_utc"][:10], mm2g[r["mcc_mnc"]])]
            a[0] += 1
            a[1] += int(r["segments"])
            a[2 + DLR.index(r["dlr_stat"])] += 1
            bad_status += r["submit_status"] != "0x00000000"
            dup_ids += r["supplier_msg_id"] in smids
            smids.add(r["supplier_msg_id"])
check(dup_ids == 0, "no duplicate supplier_msg_id in message records")
check(bad_status == 0, "every stored message was accepted at submit (0x00000000)")
ds = {(r["date_utc"], r["network_group"]): [int(r[k]) for k in ("messages", "billable_segments", "delivered",
                                                                   "undelivered", "expired", "no_receipt")]
      for r in rows("base", "usage/daily_summary.csv")}
check(ds == dict(agg), f"daily_summary equals message roll-up exactly ({len(ds)} day-network rows, {len(smids)} messages)")

lines = rows("base", "rating/runs/RUN-2026-09/lines.csv")
check(all(q2(Decimal(l["rate"]) * int(l["billable_segments"])) == Decimal(l["amount"]) for l in lines),
      "every rated line amount = segments x rate (2dp)")
rated_by_dg = defaultdict(lambda: [0, 0])
for l in lines:
    rated_by_dg[(l["date_local"], l["network_group"])][0] += int(l["messages"])
    rated_by_dg[(l["date_local"], l["network_group"])][1] += int(l["billable_segments"])
check(all(rated_by_dg[k] == v[:2] for k, v in ds.items()), "rating run volumes equal daily summary")
bad = [l for l in lines if contracted_rate("base", l["mcc_mnc"], date.fromisoformat(l["date_local"]))
       != (Decimal(l["rate"]), l["deck_id"])]
check(not bad, "every rated line uses the contracted_rate rule row")
expected = rating_total("base")
inv = invoice("base")
ilines = inv["lines"]
isum = sum(Decimal(l["amount"]) for l in ilines)
check(expected == Decimal("25000.00"), f"expected = {expected}")
check(isum == Decimal(inv["subtotal"]) == Decimal(inv["total"]) == Decimal("27000.00"), f"invoice lines = subtotal = total = {isum}")
check(all(q2(Decimal(l["rate"]) * l["segments"]) == Decimal(l["amount"]) for l in ilines), "invoice line amounts recompute")
var = isum - expected
check(var / isum * 100 > 2, f"variance {var} = {q2(var / isum * 100)}% of invoiced > 2% -> case opens")


def seg(g, before=None):
    return sum(v[1] for (d, gg), v in ds.items() if gg == g and (before is None or d < before))


mx_early = seg("MX-ALT", "2026-09-15")
check(ilines[0]["segments"] == seg("MX-ALT") and ilines[0]["rate"] == "0.0185", "line 1 bills all MX-ALT segments at 0.0185")
supported = q2(mx_early * (Decimal("0.0185") - Decimal("0.0120")))
check(mx_early == 280000 and supported == Decimal("1820.00"), f"MX-ALT 09-01..14 {mx_early} segments -> supported {supported}")
rej = rows("base", "usage/rejects.csv")
unm = [r for r in rej if r["reject_reason"] == "UNMAPPED_CONNECTION"]
unm_seg = sum(int(r["segments"]) for r in unm)
cm = {r["connection_id"]: r for r in rows("base", "usage/connection_map.csv")}
check(ilines[2]["segments"] - seg("CO-AND") == unm_seg == 12000,
      f"CO-AND gap {ilines[2]['segments'] - seg('CO-AND')} segments = UNMAPPED_CONNECTION rejects ({len(unm)} messages, {unm_seg} segments)")
check(ilines[2]["messages"] - sum(v[0] for (d, g), v in ds.items() if g == "CO-AND") == len(unm),
      "CO-AND message gap = UNMAPPED_CONNECTION reject count")
check(all(r["smpp_connection"] == "SMPP-NB-07" and r["submitted_at_utc"][:10] < cm["SMPP-NB-07"]["valid_from"] for r in unm),
      "all UNMAPPED_CONNECTION rejects are SMPP-NB-07 before its connection_map valid_from")
check(all(r["supplier_msg_id"] == "" for r in rej if r["reject_reason"] == "SUBMIT_FAILED") and
      all(r["supplier_msg_id"] in smids for r in rej if r["reject_reason"] == "DUPLICATE"),
      "SUBMIT_FAILED rejects have no supplier ID; DUPLICATE rejects repeat a stored message")
check([l["segments"] for l in ilines[1:2] + ilines[3:]] == [seg(g) for g in ("MX-VRD", "BR-CRZ", "GT-QTZ")],
      "lines 2, 4, 5 match our volumes exactly")
undeliv = sum(v[3] + v[4] + v[5] for v in ds.values())
check(undeliv > 0, f"{undeliv} messages without a DELIVRD receipt are billed on both sides (clause 4.2), not a variance")
check(q2(var - supported) == Decimal("180.00"), "variance - supported = 180.00 carrier-favour")
agr = json.load(open(path("base", "contracts/agreements/AGR-NB-2025-014.json")))
deadline = date.fromisoformat(inv["issue_date"]) + timedelta(days=agr["dispute_window_days"])
check(str(deadline) == "2026-10-19" and inv["due_date"] == "2026-11-04", f"dispute deadline {deadline}, due {inv['due_date']}")
doc = open(path("base", "contracts/documents/AGR-NB-2025-014.md")).read()
check(all(f'id="clause-{c["clause_id"]}"' in doc for c in agr["clauses"]), "every clause has an anchor in the document")

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)

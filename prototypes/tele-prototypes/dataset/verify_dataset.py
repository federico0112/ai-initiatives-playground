#!/usr/bin/env python3
"""Independent check of the generated data: reads only the files (never the generator's
in-memory state) and re-derives every figure in answer-keys/. Exit code 1 on any failure."""
import csv
import gzip
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, "sim-data")
INV = "NB-INV-2026-09-0412"
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def q2(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def path(scn, rel):
    """Resolve rel inside a scenario, honouring overlay.json (inherits + deleted)."""
    root = os.path.join(SIM, scn)
    ov = os.path.join(root, "overlay.json")
    if os.path.exists(ov):
        o = json.load(open(ov))
        if rel in o["deleted"]:
            return None
        if os.path.exists(os.path.join(root, rel)):
            return os.path.join(root, rel)
        return path("base", rel)
    p = os.path.join(root, rel)
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


def contracted_rate(scn, code_group, d):
    """Spec S2 rule: longest code, later received wins, INCREASE only after notice days."""
    agr = json.load(open(path(scn, "contracts/agreements/AGR-NB-2025-014.json")))
    _h, drs = deck_rows(scn)
    cands = [r for r in drs if r["destination_group"] == code_group and date.fromisoformat(r["effective_date"]) <= d]
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


def code_to_group(scn):
    _h, drs = deck_rows("base")
    return sorted(((r["dial_code"], r["destination_group"]) for r in drs if r["deck_id"] == "NB-VOICE-2026-08-01"),
                  key=lambda x: -len(x[0]))


def invoice(scn):
    return json.load(open(path(scn, f"invoices/inbox/{INV}/invoice.json")))


def rating_total(scn):
    return sum(Decimal(r["amount"]) for r in rows(scn, "rating/runs/RUN-2026-09/lines.csv"))


# ------------------------------------------------------------------ base
print("== base")
c2g = code_to_group("base")
agg = defaultdict(lambda: [0, 0, 0])
call_ids = set()
dup_ids = 0
for fn in sorted(os.listdir(os.path.join(SIM, "base/usage/cdrs"))):
    with gzip.open(os.path.join(SIM, "base/usage/cdrs", fn), "rt") as f:
        for r in csv.DictReader(f):
            g = next(g for c, g in c2g if r["b_number"].startswith(c))
            dur = int(r["duration_sec"])
            a = agg[(r["start_time_utc"][:10], g)]
            a[0] += 1
            a[1] += dur
            a[2] += math.ceil(dur / 60)
            dup_ids += r["call_id"] in call_ids
            call_ids.add(r["call_id"])
check(dup_ids == 0, "no duplicate call_id in CDRs")
ds = {(r["date_utc"], r["destination_group"]): [int(r["calls"]), int(r["duration_sec"]), int(r["billable_minutes"])]
      for r in rows("base", "usage/daily_summary.csv")}
check(ds == dict(agg), f"daily_summary equals CDR roll-up exactly ({len(ds)} day-group rows, {len(call_ids)} CDRs)")

lines = rows("base", "rating/runs/RUN-2026-09/lines.csv")
check(all(q2(Decimal(l["rate"]) * int(l["billable_minutes"])) == Decimal(l["amount"]) for l in lines),
      "every rated line amount = minutes x rate (2dp)")
rated_by_dg = defaultdict(lambda: [0, 0])
for l in lines:
    rated_by_dg[(l["date_local"], l["destination_group"])][0] += int(l["calls"])
    rated_by_dg[(l["date_local"], l["destination_group"])][1] += int(l["billable_minutes"])
check(all(rated_by_dg[k] == [v[0], v[2]] for k, v in ds.items()), "rating run volumes equal daily summary")
bad = [l for l in lines if contracted_rate("base", l["destination_group"], date.fromisoformat(l["date_local"]))
       != (Decimal(l["rate"]), l["deck_id"])]
check(not bad, "every rated line uses the contracted_rate rule row")
expected = rating_total("base")
inv = invoice("base")
ilines = inv["lines"]
isum = sum(Decimal(l["amount"]) for l in ilines)
check(expected == Decimal("25000.00"), f"expected = {expected}")
check(isum == Decimal(inv["subtotal"]) == Decimal(inv["total"]) == Decimal("27000.00"), f"invoice lines = subtotal = total = {isum}")
check(all(q2(Decimal(l["rate"]) * l["minutes"]) == Decimal(l["amount"]) for l in ilines), "invoice line amounts recompute")
var = isum - expected
check(var / isum * 100 > 2, f"variance {var} = {q2(var / isum * 100)}% of invoiced > 2% -> case opens")

mx_early = sum(v[2] for (d, g), v in ds.items() if g == "MX-MOB" and d < "2026-09-15")
mx_all = sum(v[2] for (d, g), v in ds.items() if g == "MX-MOB")
check(ilines[0]["minutes"] == mx_all and ilines[0]["rate"] == "0.0185", "line 1 bills all MX-MOB minutes at 0.0185")
supported = q2(mx_early * (Decimal("0.0185") - Decimal("0.0120")))
check(mx_early == 280000 and supported == Decimal("1820.00"), f"MX-MOB 09-01..14 {mx_early} min -> supported {supported}")
co_ours = sum(v[2] for (d, g), v in ds.items() if g == "CO-MOB")
rej = rows("base", "usage/rejects.csv")
unm = [r for r in rej if r["reject_reason"] == "UNMAPPED_TRUNK"]
unm_min = sum(math.ceil(int(r["duration_sec"]) / 60) for r in unm)
tm = {r["trunk_id"]: r for r in rows("base", "usage/trunk_map.csv")}
check(ilines[2]["minutes"] - co_ours == unm_min == 12000 and len(unm) == 4000,
      f"CO-MOB gap {ilines[2]['minutes'] - co_ours} min = UNMAPPED_TRUNK rejects ({len(unm)} calls, {unm_min} min)")
check(all(r["egress_trunk"] == "TRK-NB-07" and r["start_time_utc"][:10] < tm["TRK-NB-07"]["valid_from"] for r in unm),
      "all UNMAPPED_TRUNK rejects are TRK-NB-07 before its trunk_map valid_from")
check(all(Decimal(a["amount"]) == q2(Decimal(a["rate"]) * a["minutes"]) for a in ilines[1:2] + ilines[3:]) and
      [l["minutes"] for l in ilines[1:2] + ilines[3:]] ==
      [sum(v[2] for (d, g), v in ds.items() if g == G) for G in ("MX-FIX", "BR-MOB", "GT-MOB")],
      "lines 2, 4, 5 match our volumes exactly")
check(q2(var - supported) == Decimal("180.00"), "variance - supported = 180.00 carrier-favour")
agr = json.load(open(path("base", "contracts/agreements/AGR-NB-2025-014.json")))
deadline = date.fromisoformat(inv["issue_date"]) + timedelta(days=agr["dispute_window_days"])
check(str(deadline) == "2026-10-19" and inv["due_date"] == "2026-11-04", f"dispute deadline {deadline}, due {inv['due_date']}")
doc = open(path("base", "contracts/documents/AGR-NB-2025-014.md")).read()
check(all(f'id="clause-{c["clause_id"]}"' in doc for c in agr["clauses"]), "every clause has an anchor in the document")

# ------------------------------------------------------------------ variants
print("== v1")
v1_unm = [r for r in rows("v1", "usage/rejects.csv") if r["reject_reason"] == "UNMAPPED_TRUNK"]
check(not v1_unm and q2(Decimal(180) / Decimal(27000) * 100) == Decimal("0.67"), "v1: no CO rejects; 180.00 = 0.67% < 1%")

print("== v2")
inv2 = invoice("v2")
t2 = sum(Decimal(l["amount"]) for l in inv2["lines"])
unres = t2 - expected - supported
check(t2 == Decimal("27420.00") and unres == Decimal("600.00") and unres / t2 * 100 > 1,
      f"v2: invoice {t2}, unresolved {unres} = {q2(unres / t2 * 100)}% > 1% -> request evidence")
det_p = os.path.join(SIM, "v2/staged/invoices/inbox", INV, "cdr_detail.csv.gz")
with gzip.open(det_p, "rt") as f:
    det = list(csv.DictReader(f))
seen, dmin, nb7 = set(), 0, 0
for r in det:
    k = (r["start_time_local"], r["b_number_masked"], r["duration_sec"])
    if k in seen:
        dmin += int(r["billed_minutes"])
    seen.add(k)
    if r["our_trunk_id"] == "TRK-NB-07":
        nb7 += int(r["billed_minutes"])
check(sum(int(r["billed_minutes"]) for r in det) == inv2["lines"][2]["minutes"] == 340000, "v2: detail minutes = invoice line 3 = 340000")
check(dmin == 28000 and nb7 == 12000, f"v2: duplicates {dmin} min (420.00 supported), TRK-NB-07 {nb7} min (180.00 carrier favour)")

print("== v3")
r3, _ = contracted_rate("v3", "MX-MOB", date(2026, 9, 19))
r3b, _ = contracted_rate("v3", "MX-MOB", date(2026, 9, 20))
check(r3 == Decimal("0.0120") and r3b == Decimal("0.0185"), "v3: increase effective 2026-09-20 under the rule")
l3 = rows("v3", "rating/runs/RUN-2026-09/lines.csv")
check(all(contracted_rate("v3", l["destination_group"], date.fromisoformat(l["date_local"]))[0] == Decimal(l["rate"]) for l in l3),
      "v3: rating run follows the rule")
e3 = rating_total("v3")
s3 = q2(sum(v[2] for (d, g), v in ds.items() if g == "MX-MOB" and d < "2026-09-20") * Decimal("0.0065"))
check(e3 == Decimal("24350.00") and s3 == Decimal("2470.00") and Decimal(27000) - e3 - s3 == Decimal("180.00"),
      f"v3: expected {e3}, supported {s3}, remainder 180.00")

print("== v4")
check(path("v4", "rates/decks/NB-VOICE-2026-09-07.csv") is None and
      any(h["deck_id"] == "NB-VOICE-2026-09-07" for h in rows("v4", "rates/deck_history.csv")),
      "v4: deck referenced in history but file missing")

print("== v5")
inv5 = invoice("v5")
check(sum(Decimal(l["amount"]) for l in inv5["lines"]) == Decimal("27270.00") and inv5["subtotal"] == "27000.00",
      "v5: lines 27270.00 vs subtotal 27000.00")

print("== v6")
inv6 = invoice("v6")
check(sum(Decimal(l["amount"]) for l in inv6["lines"]) == Decimal(inv6["total"]) == Decimal("25000.00"), "v6: invoice 25000.00, no case")

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)

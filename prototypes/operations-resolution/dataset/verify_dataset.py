#!/usr/bin/env python3
"""Re-derive every figure in the answer keys from the generated files only, and check the design.

Independent of generate_dataset.py on purpose (no imports from it): if both agree, the files say what
the answer keys say. Uses telco_platform for overlay resolution and contract PDF parsing. Exits 1 on
any failed check.

  python3 verify_dataset.py                       # checks ./sim-data
  python3 verify_dataset.py --root /path/to/sim-data
"""
import argparse
import bisect
import csv
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from telco_platform.contracts.pdf import parse_contract
from telco_platform.sim.overlay import DataRoot

UTC = timezone.utc
FAILS = []
PASSES = 0


def check(cond, msg):
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(msg)
        print("FAIL", msg)


def dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def iso(d):
    return d.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z") if d else None


def pct(a, b):
    return str(Decimal(100 * a / b).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) if b else None


class Root(DataRoot):
    """A scenario data root with overlay resolution (telco_platform.sim.overlay)."""

    def __init__(self, sim, scn):
        super().__init__(Path(sim) / scn)

    def json(self, rel):
        p = self.path(rel)
        return json.loads(p.read_text()) if p else None

    def csv(self, rel):
        with open(self.require(rel), newline="") as f:
            return list(csv.DictReader(f))


def load_messages(root):
    rows = []
    for p in [root.require(rel) for rel in root.list("messages") if rel.endswith(".csv.gz")]:
        with gzip.open(p, "rt", newline="") as f:
            for r in csv.DictReader(f):
                r["_sub"], r["_dlr"] = dt(r["submitted_at"]), dt(r["dlr_received_at"])
                rows.append(r)
    rows.sort(key=lambda r: r["_sub"])
    subs = [r["_sub"] for r in rows]
    return rows, subs


def stats(msgs, t0, t1, as_of, net=None, sup=None, cust=None, not_cust=None):
    rows, subs = msgs
    out = {"submitted": 0, "delivered": 0, "with_receipt": 0, "errors": {}}
    for r in rows[bisect.bisect_left(subs, t0):bisect.bisect_left(subs, t1)]:
        if (net and r["network_group"] != net) or (sup and r["supplier_id"] != sup) or \
                (cust and r["customer_id"] != cust) or (not_cust and r["customer_id"] == not_cust):
            continue
        out["submitted"] += 1
        if r["_dlr"] and r["_dlr"] <= as_of:
            out["with_receipt"] += 1
            if r["dlr_stat"] == "DELIVRD":
                out["delivered"] += 1
            else:
                k = f"{r['dlr_stat']} {r['dlr_err']}"
                out["errors"][k] = out["errors"].get(k, 0) + 1
    out["delivery_pct"] = pct(out["delivered"], out["submitted"])
    out["receipt_coverage_pct"] = pct(out["with_receipt"], out["submitted"])
    return out


def base_pct(msgs, t0, t1, lag, **kw):
    s = d = 0
    for k in range(1, 8):
        x = stats(msgs, t0 - timedelta(days=k), t1 - timedelta(days=k), t1 - timedelta(days=k) + lag, **kw)
        s, d = s + x["submitted"], d + x["delivered"]
    return pct(d, s), round(s / 7)


def near(a, target, tol):
    return a is not None and abs(Decimal(a) - Decimal(str(target))) <= Decimal(str(tol))


def verify(sim, scn, tol):
    print(f"--- {scn}")
    root = Root(sim, scn)
    key = json.loads((sim / "answer-keys" / f"{scn}.json").read_text())
    msgs = load_messages(root)
    rows = msgs[0]

    # 1. Required records exist.
    for rel in ["alerts/alarms.jsonl", "routing/route_history.csv", "salesforce/Account/SUP-NB.json",
                "reference/error_codes.csv", "reference/networks.csv", "config/monitoring.json",
                "tests/active_tests.csv", "notices/MN-NB-2026-0922.json", "scenario.yaml"]:
        check(root.path(rel) is not None, f"{scn}: missing {rel}")

    # 2. Message-level integrity.
    errs = {r["err"] for r in root.csv("reference/error_codes.csv")}
    routes = root.csv("routing/route_history.csv")
    def weights(net, t):
        w = None
        for r in routes:
            if r["network_group"] == net and dt(r["effective_at"]) <= t:
                w = dict(x.split(":") for x in r["weights"].split("|"))
        return w
    ids, bad_route, bad_time, bad_err, bad_exp = set(), 0, 0, 0, 0
    validity = timedelta(hours=4)
    for r in rows:
        ids.add(r["msg_id"])
        w = weights(r["network_group"], r["_sub"])
        if not w or int(w.get(r["supplier_id"], 0)) == 0:
            bad_route += 1
        if r["_dlr"] and r["_dlr"] < r["_sub"]:
            bad_time += 1
        if r["dlr_stat"] and r["dlr_err"] not in errs:
            bad_err += 1
        if r["dlr_stat"] == "EXPIRED" and not (validity <= r["_dlr"] - r["_sub"] <= validity + timedelta(seconds=61)):
            bad_exp += 1
    check(len(ids) == len(rows), f"{scn}: duplicate msg_id")
    check(bad_route == 0, f"{scn}: {bad_route} messages on a supplier with no route weight at submit time")
    check(bad_time == 0, f"{scn}: {bad_time} receipts before submission")
    check(bad_err == 0, f"{scn}: {bad_err} receipts with unknown error code")
    check(bad_exp == 0, f"{scn}: {bad_exp} EXPIRED receipts not at submit + validity")

    # 3. Alarm record and alert-window figures.
    alarm = json.loads(root.path("alerts/alarms.jsonl").read_text().splitlines()[0])
    cti = alarm["crossedThresholdInformation"]
    at, t0, t1 = dt(alarm["alarmRaisedTime"]), dt(cti["window_start"]), dt(cti["window_end"])
    lag = at - t1
    net = stats(msgs, t0, t1, at, net="MX-ALT")
    bl, _ = base_pct(msgs, t0, t1, lag, net="MX-ALT")
    check(cti["observedValue"] == net["delivery_pct"], f"{scn}: alarm observed {cti['observedValue']} != {net['delivery_pct']}")
    check(cti["baselineValue"] == bl, f"{scn}: alarm baseline {cti['baselineValue']} != {bl}")
    check(cti["submitted"] == net["submitted"], f"{scn}: alarm submitted mismatch")
    check(near(net["delivery_pct"], 68.0, tol), f"{scn}: alert {net['delivery_pct']}% not ~68%")
    check(near(bl, 94.0, tol), f"{scn}: baseline {bl}% not ~94%")
    aw = key["alert_window"]
    for f in ("submitted", "delivered", "with_receipt", "delivery_pct", "receipt_coverage_pct"):
        check(aw["network"][f] == net[f], f"{scn}: key alert_window.network.{f}")
    check(aw["errors"] == dict(sorted(net["errors"].items(), key=lambda kv: -kv[1])), f"{scn}: key errors")
    for s, v in aw["by_supplier"].items():
        x = stats(msgs, t0, t1, at, net="MX-ALT", sup=s)
        check(v["delivery_pct"] == x["delivery_pct"] and v["submitted"] == x["submitted"], f"{scn}: key by_supplier {s}")
        check(v["baseline_pct"] == base_pct(msgs, t0, t1, lag, net="MX-ALT", sup=s)[0], f"{scn}: key baseline {s}")
        w = weights("MX-ALT", t0)
        share = 100 * x["submitted"] / net["submitted"]
        exp_share = 100 * int(w.get(s, 0)) / sum(int(v) for v in w.values())
        if scn != "v4":  # v4 changes weights inside the window
            check(abs(share - exp_share) <= 2 + (0 if tol < 1 else 3), f"{scn}: {s} share {share:.1f}% vs weight {exp_share}%")
    for c, v in aw["by_customer"].items():
        check(v["delivery_pct"] == stats(msgs, t0, t1, at, net="MX-ALT", cust=c)["delivery_pct"], f"{scn}: key customer {c}")
    ex3 = stats(msgs, t0, t1, at, net="MX-ALT", not_cust="CUST-003")
    check(aw["excluding_CUST-003"]["delivery_pct"] == ex3["delivery_pct"], f"{scn}: key excluding CUST-003")
    vrd = stats(msgs, t0, t1, at, net="MX-VRD")
    check(aw["control_network_MX-VRD"]["delivery_pct"] == vrd["delivery_pct"], f"{scn}: key control network")
    check(near(vrd["delivery_pct"], 94.0, 1 + tol), f"{scn}: control network not at baseline ({vrd['delivery_pct']})")

    # 4. Hourly figures and the recovery rule.
    run, verified = 0, None
    check_from = dt(key["recovery"]["check_from"])
    for h in key["hourly_MX-ALT"]:
        t = dt(h["hour"])
        x = stats(msgs, t, t + timedelta(hours=1), t + timedelta(hours=1, minutes=5), net="MX-ALT")
        b, bv = base_pct(msgs, t, t + timedelta(hours=1), timedelta(minutes=5), net="MX-ALT")
        ok = Decimal(x["delivery_pct"]) >= Decimal(b) - 2 and x["submitted"] >= 0.5 * bv
        check(h["delivery_pct"] == x["delivery_pct"] and h["baseline_pct"] == b and h["meets_recovery_rule"] == ok,
              f"{scn}: hourly {h['hour']}")
        if t >= check_from and verified is None:
            run = run + 1 if ok else 0
            if run == 3:
                verified = iso(t + timedelta(hours=1, minutes=5))
    check(key["recovery"]["verified_at"] == verified, f"{scn}: recovery verified_at {key['recovery']['verified_at']} != {verified}")

    # 5. SLA figures against the agreement record and its document.
    excl_undeliv = {"001", "009", "011", "013"}
    day0 = datetime(2026, 9, 22, tzinfo=UTC)
    for s, v in key["sla_2026-09-22_MX-ALT"].items():
        agr = root.json(f"suppliers/agreements/{v['agreement']}.json")
        parsed = parse_contract(root.require(agr["signed_pdf"]), agr["agreement_id"], system="suppliers")
        doc = " ".join(f"{c.data['title']}. {c.data['text']}" for c in parsed.clauses)
        check([c.data["clause_id"] for c in parsed.clauses] == [c["clause_id"] for c in agr["clauses"]],
              f"{scn}: clauses parsed from {v['agreement']} PDF differ from the agreement record")
        price = next(p["price"] for p in agr["prices"] if p["network_group"] == "MX-ALT")
        check(price in doc, f"{scn}: price {price} for {s} not in the signed PDF")
        target = agr["sla"]["delivery_target_pct"] if agr["sla"] else None
        if target:
            check(f"{target}%" in doc, f"{scn}: target {target}% for {s} not in the signed PDF")
        acc = dlv = exc = parts = 0
        for r in rows[bisect.bisect_left(msgs[1], day0):bisect.bisect_left(msgs[1], day0 + timedelta(days=1))]:
            if r["network_group"] != "MX-ALT" or r["supplier_id"] != s:
                continue
            acc += 1
            parts += int(r["segments"])
            dlv += r["dlr_stat"] == "DELIVRD"
            exc += (r["dlr_stat"] == "UNDELIV" and r["dlr_err"] in excl_undeliv) or \
                   (r["dlr_stat"] == "EXPIRED" and r["dlr_err"] == "006")
        rate = pct(dlv, acc - exc)
        charges = (Decimal(price) * parts).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        check(v["sla_delivery_pct"] == rate and v["accepted"] == acc and v["day_charges"] == str(charges),
              f"{scn}: SLA figures for {s}")
        breach = bool(target) and Decimal(rate) < Decimal(target)
        check(v["breach"] == breach, f"{scn}: SLA breach flag for {s}")
        if breach and s == "SUP-NB":
            credit = (charges * Decimal(agr["sla"]["service_credit"]["pct_of_day_charges"]) / 100).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            check(v.get("service_credit") == str(credit), f"{scn}: service credit {v.get('service_credit')} != {credit}")

    # 6. Scenario design: the planted pattern is in the files.
    sup = {s: stats(msgs, t0, t1, at, net="MX-ALT", sup=s) for s in aw["by_supplier"]}
    tests = root.csv("tests/active_tests.csv")
    def test_fail(s, a, b):
        sel = [t for t in tests if t["supplier_id"] == s and t["network_group"] == "MX-ALT" and a <= dt(t["sent_at"]) < b]
        return sel and all(not t["handset_received_at"] for t in sel)
    has_pr_notice = root.path("notices/MN-PR-2026-0922.json") is not None
    has_ticket = root.path("tickets/customer/SF-00012871.json") is not None
    if scn in ("base", "v2"):
        check(near(sup["SUP-NB"]["delivery_pct"], 62.0, tol), f"{scn}: NB not ~62%")
        check(near(sup["SUP-PR"]["delivery_pct"], 92.0, tol + 0.5), f"{scn}: PR not ~92%")
        check(key["sla_2026-09-22_MX-ALT"]["SUP-NB"]["breach"], f"{scn}: NB SLA should be breached")
        check(test_fail("SUP-NB", dt("2026-09-22T16:30:00Z"), dt("2026-09-22T19:00:00Z")), f"{scn}: NB tests should fail")
        check(not test_fail("SUP-PR", dt("2026-09-22T16:30:00Z"), dt("2026-09-22T19:00:00Z")), f"{scn}: PR tests should pass")
        fix = dt("2026-09-22T19:30:00Z" if scn == "base" else "2026-09-22T20:40:00Z")
        after = stats(msgs, fix, fix + timedelta(hours=1), fix + timedelta(hours=1, minutes=5), net="MX-ALT", sup="SUP-NB")
        check(near(after["delivery_pct"], 94.5, tol + 0.5), f"{scn}: NB not recovered after fix")
        if scn == "v2":
            mid = stats(msgs, dt("2026-09-22T18:10:00Z"), dt("2026-09-22T19:10:00Z"), dt("2026-09-22T19:15:00Z"),
                        net="MX-ALT", sup="SUP-NB")
            check(near(mid["delivery_pct"], 62.0, tol + 0.5), "v2: NB should still be degraded after the 18:10 reply")
    if scn == "v1":
        check(Decimal(ex3["delivery_pct"]) >= Decimal("92.5"), "v1: excluding CUST-003 should be near baseline")
        check(abs(Decimal(sup["SUP-NB"]["delivery_pct"]) - Decimal(sup["SUP-PR"]["delivery_pct"])) <= 3 + tol,
              "v1: both suppliers should drop alike")
        subscriber = sum(v for k, v in net["errors"].items() if k in ("UNDELIV 001", "UNDELIV 011", "UNDELIV 013"))
        check(subscriber >= 0.8 * sum(net["errors"].values()), "v1: failures should be mostly subscriber errors")
        check(not has_ticket, "v1: no customer ticket expected")
    if scn == "v3":
        check(Decimal(net["receipt_coverage_pct"]) < 80, "v3: receipt coverage should be low at alert time")
        final = stats(msgs, t0, t1, dt("2026-09-22T19:00:00Z"), net="MX-ALT")
        check(Decimal(final["delivery_pct"]) >= 92, f"v3: queued messages should deliver after the window ({final['delivery_pct']})")
        check(has_pr_notice, "v3: operator notice MN-PR-2026-0922 missing")
    else:
        check(not has_pr_notice, f"{scn}: operator notice should only exist in v3")
    if scn == "v4":
        ids = {r["change_id"] for r in routes}
        check({"RC-2026-0922-01", "RC-2026-0922-02"} <= ids, "v4: route changes missing")
        check(near(sup["SUP-CB"]["delivery_pct"], 56.6, tol + 0.5), "v4: Cobalt not ~56.6%")
        check(root.json("suppliers/agreements/AGR-CB-SMS-2026-012.json")["sla"] is None, "v4: Cobalt should have no SLA")
    else:
        check("RC-2026-0922-01" not in {r["change_id"] for r in routes}, f"{scn}: v4 route change leaked")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent / "sim-data"))
    args = ap.parse_args()
    sim = Path(args.root)
    meta = json.loads((sim / "answer-keys/_meta.json").read_text())
    tol = 0.3 if meta["scale"] >= 0.5 else 2.0
    for scn in meta["scenarios"]:
        verify(sim, scn, tol)
    print(f"{PASSES} checks passed, {len(FAILS)} failed (scale {meta['scale']})")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

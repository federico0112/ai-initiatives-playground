#!/usr/bin/env python3
"""Generate the simulated data and test cases for prototype two (Operations Resolution Agents).

Case: A2P SMS delivery to Mexico / Altavia Movil (MCC-MNC 334-990, fictional network) falls from
94% to 68%. See ../simulated-systems.md for the systems, record schemas and scenarios.

Writes, under --out (default ./sim-data next to this script):
  base/            full data root for the seeded case
  v1 .. v4/        overlays on base: only files that differ, plus overlay.json
  answer-keys/     ground truth per scenario (keep away from the agents)

Nothing generated here is meant to be committed. Needs the shared telco_platform package
(`pip install -e platform` from the repo root). Fixed seed, so the output is byte-for-byte
reproducible for a given --scale.

  python3 generate_dataset.py                 # full size, about 470k messages in base
  python3 generate_dataset.py --scale 0.1     # small and fast, for tests and CI
  python3 verify_dataset.py                   # re-derives every answer-key figure from the files
"""
import argparse
import bisect
import csv
import gzip
import json
import random
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from telco_platform.sim.datagen import write_csv, write_csv_gz, write_json, write_text
from telco_platform.sim.overlay import DataRoot
from telco_platform.sim.render_pdf import render_markdown_pdf

SEED = 20260922
UTC = timezone.utc
LOCAL_OFFSET = timedelta(hours=-6)          # Mexico City, no DST
H_START = datetime(2026, 9, 15, tzinfo=UTC)  # data horizon
H_END = datetime(2026, 9, 23, 6, tzinfo=UTC)
INCIDENT_DAY = "2026-09-22"
BUCKET = timedelta(minutes=5)
VALIDITY = timedelta(hours=4)                # platform validity period; EXPIRED receipts arrive then


def T(hhmm, day=INCIDENT_DAY):
    return datetime.fromisoformat(f"{day}T{hhmm}:00+00:00")


ALERT_AT = T("17:10")
ALERT_WINDOW = (T("16:05"), T("17:05"))

NETWORKS = {
    "MX-ALT": {"mcc_mnc": "334990", "name": "Altavia Movil", "country": "Mexico", "per_hour": 1500,
               "areas": ["55", "33", "81", "22"]},
    "MX-VRD": {"mcc_mnc": "334991", "name": "Verdemovil", "country": "Mexico", "per_hour": 600,
               "areas": ["55", "33", "66"]},
}
SUPPLIERS = {
    "SUP-NB": {"name": "Northbridge Messaging", "agreement": "AGR-NB-SMS-2025-031", "prefix": "nb"},
    "SUP-PR": {"name": "Pacifica Route", "agreement": "AGR-PR-SMS-2026-007", "prefix": "pr"},
    "SUP-CB": {"name": "Cobalt SMS", "agreement": "AGR-CB-SMS-2026-012", "prefix": "cb"},
}
CUSTOMERS = [  # id, name, traffic_type, sender_type, sender_id, share of normal traffic
    ("CUST-001", "Banco Litoral", "otp", "alphanumeric", "BLITORAL", 0.50),
    ("CUST-002", "Rapido Envios", "transactional", "alphanumeric", "RAPIDO", 0.35),
    ("CUST-003", "Mercado Sol", "marketing", "shortcode", "45678", 0.15),
]
CUST = {c[0]: c for c in CUSTOMERS}

# Final delivery ratio per (network, supplier) on a normal day. NB/PR on ALT at 80/20 blend to 94.0%.
BASE_P = {("MX-ALT", "SUP-NB"): 0.945, ("MX-ALT", "SUP-PR"): 0.920,
          ("MX-VRD", "SUP-NB"): 0.950, ("MX-VRD", "SUP-PR"): 0.930,
          ("MX-ALT", "SUP-CB"): 0.945}
# Failure mixes: (stat, err, share). NODLR = no receipt ever arrives.
MIX_BASE = [("UNDELIV", "001", .35), ("EXPIRED", "006", .35), ("UNDELIV", "013", .15),
            ("UNDELIV", "022", .10), ("NODLR", "", .05)]
MIX_NB_FAULT = [("UNDELIV", "022", .75), ("EXPIRED", "000", .25)]
MIX_CB_FILTER = [("UNDELIV", "011", .60), ("UNDELIV", "009", .25), ("UNDELIV", "022", .15)]
MIX_BAD_LIST = [("UNDELIV", "001", .80), ("UNDELIV", "011", .20)]

ERROR_CODES = [  # err, name, permanent, sla_excluded, meaning
    ("000", "No error / no network response", "no", "no", "Used with EXPIRED when the network never answered"),
    ("001", "Unknown subscriber", "yes", "yes", "Number not assigned"),
    ("005", "Unidentified subscriber", "no", "no", "HLR/MSC mismatch"),
    ("006", "Absent subscriber SM", "no", "yes", "Handset off or out of coverage (excluded only with EXPIRED)"),
    ("009", "Illegal subscriber", "yes", "yes", "Authentication failed; can indicate filtering"),
    ("011", "Teleservice not provisioned", "yes", "yes", "SMS disabled for receiver, or blocked by the network"),
    ("013", "Call barred", "no", "yes", "Barred or deactivated by the operator"),
    ("015", "Facility not supported", "no", "no", "Can indicate network filtering"),
    ("020", "SM delivery failure", "no", "no", "Network-side delivery failure"),
    ("022", "System failure", "no", "no", "Generic error in the destination network"),
]
SLA_EXCLUDED_UNDELIV = {"001", "009", "011", "013"}

INITIAL_ROUTES = {"MX-ALT": {"SUP-NB": 80, "SUP-PR": 20}, "MX-VRD": {"SUP-NB": 70, "SUP-PR": 30}}
ROUTE_CHANGES = [  # change_id, at, network, weights, changed_by, actor_kind, reason, ticket
    ("RC-2026-0801-01", datetime(2026, 8, 1, tzinfo=UTC), "MX-ALT", {"SUP-NB": 80, "SUP-PR": 20},
     "a.ruiz", "person", "Quarterly route plan Q3", "NETOPS-2104"),
    ("RC-2026-0801-02", datetime(2026, 8, 1, tzinfo=UTC), "MX-VRD", {"SUP-NB": 70, "SUP-PR": 30},
     "a.ruiz", "person", "Quarterly route plan Q3", "NETOPS-2104"),
    ("RC-2026-0918-01", datetime(2026, 9, 18, 15, tzinfo=UTC), "MX-VRD", {"SUP-NB": 60, "SUP-PR": 40},
     "quality-rule QR-7", "auto_rule", "Rebalance on 24h delivery-ratio spread", ""),
]

# ---------------------------------------------------------------------------------------------
# Scenarios. Each modifier changes how messages behave inside a time window on the incident day.
# ---------------------------------------------------------------------------------------------
SCENARIOS = {
    "base": {
        "title": "Supplier connection fault after its maintenance",
        "degrade": [{"network": "MX-ALT", "supplier": "SUP-NB", "start": T("16:05"), "end": T("19:30"),
                     "p": 0.62, "mix": MIX_NB_FAULT}],
        "fix_or_end": T("19:30"),
    },
    "v1": {
        "title": "Misleading alert: one sender's bad list, routes healthy",
        "burst": {"network": "MX-ALT", "customer": "CUST-003", "start": T("16:00"), "end": T("17:30"),
                  "p": 0.15, "mix": MIX_BAD_LIST, "target_blend": 0.68},
        "fix_or_end": T("17:30"),
    },
    "v2": {
        "title": "Incomplete supplier status: first answer says no fault",
        "degrade": [{"network": "MX-ALT", "supplier": "SUP-NB", "start": T("16:05"), "end": T("20:40"),
                     "p": 0.62, "mix": MIX_NB_FAULT}],
        "fix_or_end": T("20:40"),
    },
    "v3": {
        "title": "Wait for more evidence: operator maintenance, receipts queued",
        "queue": {"network": "MX-ALT", "start": T("16:00"), "end": T("18:00"), "release": T("18:00"),
                  "release_span_min": 25, "target_observed": 0.68},
        "notices_extra": ["MN-PR-2026-0922"],
        "fix_or_end": T("18:00"),
    },
    "v4": {
        "title": "Own route change moved traffic to a filtered supplier",
        "route_changes": [
            ("RC-2026-0922-01", T("16:00"), "MX-ALT", {"SUP-NB": 30, "SUP-CB": 70}, "j.ortega", "person",
             "Cost optimisation: shift Altavia to Cobalt", "NETOPS-2231"),
            ("RC-2026-0922-02", T("17:45"), "MX-ALT", {"SUP-NB": 80, "SUP-PR": 20}, "noc.oncall", "person",
             "Revert RC-2026-0922-01 after Altavia delivery drop", "NETOPS-2236"),
        ],
        "stream_p": {("MX-ALT", "SUP-CB"): (0.566, MIX_CB_FILTER)},
        "fix_or_end": T("17:45"),
    },
}

# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def iso(dt, ms=False):
    if dt is None:
        return ""
    s = dt.astimezone(UTC).isoformat(timespec="milliseconds" if ms else "seconds")
    return s.replace("+00:00", "Z")


def q2(x):
    return str(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class Diffuser:
    """Error-diffusion rounding: cumulative integer counts track the cumulative real target."""

    def __init__(self):
        self.target = {}
        self.done = {}
        self.carry = {}

    def split(self, key, total, weights):
        """Split total exactly by weights, carrying rounding residue to the next call with this key."""
        s = sum(weights)
        want = [c + total * w / s for c, w in zip(self.carry.get(key, [0.0] * len(weights)), weights)]
        out = [max(int(x // 1), 0) for x in want]
        while sum(out) < total:
            i = max(range(len(want)), key=lambda i: want[i] - out[i])
            out[i] += 1
        while sum(out) > total:
            i = min((i for i in range(len(want)) if out[i] > 0), key=lambda i: want[i] - out[i])
            out[i] -= 1
        self.carry[key] = [x - o for x, o in zip(want, out)]
        return out

    def take(self, key, x):
        self.target[key] = self.target.get(key, 0.0) + x
        n = round(self.target[key]) - self.done.get(key, 0)
        n = max(n, 0)
        self.done[key] = self.done.get(key, 0) + n
        return n


DIURNAL_LOCAL = [0.25, 0.18, 0.15, 0.15, 0.20, 0.35, 0.60, 0.90, 1.20, 1.40, 1.50, 1.50,
                 1.45, 1.40, 1.40, 1.35, 1.30, 1.25, 1.20, 1.10, 0.95, 0.80, 0.60, 0.40]


def volume_per_bucket(network, t, scale):
    local = t + LOCAL_OFFSET
    weekend = 0.75 if local.weekday() >= 5 else 1.0
    return NETWORKS[network]["per_hour"] * DIURNAL_LOCAL[local.hour] * weekend * scale / 12


def route_history(scn):
    changes = list(ROUTE_CHANGES) + list(SCENARIOS[scn].get("route_changes", []))
    return sorted(changes, key=lambda c: (c[1], c[0]))


def weights_at(changes, network, t):
    w = None
    for c in changes:
        if c[2] == network and c[1] <= t:
            w = c[3]
    return w


def in_window(m, t):
    return m["start"] <= t < m["end"]


# ---------------------------------------------------------------------------------------------
# Message generation
# ---------------------------------------------------------------------------------------------
MSG_HEADER = ["msg_id", "submitted_at", "customer_id", "traffic_type", "sender_type", "sender_id",
              "network_group", "mcc_mnc", "dest_masked", "dest_range", "supplier_id", "route_id",
              "submit_status", "supplier_msg_id", "segments", "dlr_stat", "dlr_err", "dlr_received_at"]


def gen_day(scn, day, scale):
    """Return message rows for one UTC day (all networks), sorted by submit time."""
    cfg = SCENARIOS[scn]
    rng = random.Random(f"{SEED}-{day}")
    diff = Diffuser()
    changes = route_history(scn)
    start = datetime.fromisoformat(f"{day}T00:00:00+00:00")
    end = min(start + timedelta(days=1), H_END)
    msgs = []
    t = start
    while t < end:
        for net in NETWORKS:
            n_net = diff.take(("vol", net), volume_per_bucket(net, t, scale))
            weights = weights_at(changes, net, t)
            sups = list(weights)
            split = diff.split(("route", net, tuple(sups)), n_net, [weights[s] for s in sups])
            for sup, n in zip(sups, split):
                msgs += stream(rng, diff, cfg, net, sup, t, n, customers=None)
            b = cfg.get("burst")
            if b and b["network"] == net and in_window(b, t):
                p_norm = sum(BASE_P[(net, s)] * weights[s] for s in sups) / sum(weights.values())
                factor = (p_norm - b["target_blend"]) / (b["target_blend"] - b["p"])
                nb = diff.take(("burst", net), volume_per_bucket(net, t, scale) * factor)
                for sup, n in zip(sups, diff.split(("broute", net, tuple(sups)), nb, [weights[s] for s in sups])):
                    msgs += stream(rng, diff, cfg, net, sup, t, n, customers=b["customer"], burst=b)
        t += BUCKET
    msgs.sort(key=lambda m: m[0])
    rows = []
    for i, m in enumerate(msgs, 1):
        sub, cust, net, sup, stat, err, dlr_at, seg = m
        c = CUST[cust]
        area = rng.choice(NETWORKS[net]["areas"])
        digits = f"{area}{rng.randrange(10**8):08d}"
        rows.append([
            f"ORX-{day.replace('-', '')}-{i:07d}", iso(sub, ms=True), cust, c[2], c[3], c[4],
            net, NETWORKS[net]["mcc_mnc"], f"52{digits[:6]}XXXX", f"52{digits[:6]}", sup,
            f"RT-{net.split('-')[1]}-{sup.split('-')[1]}", "0x00000000",
            f"{SUPPLIERS[sup]['prefix']}{rng.getrandbits(40):010x}", seg,
            "" if stat == "NODLR" else stat, err, iso(dlr_at, ms=True)])
    return rows


def stream(rng, diff, cfg, net, sup, t, n, customers=None, burst=None):
    """Messages for one (network, supplier) in one 5-minute bucket, with exact outcome counts."""
    if n <= 0:
        return []
    p_ref = BASE_P[(net, sup)]
    p, extra_mix = p_ref, None
    if (net, sup) in cfg.get("stream_p", {}):
        p, extra_mix = cfg["stream_p"][(net, sup)]
    for d in cfg.get("degrade", []):
        if d["network"] == net and d["supplier"] == sup and in_window(d, t):
            p, extra_mix = d["p"], d["mix"]
    tag = "burst" if burst else "n"
    if burst:
        p, p_ref, extra_mix = burst["p"], burst["p"], None
    delivered = diff.take((tag, "d", net, sup), n * p)
    delivered = min(delivered, n)
    fails = n - delivered
    f_base = min(fails, diff.take((tag, "fb", net, sup), n * (1 - p_ref)))
    f_extra = fails - f_base
    if extra_mix is None:
        f_base, f_extra = fails, 0
    outcomes = [("DELIVRD", "000")] * delivered
    mix = burst["mix"] if burst else MIX_BASE
    for (stat, err, _), k in zip(mix, diff.split((tag, "mix", net, sup), f_base, [m[2] for m in mix])):
        outcomes += [(stat, err)] * k
    if f_extra:
        for (stat, err, _), k in zip(extra_mix, diff.split((tag, "xmix", net, sup, id(extra_mix)), f_extra,
                                                           [m[2] for m in extra_mix])):
            outcomes += [(stat, err)] * k
    q = cfg.get("queue")
    queued_flags = [False] * len(outcomes)
    if q and q["network"] == net and in_window(q, t):
        p_blend = 0.94
        prompt = diff.take((tag, "q", net, sup), delivered * q["target_observed"] / p_blend)
        queued_flags = [False] * min(prompt, delivered) + [True] * (delivered - min(prompt, delivered)) \
            + [False] * (len(outcomes) - delivered)
    if customers:
        custs = [customers] * n
    else:
        custs = []
        for c, k in zip(CUSTOMERS, diff.split(("cust", net, sup), n, [c[5] for c in CUSTOMERS])):
            custs += [c[0]] * k
    rng.shuffle(custs)
    out = []
    for (stat, err), queued, cust in zip(outcomes, queued_flags, custs):
        sub = t + timedelta(milliseconds=rng.randrange(300000))
        if queued:
            dlr = q["release"] + timedelta(seconds=rng.uniform(0, q["release_span_min"] * 60))
        elif stat == "DELIVRD":
            dlr = sub + timedelta(seconds=rng.uniform(2, 20))
        elif stat == "UNDELIV":
            dlr = sub + timedelta(seconds=rng.uniform(1, 8))
        elif stat == "EXPIRED":
            dlr = sub + VALIDITY + timedelta(seconds=rng.uniform(0, 60))
        else:
            dlr = None
        seg = 2 if CUST[cust][2] == "marketing" and rng.random() < 0.3 else 1
        out.append((sub, cust, net, sup, stat, err, dlr, seg))
    return out


# ---------------------------------------------------------------------------------------------
# Small record sets
# ---------------------------------------------------------------------------------------------


def write_reference(root):
    write_csv(root / "reference/networks.csv",
              ["network_group", "mcc_mnc", "network_name", "country", "number_areas"],
              [[k, v["mcc_mnc"], v["name"], v["country"], "|".join(v["areas"])] for k, v in NETWORKS.items()])
    write_csv(root / "reference/error_codes.csv",
              ["err", "name", "permanent", "sla_excluded", "meaning"], [list(e) for e in ERROR_CODES])
    write_json(root / "config/monitoring.json", {
        "metric": "delivery_rate_60m",
        "definition": "DELIVRD receipts received by evaluation time / messages submitted in [t-65m, t-5m)",
        "baseline": "same clock window on each of the previous 7 days, pooled",
        "alert_rule": "raise when observed < baseline - 15.0 pp and submitted >= 200",
        "severity": {"CRITICAL": "observed < baseline - 25 pp", "MAJOR": "otherwise"},
        "validity_period": "PT4H",
        "recovery_rule": "3 consecutive full hours with network rate >= hourly baseline - 2.0 pp and "
                         "volume >= 50% of baseline volume; each hour read at hour end + 5 min",
    })


SUPPLIER_ACCOUNTS = [  # Id, connection, smpp_system_id, noc hours, contacts (role, name, email, escalate after)
    ("SUP-NB", "direct to operator SMSCs", "orx_nb_01", "24x7",
     [("NOC L1", "Northbridge NOC", "noc@northbridge-msg.example", None),
      ("NOC L2", "NOC duty manager", "duty.mgr@northbridge-msg.example", "PT2H"),
      ("Account Manager", "Clara Hale", "c.hale@northbridge-msg.example", "PT4H")]),
    ("SUP-PR", "aggregator (via operator hub)", "orx_pr_02", "24x7",
     [("NOC L1", "Pacifica support", "support@pacificaroute.example", None),
      ("Account Manager", "Ravi Tan", "r.tan@pacificaroute.example", "PT4H")]),
    ("SUP-CB", "aggregator (route not disclosed)", "orx_cb_01", "business hours",
     [("NOC L1", "Cobalt helpdesk", "help@cobaltsms.example", None)]),
]


def write_crm(root):
    """Supplier and customer accounts as Salesforce sObjects, read by telco_platform.connectors.crm."""
    for sid, connection, system_id, hours, contacts in SUPPLIER_ACCOUNTS:
        write_json(root / f"salesforce/Account/{sid}.json", {
            "attributes": {"type": "Account"}, "Id": sid, "Name": SUPPLIERS[sid]["name"], "Type": "Supplier",
            "Connection__c": connection, "SMPP_System_Id__c": system_id, "NOC_Hours__c": hours,
            "Agreement_Id__c": SUPPLIERS[sid]["agreement"]})
        for i, (role, name, email, after) in enumerate(contacts, 1):
            cid = f"{sid}-C{i}"
            write_json(root / f"salesforce/Contact/{cid}.json", {
                "attributes": {"type": "Contact"}, "Id": cid, "AccountId": sid, "Name": name, "Email": email,
                "Role__c": role, "Escalation_Level__c": i, "Escalate_After__c": after})
    for (cid, name, ttype, stype, sender, _), am in zip(CUSTOMERS, ["l.mendez", "l.mendez", "p.salas"]):
        write_json(root / f"salesforce/Account/{cid}.json", {
            "attributes": {"type": "Account"}, "Id": cid, "Name": name, "Type": "Customer",
            "Traffic_Type__c": ttype, "Sender_Type__c": stype, "Sender_Id__c": sender, "Account_Manager__c": am})


def write_suppliers(root):
    write_crm(root)
    write_agreements(root)
    for md in sorted((root / "suppliers/documents").glob("*.md")):
        render_markdown_pdf(md.read_text(), md.with_suffix(".pdf"))
        rec_path = root / f"suppliers/agreements/{md.stem}.json"
        rec = json.loads(rec_path.read_text())
        rec["signed_pdf"] = f"suppliers/documents/{md.stem}.pdf"
        rec["clauses"] = [{"clause_id": n, "title": t}
                          for n, t in re.findall(r"^(\d+\.\d+) \*\*(.+?)\.\*\*", md.read_text(), re.M)]
        write_json(rec_path, rec)


def write_agreements(root):
    agreements = {
        "AGR-NB-SMS-2025-031": {
            "agreement_id": "AGR-NB-SMS-2025-031", "document_number": "NBM-A2P-2025-0311",
            "name": "A2P SMS Termination Agreement, Mexico", "agreement_type": "a2p_sms", "status": "active",
            "version": 1, "period_start": "2025-06-01", "period_end": None,
            "buyer_party": {"party_id": "ORX", "name": "Orion Messaging Exchange", "role": "buyer"},
            "seller_party": {"party_id": "SUP-NB", "name": "Northbridge Messaging", "role": "seller"},
            "currency": "USD", "billing_basis": "per accepted message part",
            "prices": [{"network_group": "MX-ALT", "mcc_mnc": "334990", "price": "0.0185"},
                       {"network_group": "MX-VRD", "mcc_mnc": "334991", "price": "0.0160"}],
            "sla": {"measurement_day": "UTC calendar day", "delivery_target_pct": "95.0",
                    "delivery_measure": "DELIVRD / (accepted - SLA-excluded), per network per day",
                    "excluded_errors": ["UNDELIV 001", "UNDELIV 009", "UNDELIV 011", "UNDELIV 013", "EXPIRED 006"],
                    "dlr_coverage_target_pct": "97.0", "dlr_latency": "95% within 30 s",
                    "priorities": [
                        {"priority": "P1", "condition": "raw delivery to a network < 80% for >= 30 consecutive minutes",
                         "raw_delivery_below_pct": "80", "for_minutes": 30, "response": "PT30M", "restoration": "PT4H"},
                        {"priority": "P2", "condition": "raw delivery to a network < 90% for >= 60 consecutive minutes",
                         "raw_delivery_below_pct": "90", "for_minutes": 60, "response": "PT2H", "restoration": "PT12H"}],
                    "escalation_min_samples": 10,
                    "escalation_evidence": ["at least 10 supplier message IDs with submit time and receipt",
                                            "affected MCC-MNC and destination ranges", "start time in UTC",
                                            "error code breakdown"],
                    "maintenance_notice": "P5D (business days)", "maintenance_notice_business_days": 5,
                    "maintenance_exclusion": "only the notified window and only for the stated impact",
                    "service_credit": {"pct_of_day_charges": "10", "per": "network and day below target",
                                       "claim_within": "P30D", "claim_via": "account manager"}},
            "document": "suppliers/documents/AGR-NB-SMS-2025-031.md",
        },
        "AGR-PR-SMS-2026-007": {
            "agreement_id": "AGR-PR-SMS-2026-007", "document_number": "PR-WS-0007",
            "name": "Wholesale SMS Services Agreement", "agreement_type": "a2p_sms", "status": "active",
            "version": 1, "period_start": "2026-01-15", "period_end": "2027-01-14",
            "buyer_party": {"party_id": "ORX", "name": "Orion Messaging Exchange", "role": "buyer"},
            "seller_party": {"party_id": "SUP-PR", "name": "Pacifica Route", "role": "seller"},
            "currency": "USD", "billing_basis": "per accepted message part",
            "prices": [{"network_group": "MX-ALT", "mcc_mnc": "334990", "price": "0.0172"},
                       {"network_group": "MX-VRD", "mcc_mnc": "334991", "price": "0.0151"}],
            "sla": {"measurement_day": "UTC calendar day", "delivery_target_pct": "92.0",
                    "delivery_measure": "DELIVRD / (accepted - SLA-excluded), per network per day",
                    "excluded_errors": ["UNDELIV 001", "UNDELIV 011", "UNDELIV 013", "EXPIRED 006"],
                    "priorities": [{"priority": "P1", "condition": "raw delivery < 75% for >= 60 minutes",
                                    "raw_delivery_below_pct": "75", "for_minutes": 60,
                                    "response": "PT1H", "restoration": "PT8H"}],
                    "maintenance_notice": "P3D", "service_credit": None},
            "document": "suppliers/documents/AGR-PR-SMS-2026-007.md",
        },
        "AGR-CB-SMS-2026-012": {
            "agreement_id": "AGR-CB-SMS-2026-012", "document_number": "CB-ORX-12",
            "name": "SMS Route Supply Terms (best effort)", "agreement_type": "a2p_sms", "status": "active",
            "version": 1, "period_start": "2026-09-01", "period_end": None,
            "buyer_party": {"party_id": "ORX", "name": "Orion Messaging Exchange", "role": "buyer"},
            "seller_party": {"party_id": "SUP-CB", "name": "Cobalt SMS", "role": "seller"},
            "currency": "USD", "billing_basis": "per accepted message part",
            "prices": [{"network_group": "MX-ALT", "mcc_mnc": "334990", "price": "0.0098"}],
            "sla": None,
            "document": "suppliers/documents/AGR-CB-SMS-2026-012.md",
        },
    }
    for aid, a in agreements.items():
        write_json(root / f"suppliers/agreements/{aid}.json", a)

    write_text(root / "suppliers/documents/AGR-NB-SMS-2025-031.md", """# A2P SMS Termination Agreement, Mexico (NBM-A2P-2025-0311)

Internal ID AGR-NB-SMS-2025-031, version 1. Fictional, prototype use only.
Between **Northbridge Messaging** ("Supplier") and **Orion Messaging Exchange** ("Customer"). Signed 2025-05-20.

<a id="clause-1.1"></a>
1.1 **Parties and term.** Effective 2025-06-01, evergreen, terminable on 90 days notice.

<a id="clause-2.1"></a>
2.1 **Service.** Supplier terminates application-to-person SMS submitted by Customer over SMPP to the Mexican mobile networks listed in Schedule A, using direct connections to the operators' SMSCs.

<a id="clause-3.1"></a>
3.1 **Prices and billing.** Prices per accepted message part in USD per Schedule A (Altavia Movil 334-990: 0.0185; Verdemovil 334-991: 0.0160). A message part is accepted when Supplier returns `submit_sm_resp` with status 0x00000000.

<a id="clause-4.1"></a>
4.1 **Delivery target.** For each destination network and UTC calendar day, Supplier shall achieve a delivery rate of at least **95.0%**, computed as messages with a DELIVRD receipt divided by accepted messages less SLA-excluded messages (clause 4.2).

<a id="clause-4.2"></a>
4.2 **Excluded outcomes.** Receipts UNDELIV with error 001, 009, 011 or 013, and EXPIRED with error 006, are subscriber-caused and excluded from clause 4.1, unless Customer shows they result from Supplier filtering.

<a id="clause-4.3"></a>
4.3 **Receipts.** Supplier shall return a final receipt for at least 97% of accepted messages, and 95% of DELIVRD receipts within 30 seconds of acceptance.

<a id="clause-5.1"></a>
5.1 **Incident priorities.** P1: raw delivery to a network below 80% for 30 consecutive minutes or more; response within 30 minutes, restoration within 4 hours. P2: raw delivery below 90% for 60 consecutive minutes or more; response within 2 hours, restoration within 12 hours. Response and restoration times run from Customer's escalation.

<a id="clause-5.2"></a>
5.2 **Escalation path.** Level 1 NOC (noc@northbridge-msg.example, 24x7). Level 2 NOC duty manager if not restored 2 hours after escalation. Level 3 account manager if not restored after 4 hours.

<a id="clause-5.3"></a>
5.3 **Escalation content.** An escalation shall include at least ten Supplier message IDs with submit times and receipts, the affected MCC-MNC and destination ranges, the start time in UTC and an error code breakdown. Supplier may ask for more samples but shall not delay the response for that reason.

<a id="clause-5.4"></a>
5.4 **Closure.** Supplier shall state the root cause and the restoration time when resolving an incident. An incident is closed when Customer confirms recovery.

<a id="clause-6.1"></a>
6.1 **Planned maintenance.** Supplier shall give at least 5 business days notice of planned maintenance, stating the window and the expected impact on MT, MO and receipts.

<a id="clause-6.2"></a>
6.2 **Measurement during maintenance.** A notified window is excluded from clause 4.1 only for the impact stated in the notice. Impact outside the window, or beyond the stated impact, counts.

<a id="clause-7.1"></a>
7.1 **Service credit.** If clause 4.1 is missed for a network and day, Supplier credits 10% of that day's charges for that network.

<a id="clause-7.2"></a>
7.2 **Credit claims.** Customer claims credits through the account manager within 30 days of the end of the affected month. Credits are a commercial matter and are not part of an operational escalation.
""")
    write_text(root / "suppliers/documents/AGR-PR-SMS-2026-007.md", """# Wholesale SMS Services Agreement (PR-WS-0007)

Internal ID AGR-PR-SMS-2026-007, version 1. Fictional, prototype use only.
Between **Pacifica Route** ("Provider") and **Orion Messaging Exchange** ("Client"). Signed 2026-01-10.

<a id="clause-1.1"></a>
1.1 **Term.** 2026-01-15 to 2027-01-14.

<a id="clause-2.1"></a>
2.1 **Service.** Provider delivers A2P SMS to the networks in Annex 1 through its own interconnects and partner hubs.

<a id="clause-3.1"></a>
3.1 **Prices.** Per accepted message part, USD (Altavia Movil 0.0172; Verdemovil 0.0151).

<a id="clause-4.1"></a>
4.1 **Delivery target.** At least **92.0%** delivered per network per UTC day, excluding UNDELIV 001, 011, 013 and EXPIRED 006.

<a id="clause-5.1"></a>
5.1 **Incidents.** P1 when raw delivery is below 75% for 60 minutes: response 1 hour, restoration 8 hours.

<a id="clause-6.1"></a>
6.1 **Maintenance and partner notices.** Provider gives 3 days notice of its own maintenance and forwards operator or partner notices it receives as soon as practical.

<a id="clause-7.1"></a>
7.1 **Credits.** No service credits apply.
""")
    write_text(root / "suppliers/documents/AGR-CB-SMS-2026-012.md", """# SMS Route Supply Terms (CB-ORX-12)

Internal ID AGR-CB-SMS-2026-012. Fictional, prototype use only.
Between **Cobalt SMS** and **Orion Messaging Exchange**. Accepted online 2026-08-28.

<a id="clause-1.1"></a>
1.1 **Service.** Best-effort delivery of A2P SMS. Routes may change without notice.

<a id="clause-2.1"></a>
2.1 **Prices.** Altavia Movil 0.0098 per accepted message part, USD.

<a id="clause-3.1"></a>
3.1 **No service levels.** No delivery, receipt or response targets apply. Support by email, business hours.
""")


NOTICES = {
    "MN-NB-2026-0922": {
        "notice_id": "MN-NB-2026-0922", "from_party": "SUP-NB", "notice_type": "planned_maintenance",
        "sent_at": "2026-09-15T16:00:00Z", "received_at": "2026-09-15T16:00:40Z", "channel": "email",
        "subject": "[Northbridge NOC] Planned maintenance 2026-09-22 14:00-16:00 UTC: SMSC software upgrade",
        "window_start": "2026-09-22T14:00:00Z", "window_end": "2026-09-22T16:00:00Z",
        "scope": "SMSC software upgrade on Mexico interconnect nodes", "networks": ["334990", "334991"],
        "impact": {"MT": "No impact expected", "MO": "No impact expected", "DR": "No impact expected"},
        "reference": "NB-CHG-88412",
    },
    "MN-PR-2026-0910": {
        "notice_id": "MN-PR-2026-0910", "from_party": "SUP-PR", "notice_type": "partner_notice",
        "sent_at": "2026-09-08T11:00:00Z", "received_at": "2026-09-08T11:02:10Z", "channel": "email",
        "subject": "[Pacifica] Operator maintenance Verdemovil 2026-09-10 06:00-08:00 UTC",
        "window_start": "2026-09-10T06:00:00Z", "window_end": "2026-09-10T08:00:00Z",
        "scope": "Verdemovil SMSC maintenance (forwarded operator notice)", "networks": ["334991"],
        "impact": {"MT": "Messages will queue at operator end", "MO": "No impact", "DR": "Delayed"},
        "reference": "PR-OPN-1180",
    },
    "MN-PR-2026-0922": {
        "notice_id": "MN-PR-2026-0922", "from_party": "SUP-PR", "notice_type": "partner_notice",
        "sent_at": "2026-09-22T15:30:00Z", "received_at": "2026-09-22T15:31:15Z", "channel": "email",
        "subject": "[Pacifica] URGENT operator maintenance Altavia Movil 2026-09-22 16:00-18:00 UTC",
        "window_start": "2026-09-22T16:00:00Z", "window_end": "2026-09-22T18:00:00Z",
        "scope": "Altavia Movil emergency SMSC maintenance (forwarded operator notice)", "networks": ["334990"],
        "impact": {"MT": "Messages will queue at operator end and deliver after the window",
                   "MO": "No impact", "DR": "Delayed until queued messages deliver"},
        "reference": "PR-OPN-1207",
    },
}


def write_notices(root, scn):
    ids = ["MN-PR-2026-0910", "MN-NB-2026-0922"] + SCENARIOS[scn].get("notices_extra", [])
    for nid in ids:
        n = NOTICES[nid]
        write_json(root / f"notices/{nid}.json", n)
        imp = n["impact"]
        write_text(root / f"notices/{nid}.eml.md", f"""From: {'noc@northbridge-msg.example' if n['from_party'] == 'SUP-NB' else 'support@pacificaroute.example'}
To: noc@orion-msg.example
Date: {n['sent_at']}
Subject: {n['subject']}

Reference: {n['reference']}
Type: {n['notice_type'].replace('_', ' ')}
Window: {n['window_start']} to {n['window_end']}
Scope: {n['scope']}
Networks (MCC-MNC): {', '.join(n['networks'])}

Expected impact
- MT: {imp['MT']}
- MO: {imp['MO']}
- DR: {imp['DR']}

Activities may take place at any time within the window.
""")


def write_routes(root, scn):
    rows, prev = [], {}
    for cid, at, net, w, by, kind, reason, ticket in route_history(scn):
        fmt = lambda d: "|".join(f"{k}:{v}" for k, v in d.items()) if d else ""
        rows.append([cid, iso(at), net, fmt(w), fmt(prev.get(net)), by, kind, reason, ticket])
        prev[net] = w
    write_csv(root / "routing/route_history.csv",
              ["change_id", "effective_at", "network_group", "weights", "previous_weights", "changed_by",
               "actor_kind", "reason", "change_ticket"], rows)


def write_tickets(root, scn):
    if scn == "v1":
        return
    write_json(root / "tickets/customer/SF-00012871.json", {
        "Id": "500Dn00000SIM12871", "CaseNumber": "SF-00012871", "AccountId": "CUST-001",
        "Account": {"Name": "Banco Litoral"}, "ContactEmail": "ops.digital@bancolitoral.example",
        "Subject": "OTP SMS not arriving for Altavia subscribers",
        "Description": "Since about 10:00 local our customers on Altavia are reporting one-time passwords "
                       "arriving late or not at all. Login completion dropped. Other networks look fine. "
                       "Example numbers end 4471, 0918, 2265.",
        "Priority": "High", "Status": "New", "Origin": "Email", "Type": "Problem",
        "CreatedDate": "2026-09-22T16:40:00Z", "OwnerId": "support-queue-l1",
    })


def write_active_tests(root, scn, scale):
    cfg = SCENARIOS[scn]
    changes = route_history(scn)
    rng = random.Random(f"{SEED}-tests-{scn}")
    rows, k = [], 0
    t = H_START + timedelta(minutes=30)
    while t < H_END:
        for net in NETWORKS:
            for sup, w in weights_at(changes, net, t).items():
                if not w:
                    continue
                k += 1
                sent = t + timedelta(seconds=list(SUPPLIERS).index(sup) * 20)
                stat, err = "DELIVRD", "000"
                dlr = sent + timedelta(seconds=4)
                handset = sent + timedelta(seconds=6)
                for d in cfg.get("degrade", []):
                    if d["network"] == net and d["supplier"] == sup and in_window(d, sent):
                        stat, err, dlr, handset = "UNDELIV", "022", sent + timedelta(seconds=3), None
                if (net, sup) in cfg.get("stream_p", {}):
                    stat, err, dlr, handset = "UNDELIV", "011", sent + timedelta(seconds=2), None
                q = cfg.get("queue")
                if q and q["network"] == net and in_window(q, sent):
                    dlr = q["release"] + timedelta(seconds=rng.uniform(0, 600))
                    handset = dlr + timedelta(seconds=2)
                rows.append([f"TST-{k:06d}", iso(sent), sup, net, NETWORKS[net]["mcc_mnc"],
                             f"52{NETWORKS[net]['areas'][0]}XXXX{9000 + list(SUPPLIERS).index(sup):04d}",
                             stat, err, iso(dlr), iso(handset)])
        t += timedelta(hours=1)
    write_csv(root / "tests/active_tests.csv",
              ["test_id", "sent_at", "supplier_id", "network_group", "mcc_mnc", "test_number_masked",
               "dlr_stat", "dlr_err", "dlr_received_at", "handset_received_at"], rows)


def write_empty_stores(root):
    for d in ["cases", "ledger", "supplier-portal/submissions", "supplier-portal/responses",
              "tickets/internal", "outbox"]:
        (root / d).mkdir(parents=True, exist_ok=True)
        (root / d / ".keep").write_text("")


# ---------------------------------------------------------------------------------------------
# Metrics used for the alert record and the answer keys (verify_dataset.py re-derives them)
# ---------------------------------------------------------------------------------------------


def load_messages(root_paths):
    rows = []
    for p in root_paths:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                r["_sub"] = datetime.fromisoformat(r["submitted_at"].replace("Z", "+00:00"))
                r["_dlr"] = (datetime.fromisoformat(r["dlr_received_at"].replace("Z", "+00:00"))
                             if r["dlr_received_at"] else None)
                r["_seg"] = int(r["segments"])
                rows.append(r)
    rows.sort(key=lambda r: r["_sub"])
    return Messages(rows)


class Messages(list):
    """Message rows sorted by submit time, with a parallel index for window lookups."""

    def __init__(self, rows):
        super().__init__(rows)
        self.subs = [r["_sub"] for r in rows]


def window_stats(rows, t0, t1, as_of, **flt):
    sub = dlv = cov = 0
    errs = {}
    for r in rows[bisect.bisect_left(rows.subs, t0):bisect.bisect_left(rows.subs, t1)]:
        if any(r[k] != v if not k.endswith("__ne") else r[k[:-4]] == v for k, v in flt.items()):
            continue
        sub += 1
        if r["_dlr"] is not None and r["_dlr"] <= as_of:
            cov += 1
            if r["dlr_stat"] == "DELIVRD":
                dlv += 1
            else:
                key = f"{r['dlr_stat']} {r['dlr_err']}"
                errs[key] = errs.get(key, 0) + 1
    return {"submitted": sub, "delivered": dlv, "with_receipt": cov,
            "delivery_pct": pct(dlv, sub), "receipt_coverage_pct": pct(cov, sub),
            "errors": dict(sorted(errs.items(), key=lambda kv: -kv[1]))}


def pct(a, b):
    return str(Decimal(100 * a / b).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) if b else None


def baseline(rows, t0, t1, lag, **flt):
    sub = dlv = 0
    for d in range(1, 8):
        s = window_stats(rows, t0 - timedelta(days=d), t1 - timedelta(days=d), t1 - timedelta(days=d) + lag, **flt)
        sub += s["submitted"]
        dlv += s["delivered"]
    return {"submitted_avg": round(sub / 7), "delivery_pct": pct(dlv, sub)}


def build_alarm(rows):
    t0, t1 = ALERT_WINDOW
    s = window_stats(rows, t0, t1, ALERT_AT, network_group="MX-ALT")
    b = baseline(rows, t0, t1, ALERT_AT - t1, network_group="MX-ALT")
    nb = window_stats(rows, t0, t1, ALERT_AT, network_group="MX-ALT", supplier_id="SUP-NB")
    share = round(100 * nb["submitted"] / s["submitted"]) if s["submitted"] else 0
    obs, base = Decimal(s["delivery_pct"]), Decimal(b["delivery_pct"])
    return {
        "externalAlarmId": "ALM-2026-0922-0042", "sourceSystemId": "orx-monitor-01",
        "alarmType": "QualityOfServiceAlarm",
        "perceivedSeverity": "CRITICAL" if obs < base - 25 else "MAJOR",
        "probableCause": "thresholdCrossed", "specificProblem": "SMS delivery rate below baseline",
        "alarmedObject": {"id": "MX-ALT", "mcc_mnc": "334990", "name": "Mexico / Altavia Movil"},
        "alarmRaisedTime": iso(ALERT_AT), "alarmReportingTime": iso(ALERT_AT + timedelta(seconds=20)),
        "serviceAffecting": True, "state": "raised",
        "crossedThresholdInformation": {
            "direction": "DOWN", "indicatorName": "delivery_rate_60m", "indicatorUnit": "%",
            "observedValue": s["delivery_pct"], "baselineValue": b["delivery_pct"],
            "thresholdValue": str(base - 15), "window_start": iso(t0), "window_end": iso(t1),
            "submitted": s["submitted"]},
        "comment": f"Top contributing route: RT-ALT-NB ({share}% of volume). Suspected supplier degradation.",
    }


def answer_key(scn, rows, alarm):
    cfg = SCENARIOS[scn]
    t0, t1 = ALERT_WINDOW
    lag = ALERT_AT - t1
    alt = {"network_group": "MX-ALT"}
    sup_ids = sorted({r["supplier_id"] for r in rows if r["network_group"] == "MX-ALT" and t0 <= r["_sub"] < t1})
    key = {
        "scenario": scn, "title": cfg["title"],
        "alarm": {"observed_pct": alarm["crossedThresholdInformation"]["observedValue"],
                  "baseline_pct": alarm["crossedThresholdInformation"]["baselineValue"],
                  "severity": alarm["perceivedSeverity"]},
        "alert_window": {
            "window": [iso(t0), iso(t1)], "as_of": iso(ALERT_AT),
            "network": window_stats(rows, t0, t1, ALERT_AT, **alt),
            "by_supplier": {s: {**window_stats(rows, t0, t1, ALERT_AT, network_group="MX-ALT", supplier_id=s),
                                "baseline_pct": baseline(rows, t0, t1, lag, network_group="MX-ALT",
                                                         supplier_id=s)["delivery_pct"]} for s in sup_ids},
            "by_customer": {c[0]: window_stats(rows, t0, t1, ALERT_AT, network_group="MX-ALT", customer_id=c[0])
                            for c in CUSTOMERS},
            "excluding_CUST-003": window_stats(rows, t0, t1, ALERT_AT, network_group="MX-ALT",
                                               customer_id__ne="CUST-003"),
            "control_network_MX-VRD": window_stats(rows, t0, t1, ALERT_AT, network_group="MX-VRD"),
        },
    }
    for k in ("errors",):
        key["alert_window"]["network"].pop(k)
    key["alert_window"]["errors"] = window_stats(rows, t0, t1, ALERT_AT, **alt)["errors"]

    # Hourly view of the incident day plus recovery check, each hour read at hour end + 5 min.
    hours, verified_at, run = [], None, 0
    start_check = cfg["fix_or_end"].replace(minute=0) + (timedelta(hours=1) if cfg["fix_or_end"].minute else timedelta())
    h = T("12:00")
    while h + timedelta(hours=1) <= H_END - timedelta(hours=1):
        s = window_stats(rows, h, h + timedelta(hours=1), h + timedelta(hours=1, minutes=5), **alt)
        b = baseline(rows, h, h + timedelta(hours=1), timedelta(minutes=5), **alt)
        ok = (Decimal(s["delivery_pct"]) >= Decimal(b["delivery_pct"]) - 2
              and s["submitted"] >= 0.5 * b["submitted_avg"])
        hours.append({"hour": iso(h), "submitted": s["submitted"], "delivery_pct": s["delivery_pct"],
                      "baseline_pct": b["delivery_pct"], "baseline_submitted_avg": b["submitted_avg"],
                      "meets_recovery_rule": ok})
        if h >= start_check and verified_at is None:
            run = run + 1 if ok else 0
            if run == 3:
                verified_at = iso(h + timedelta(hours=1, minutes=5))
        h += timedelta(hours=1)
    key["hourly_MX-ALT"] = hours
    key["recovery"] = {"check_from": iso(start_check), "verified_at": verified_at}

    # SLA view for the incident day, final receipts.
    day0, day1 = T("00:00"), T("00:00") + timedelta(days=1)
    sla = {}
    for s in sorted({r["supplier_id"] for r in rows if r["network_group"] == "MX-ALT" and day0 <= r["_sub"] < day1}):
        acc = dlv = exc = parts = 0
        for r in rows[bisect.bisect_left(rows.subs, day0):bisect.bisect_left(rows.subs, day1)]:
            if r["network_group"] != "MX-ALT" or r["supplier_id"] != s:
                continue
            acc += 1
            parts += r["_seg"]
            if r["dlr_stat"] == "DELIVRD":
                dlv += 1
            elif (r["dlr_stat"] == "UNDELIV" and r["dlr_err"] in SLA_EXCLUDED_UNDELIV) or \
                    (r["dlr_stat"] == "EXPIRED" and r["dlr_err"] == "006"):
                exc += 1
        agr = SUPPLIERS[s]["agreement"]
        target = {"SUP-NB": "95.0", "SUP-PR": "92.0"}.get(s)
        price = {"SUP-NB": "0.0185", "SUP-PR": "0.0172", "SUP-CB": "0.0098"}[s]
        rate = pct(dlv, acc - exc)
        entry = {"agreement": agr, "accepted": acc, "message_parts": parts, "delivered": dlv, "sla_excluded": exc,
                 "raw_delivery_pct": pct(dlv, acc), "sla_delivery_pct": rate, "target_pct": target,
                 "day_charges": q2(Decimal(price) * parts)}
        entry["breach"] = bool(target) and Decimal(rate) < Decimal(target)
        if s == "SUP-NB" and entry["breach"]:
            entry["service_credit"] = q2(Decimal(entry["day_charges"]) * Decimal("0.10"))
        sla[s] = entry
    key["sla_2026-09-22_MX-ALT"] = sla

    # P1 condition for NB (clause 5.1): raw delivery below 80% in six consecutive 5-minute buckets.
    p1, run = None, 0
    t = T("12:00")
    while t < T("23:00"):
        s = window_stats(rows, t, t + BUCKET, t + BUCKET + timedelta(minutes=5),
                         network_group="MX-ALT", supplier_id="SUP-NB")
        run = run + 1 if s["submitted"] and Decimal(s["delivery_pct"]) < 80 else 0
        if run == 6:
            p1 = iso(t + BUCKET)
            break
        t += BUCKET
    key["nb_raw_p1_condition_met_at"] = p1
    key["nb_raw_p1_note"] = ("Clause 5.1 is on raw delivery. It can be met when Northbridge is not at fault "
                             "(bad list, queued receipts); ownership decides whether to escalate, not this flag.")
    key["truth"] = TRUTH[scn]
    return key


TRUTH = {
    "base": {
        "probable_cause": "Northbridge's connection to Altavia degraded right after its 14:00-16:00 SMSC upgrade",
        "owner": "SUP-NB", "owner_kind": "supplier",
        "evidence_pattern": ["only RT-ALT-NB drops (Pacifica on Altavia stays at baseline)",
                             "Northbridge on Verdemovil stays at baseline, so not Northbridge-wide",
                             "failures are network errors (UNDELIV 022, EXPIRED 000), not subscriber errors",
                             "active tests via Northbridge to Altavia fail from 16:30, via Pacifica succeed",
                             "start 16:05 is just after the notified window ending 16:00, whose notice said no impact"],
        "contradictions": ["MN-NB-2026-0922 says no impact expected, data shows impact starting 5 minutes after the window"],
        "hypotheses": {"supplier_nb_connection": "supported", "operator_altavia": "refuted",
                       "customer_traffic": "refuted", "own_route_change": "refuted",
                       "nb_planned_maintenance_in_window": "refuted (impact starts after the window)"},
        "containment_option": "Shift Altavia weight to Pacifica; the analyst decides, the system never changes routes",
        "actions": [
            {"at": "2026-09-22T17:10:00Z", "step": 4, "action": "recommend_escalation", "to": "SUP-NB",
             "priority": "P1", "level": 1},
            {"at": "2026-09-22T17:25:00Z", "step": 5, "action": "analyst_revises",
             "revision": "remove the service credit request from the operational escalation (clause 7.2)"},
            {"at": "2026-09-22T17:25:00Z", "step": 6, "action": "send_escalation", "to": "SUP-NB"},
            {"at": "2026-09-22T19:40:00Z", "step": 7, "action": "evaluate_supplier_response",
             "verdict": "root cause and fix time stated (clause 5.4); start recovery check"},
            {"step": 7, "action": "verify_recovery_and_close", "at": "recovery.verified_at"}],
        "escalation_must_include": ["10+ supplier message IDs from SUP-NB with UNDELIV 022 or EXPIRED 000",
                                    "MCC-MNC 334990 and destination ranges", "start 2026-09-22T16:05Z",
                                    "error breakdown", "Pacifica comparison", "link to MN-NB-2026-0922"],
        "escalation_must_not_include": ["service credit claim (removed by analyst)"],
    },
    "v1": {
        "probable_cause": "A burst from Mercado Sol (CUST-003) to a poor-quality number list; routes are healthy",
        "owner": "CUST-003", "owner_kind": "customer",
        "evidence_pattern": ["volume on Altavia up about 49% from 16:00", "extra traffic is all CUST-003",
                             "failures are UNDELIV 001 and 011 (subscriber errors)",
                             "both suppliers drop by the same amount", "excluding CUST-003 delivery is at baseline",
                             "active tests on both routes succeed"],
        "contradictions": ["alert comment suspects supplier degradation on RT-ALT-NB"],
        "hypotheses": {"supplier_nb_connection": "refuted", "operator_altavia": "refuted",
                       "customer_traffic": "supported", "own_route_change": "refuted"},
        "actions": [
            {"at": "2026-09-22T17:10:00Z", "step": 4, "action": "recommend_no_supplier_escalation"},
            {"at": "2026-09-22T17:25:00Z", "step": 5, "action": "analyst_approves",
             "follow_up": "internal note to CUST-003's account manager (p.salas) about list quality"},
            {"step": 7, "action": "close", "reason": "no network or supplier fault; SLA not affected (errors excluded)"}],
        "escalation_must_include": None,
    },
    "v2": {
        "probable_cause": "Same Northbridge connection fault as base; the first supplier answer is wrong",
        "owner": "SUP-NB", "owner_kind": "supplier",
        "evidence_pattern": ["as base", "after the 18:10 'no fault found' reply, RT-ALT-NB is still at about 62%",
                             "active tests via Northbridge still fail after 18:10"],
        "contradictions": ["supplier response at 18:10 says resolved with no fault found, data shows ongoing failure"],
        "hypotheses": {"supplier_nb_connection": "supported", "operator_altavia": "refuted",
                       "customer_traffic": "refuted", "own_route_change": "refuted"},
        "actions": [
            {"at": "2026-09-22T17:25:00Z", "step": 6, "action": "send_escalation", "to": "SUP-NB", "level": 1},
            {"at": "2026-09-22T18:10:00Z", "step": 7, "action": "reject_supplier_closure",
             "why": "no root cause or fix time (clause 5.4), recovery not verified"},
            {"at": "2026-09-22T18:25:00Z", "step": 6, "action": "send_follow_up", "to": "SUP-NB",
             "with": "fresh samples after 18:10 and Pacifica comparison"},
            {"at": "2026-09-22T19:25:00Z", "step": 6, "action": "escalate_level_2", "to": "duty.mgr@northbridge-msg.example",
             "why": "not restored 2 hours after escalation (clause 5.2)"},
            {"at": "2026-09-22T20:50:00Z", "step": 7, "action": "evaluate_supplier_response",
             "verdict": "root cause and fix time stated; start recovery check"},
            {"step": 7, "action": "verify_recovery_and_close", "at": "recovery.verified_at"}],
        "escalation_must_include": ["as base"],
    },
    "v3": {
        "probable_cause": "Altavia (operator) emergency SMSC maintenance 16:00-18:00; messages queue and deliver late",
        "owner": "MX-ALT operator", "owner_kind": "operator",
        "evidence_pattern": ["both suppliers drop by the same amount", "Verdemovil unaffected",
                             "receipt coverage low (many messages have no receipt yet), failure errors not elevated",
                             "Pacifica forwarded an operator notice at 15:30 for 16:00-18:00, Northbridge did not",
                             "active tests during the window show no receipt yet"],
        "contradictions": ["alert comment suspects supplier degradation on RT-ALT-NB"],
        "hypotheses": {"supplier_nb_connection": "refuted", "operator_altavia": "supported (planned, queued)",
                       "customer_traffic": "refuted", "own_route_change": "refuted"},
        "actions": [
            {"at": "2026-09-22T17:10:00Z", "step": 4, "action": "recommend_wait_and_recheck",
             "recheck_not_before": "2026-09-22T18:00:00Z", "recheck_by": "2026-09-22T18:45:00Z"},
            {"step": 7, "action": "recheck", "expect": "queued messages delivered between 18:00 and 18:25; "
                                                     "alert window final delivery back near 94%"},
            {"step": 7, "action": "verify_recovery_and_close", "at": "recovery.verified_at",
             "note": "no supplier escalation; optionally ask Northbridge why the operator notice was not forwarded"}],
        "escalation_must_include": None,
    },
    "v4": {
        "probable_cause": "Our own route change RC-2026-0922-01 moved 70% of Altavia traffic to Cobalt, "
                          "whose route is filtered by the operator",
        "owner": "ORX routing", "owner_kind": "internal",
        "evidence_pattern": ["route change at 16:00 by j.ortega (NETOPS-2231)", "Cobalt on Altavia at about 57%",
                             "Cobalt failures are UNDELIV 011 and 009 (filtering signature on one route)",
                             "Northbridge share still at baseline", "Cobalt has no SLA (best effort)"],
        "contradictions": [],
        "hypotheses": {"supplier_nb_connection": "refuted", "operator_altavia": "refuted",
                       "customer_traffic": "refuted", "own_route_change": "supported"},
        "actions": [
            {"at": "2026-09-22T17:10:00Z", "step": 4, "action": "recommend_internal_containment",
             "what": "routing team reverts RC-2026-0922-01 (a human makes the change)"},
            {"at": "2026-09-22T17:25:00Z", "step": 5, "action": "analyst_approves"},
            {"step": 7, "action": "verify_recovery_and_close", "at": "recovery.verified_at",
             "note": "revert RC-2026-0922-02 appears at 17:45; optional quality report to Cobalt"}],
        "escalation_must_include": None,
    },
}


def scenario_yaml(scn):
    head = f"""id: {scn}
title: "{SCENARIOS[scn]['title']}"
sim_start: 2026-09-22T17:10:00Z
data_root: {scn}            # relative to the generated sim-data folder; variants resolve through overlay.json
clock: advances only while the case waits (supplier, recheck, recovery window)
analyst:
  latency: PT15M            # each analyst decision takes 15 sim minutes
events:
  - at: 2026-09-22T17:10:00Z
    action: open_case_from_alarm
    alarm_id: ALM-2026-0922-0042
"""
    ack = """  - on: escalation_submitted
    to: SUP-NB
    after: PT15M
    action: supplier_response
    response: {status: acknowledged, ticket_id: NB-T-551203, message: "Ticket opened, investigating."}
"""
    if scn == "base":
        return head + ack + """  - on: escalation_submitted
    to: SUP-NB
    at: 2026-09-22T19:40:00Z      # delivered at this time, or on submission if later
    action: supplier_response
    response:
      status: resolved
      root_cause: "After the 14:00-16:00 upgrade, one of two binds to the Altavia SMSC came back with a wrong throughput profile, so part of the traffic was rejected by the operator."
      fix_time: 2026-09-22T19:30:00Z
      message: "Bind re-provisioned at 19:30 UTC. Please confirm recovery."
analyst_script:
  - at_step: 5
    decision: revise
    edits: {remove_section: service_credit}
    reason: "Credits are claimed through the account manager (clause 7.2), keep the NOC escalation operational."
"""
    if scn == "v2":
        return head + ack + """  - on: escalation_submitted
    to: SUP-NB
    at: 2026-09-22T18:10:00Z
    action: supplier_response
    response: {status: resolved, root_cause: null, fix_time: null,
               message: "We checked our platform, all binds are up and traffic looks normal on our side. No fault found, closing the ticket. Please check your side."}
  - on: follow_up_submitted
    to: SUP-NB
    at: 2026-09-22T20:50:00Z
    action: supplier_response
    response:
      status: resolved
      root_cause: "Bind 2 to the Altavia SMSC came back after the upgrade with a wrong throughput profile; first-line check only looked at bind state."
      fix_time: 2026-09-22T20:40:00Z
      message: "Fixed 20:40 UTC after review of your samples. Apologies for the earlier closure."
analyst_script:
  - at_step: 5
    decision: approve
"""
    if scn == "v4":
        return head + """  # RC-2026-0922-02 (revert) is already in routing/route_history.csv at 17:45 and becomes visible then.
analyst_script:
  - at_step: 5
    decision: approve
"""
    return head + """analyst_script:
  - at_step: 5
    decision: approve
"""


# ---------------------------------------------------------------------------------------------


def days_between(a, b):
    d = a.date()
    while datetime(d.year, d.month, d.day, tzinfo=UTC) < b:
        yield d.isoformat()
        d += timedelta(days=1)


def build_root(root, scn, scale, days):
    write_reference(root)
    write_suppliers(root)
    write_notices(root, scn)
    write_routes(root, scn)
    write_tickets(root, scn)
    write_active_tests(root, scn, scale)
    write_empty_stores(root)
    for day in days:
        write_csv_gz(root / f"messages/{day}.csv.gz", MSG_HEADER, gen_day(scn, day, scale))
    write_text(root / "scenario.yaml", scenario_yaml(scn))


def message_files(out, scn):
    """Message day files for a scenario, resolved through its overlay."""
    root = DataRoot(out / scn)
    return [root.require(rel) for rel in root.list("messages") if rel.endswith(".csv.gz")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "sim-data"))
    ap.add_argument("--scale", type=float, default=1.0, help="message volume multiplier (0.1 for fast tests)")
    ap.add_argument("--scenarios", default="base,v1,v2,v3,v4")
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    all_days = list(days_between(H_START, H_END))
    wanted = args.scenarios.split(",")

    build_root(out / "base", "base", args.scale, all_days)
    for scn in [s for s in wanted if s != "base"]:
        with tempfile.TemporaryDirectory() as tmp:
            full = Path(tmp) / scn
            build_root(full, scn, args.scale, [INCIDENT_DAY])
            var = out / scn
            base_files = {p.relative_to(out / "base").as_posix() for p in (out / "base").rglob("*") if p.is_file()}
            var_files = {p.relative_to(full).as_posix() for p in full.rglob("*") if p.is_file()}
            for rel in sorted(var_files):
                src = full / rel
                if rel in base_files and (out / "base" / rel).read_bytes() == src.read_bytes():
                    continue
                (var / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, var / rel)
            deleted = sorted(f for f in base_files - var_files if not f.startswith("messages/"))
            write_json(var / "overlay.json", {"inherits": "base", "deleted": deleted})

    keys = out / "answer-keys"
    for scn in wanted:
        rows = load_messages(message_files(out, scn))
        alarm = build_alarm(rows)
        root = out / scn
        alarms_path = root / "alerts/alarms.jsonl"
        write_text(alarms_path, json.dumps(alarm) + "\n")
        if scn != "base" and (out / "base/alerts/alarms.jsonl").read_bytes() == alarms_path.read_bytes():
            alarms_path.unlink()
            alarms_path.parent.rmdir()
        write_json(keys / f"{scn}.json", answer_key(scn, rows, alarm))
        k = json.loads((keys / f"{scn}.json").read_text())
        print(f"{scn:5} msgs={len(rows):7d} alarm={alarm['crossedThresholdInformation']['observedValue']}% "
              f"baseline={alarm['crossedThresholdInformation']['baselineValue']}% "
              f"recovered={k['recovery']['verified_at']}")
    write_json(keys / "_meta.json", {"scale": args.scale, "seed": SEED, "scenarios": wanted})


if __name__ == "__main__":
    main()

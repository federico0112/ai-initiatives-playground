#!/usr/bin/env python3
"""Generate the simulated records for the 8% wholesale invoice case.

Matches ../simulated-systems.md (schemas in section 4, seeded scenario in section 6,
layout in section 9). Stdlib only and deterministic: re-running rewrites identical files.
All companies, people, trunks and numbers are fictional.

Outputs (relative to this folder):
  sim-data/base/          full data root for the base scenario
  sim-data/v1 .. v6/      overlays: only the files that differ from base, plus
                          overlay.json (inherits + deleted paths) and scenario.yaml
  answer-keys/<id>.json   ground truth per scenario (kept outside the data roots so
                          adapters and agents never see it)
"""
import csv
import gzip
import io
import json
import math
import os
import random
import shutil
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

SEED = 20261005
HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, "sim-data")
KEYS = os.path.join(HERE, "answer-keys")
UTC = timezone.utc

# Confirmed by Federico on 2026-10-02.
CASE_OPEN_PCT = Decimal("2")          # |invoiced - expected| / invoiced
EVIDENCE_REQUEST_PCT = Decimal("1")   # unresolved / invoiced

CARRIER = "NBCS"
BUYER = {"party_id": "ORX", "name": "Orion Voice Exchange", "role": "buyer"}
SELLER = {"party_id": "NBCS", "name": "Northbridge Carrier Services", "role": "seller"}
AGR = "AGR-NB-2025-014"
AGR_DOC_NO = "NBCS-WVT-2025-0117"
ACCOUNT = "NBCS-ACC-30988"
INV = "NB-INV-2026-09-0412"
DECK_FULL = "NB-VOICE-2026-08-01"
DECK_PART = "NB-VOICE-2026-09-07"
NOTICE = "RN-NB-2026-0907"
RUN = "RUN-2026-09"
P_START, P_END = date(2026, 9, 1), date(2026, 9, 30)
DAYS = [P_START + timedelta(d) for d in range(30)]

OLD = {"MX-MOB": "0.0120", "MX-FIX": "0.0060", "CO-MOB": "0.0150", "BR-MOB": "0.0210", "GT-MOB": "0.0850"}
MX_NEW = Decimal("0.0185")
GROUPS = [  # group, label, dial codes, main egress trunk(s)
    ("MX-MOB", "Mexico Mobile", ["521"], ["TRK-NB-01", "TRK-NB-02"]),
    ("MX-FIX", "Mexico Fixed", ["52"], ["TRK-NB-06"]),
    ("CO-MOB", "Colombia Mobile", ["573"], ["TRK-NB-03"]),
    ("BR-MOB", "Brazil Mobile", ["55119", "55219"], ["TRK-NB-04"]),
    ("GT-MOB", "Guatemala Mobile", ["5025"], ["TRK-NB-05"]),
]
GL = {g[0]: g for g in GROUPS}
# Billable minutes per (group, sub-period); every total is from spec section 6.
TARGETS = {("MX-MOB", date(2026, 9, 1), date(2026, 9, 14)): 280000,
           ("MX-MOB", date(2026, 9, 15), date(2026, 9, 30)): 320000,
           ("MX-FIX", P_START, P_END): 400000,
           ("CO-MOB", P_START, P_END): 300000,
           ("BR-MOB", P_START, P_END): 250000,
           ("GT-MOB", P_START, P_END): 42000}
REJ_CO_CALLS, REJ_CO_MIN = 4000, 12000   # UNMAPPED_TRUNK on TRK-NB-07, 09-21..09-30
REJ_DAYS = [date(2026, 9, d) for d in range(21, 31)]


def q2(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def write_text(path, text):
    ensure(path)
    with open(path, "w", newline="") as f:
        f.write(text)


def csv_text(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def write_csv(path, header, rows):
    write_text(path, csv_text(header, rows))


def write_csv_gz(path, header, rows):
    ensure(path)
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(csv_text(header, rows).encode())


def write_json(path, obj):
    write_text(path, json.dumps(obj, indent=2) + "\n")


def split_total(rng, total, n, unit, weights=None):
    """Split total into n parts, each a positive multiple of unit, summing exactly."""
    weights = weights or [rng.uniform(0.85, 1.15) for _ in range(n)]
    s = sum(weights)
    raw = [total * w / s for w in weights]
    parts = [int(r // unit) * unit for r in raw]
    rem = total - sum(parts)
    order = sorted(range(n), key=lambda i: raw[i] - parts[i], reverse=True)
    i = 0
    while rem > 0:
        parts[order[i % n]] += unit
        rem -= unit
        i += 1
    assert sum(parts) == total and min(parts) > 0
    return parts


def call_minutes(rng, minutes, calls):
    """`calls` positive ints summing to `minutes` (long-tailed, mean minutes/calls)."""
    w = [rng.expovariate(1.0) ** 1.6 for _ in range(calls)]
    extra = rng.choices(range(calls), weights=w, k=minutes - calls)
    out = [1] * calls
    for i in extra:
        out[i] += 1
    return out


def masked(num):
    return num[:-4] + "XXXX"


def rand_b(rng, code):
    n = code + "".join(str(rng.randint(0, 9)) for _ in range(12 - len(code)))
    if code == "52" and n.startswith("521"):          # keep MX-FIX off the mobile prefix
        n = "525" + n[3:]
    return n


def build_base(rng):
    """Return all in-memory records for the base scenario."""
    cdrs = {d: [] for d in DAYS}            # date -> list of row lists
    daily = {}                               # (date, group, code) -> [calls, dur, mins]
    for (g, a, b), total in TARGETS.items():
        days = [d for d in DAYS if a <= d <= b]
        # MX-MOB is flat (20,000 min/day) so notice-date variants move round amounts.
        wts = [1.0] * len(days) if g == "MX-MOB" else \
            [rng.uniform(0.9, 1.1) * (0.8 if d.weekday() >= 5 else 1.0) for d in days]
        per_day = split_total(rng, total, len(days), 20, wts)
        codes = GL[g][2]
        for d, m in zip(days, per_day):
            code_parts = [m] if len(codes) == 1 else split_total(rng, m, len(codes), 20, [0.6, 0.4])
            for code, cm in zip(codes, code_parts):
                n = cm // 3
                daily[(d, g, code)] = [n, 0, cm]
                for mins in call_minutes(rng, cm, n):
                    dur = (mins - 1) * 60 + rng.randint(1, 60)
                    daily[(d, g, code)][1] += dur
                    cdrs[d].append([g, code, mins, dur])
    # Materialize CDR rows per day, sorted by start time.
    rows_by_day, call_index = {}, []
    for d in DAYS:
        recs = []
        for g, code, mins, dur in cdrs[d]:
            start = datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(seconds=rng.randrange(86400))
            trunk = rng.choice(GL[g][3])
            recs.append((start, g, code, mins, dur, trunk))
        recs.sort(key=lambda r: r[0])
        out = []
        for i, (start, g, code, mins, dur, trunk) in enumerate(recs, 1):
            cdr_id = f"CDR-{d:%Y%m%d}-{i:06d}"
            call_id = f"{rng.getrandbits(64):016x}@sbc1.orx.example"
            b = rand_b(rng, code)
            out.append([cdr_id, call_id, iso(start), iso(start + timedelta(seconds=dur)), dur,
                        f"54{rng.randint(10, 99)}XXXX{rng.randint(0, 9999):04d}", masked(b),
                        rng.choice(["ORX-IN-A07", "ORX-IN-B12", "ORX-IN-C03"]), trunk, CARRIER, 16,
                        "voice", f"MED-{d:%Y%m%d}-{trunk}"])
            call_index.append((d, g, code, mins, dur, start, b, trunk, cdr_id, call_id))
        rows_by_day[d] = out

    # Rejects: the planted CO-MOB trunk gap plus correctly dropped distractors.
    rejects, rej_calls = [], []
    rej_per_day_calls = split_total(rng, REJ_CO_CALLS, len(REJ_DAYS), 1, [1] * len(REJ_DAYS))
    rej_per_day_min = [c * 3 for c in rej_per_day_calls]
    seq = 0
    for d, n, m in zip(REJ_DAYS, rej_per_day_calls, rej_per_day_min):
        for mins in call_minutes(rng, m, n):
            seq += 1
            dur = (mins - 1) * 60 + rng.randint(1, 60)
            start = datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(seconds=rng.randrange(86400))
            b = rand_b(rng, "573")
            rid = f"REJ-{d:%Y%m%d}-{seq:04d}"
            rejects.append([rid, f"RAW-{d:%Y%m%d}-TRK-NB-07-{seq:05d}", iso(start), dur, masked(b),
                            "TRK-NB-07", "UNMAPPED_TRUNK", iso(start + timedelta(minutes=17)), "false"])
            rej_calls.append((d, "CO-MOB", "573", mins, dur, start, b, "TRK-NB-07", rid, None))
    dup_src = rng.sample(call_index, 25)
    for i, c in enumerate(sorted(dup_src, key=lambda c: c[5]), 1):
        d = c[0]
        rejects.append([f"REJ-{d:%Y%m%d}-D{i:03d}", f"RAW-{d:%Y%m%d}-{c[7]}-DUP{i:03d}", iso(c[5]), c[4],
                        masked(c[6]), c[7], "DUPLICATE", iso(c[5] + timedelta(minutes=17)), "false"])
    for i in range(1, 61):
        g = rng.choice(GROUPS)
        d = rng.choice(DAYS)
        start = datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(seconds=rng.randrange(86400))
        rejects.append([f"REJ-{d:%Y%m%d}-Z{i:03d}", f"RAW-{d:%Y%m%d}-{g[3][0]}-Z{i:03d}", iso(start), 0,
                        masked(rand_b(rng, g[2][0])), g[3][0], "ZERO_DURATION",
                        iso(start + timedelta(minutes=17)), "false"])
    rejects.sort(key=lambda r: (r[2], r[0]))
    return rows_by_day, daily, call_index, rejects, rej_calls


def contracted_rate(group, d, increase_from):
    if group == "MX-MOB" and d >= increase_from:
        return MX_NEW, DECK_PART
    return Decimal(OLD[group]), DECK_FULL


def rating_lines(daily, increase_from):
    lines = []
    for (d, g, code) in sorted(daily, key=lambda k: (k[0], [x[0] for x in GROUPS].index(k[1]), k[2])):
        calls, _dur, mins = daily[(d, g, code)]
        rate, deck = contracted_rate(g, d, increase_from)
        lines.append([RUN, d.isoformat(), g, deck, code, f"{rate}", calls, mins, f"{q2(rate * mins)}", "USD"])
    return lines


def invoice_lines(daily, co_extra_calls, co_extra_min, mx_split_at=None):
    """Carrier's view. mx_split_at=None bills all MX-MOB at the new rate (the planted error)."""
    def tot(g, a=P_START, b=P_END):
        c = m = 0
        for (d, gg, _code), (n, _dur, mm) in daily.items():
            if gg == g and a <= d <= b:
                c += n
                m += mm
        return c, m
    lines = []
    if mx_split_at is None:
        c, m = tot("MX-MOB")
        lines.append(["Mexico Mobile", "521", P_START, P_END, c, m, MX_NEW])
    else:
        c, m = tot("MX-MOB", P_START, mx_split_at - timedelta(1))
        lines.append(["Mexico Mobile", "521", P_START, mx_split_at - timedelta(1), c, m, Decimal(OLD["MX-MOB"])])
        c, m = tot("MX-MOB", mx_split_at, P_END)
        lines.append(["Mexico Mobile", "521", mx_split_at, P_END, c, m, MX_NEW])
    for g, label, codes, _t in GROUPS[1:]:
        c, m = tot(g)
        if g == "CO-MOB":
            c, m = c + co_extra_calls, m + co_extra_min
        lines.append([label, "|".join(codes), P_START, P_END, c, m, Decimal(OLD[g])])
    return [{"line_no": i, "destination": l[0], "dial_codes": l[1], "service": "voice",
             "period_start": l[2].isoformat(), "period_end": l[3].isoformat(), "calls": l[4],
             "minutes": l[5], "rate": f"{l[6]}", "amount": f"{q2(l[6] * l[5])}"}
            for i, l in enumerate(lines, 1)]


def write_invoice(root, lines, amount_overrides=None, subtotal_override=None):
    amount_overrides = amount_overrides or {}
    for l in lines:
        if l["line_no"] in amount_overrides:
            l["amount"] = amount_overrides[l["line_no"]]
    subtotal = subtotal_override or f"{sum(Decimal(l['amount']) for l in lines)}"
    base = os.path.join(root, "invoices", "inbox", INV)
    header = {
        "invoice_id": INV, "issue_date": "2026-10-05", "due_date": "2026-11-04",
        "seller_party": {"party_id": "NBCS", "name": SELLER["name"], "tax_id": "GB-VAT-998877665"},
        "buyer_party": {"party_id": "ORX", "name": BUYER["name"], "tax_id": "AR-CUIT-30-71234567-9"},
        "account_id": ACCOUNT, "agreement_ref": AGR_DOC_NO,
        "period_start": "2026-09-01", "period_end": "2026-09-30", "currency": "USD",
        "line_count": len(lines), "subtotal": subtotal, "tax_total": "0.00", "total": subtotal,
        "lines": lines,
    }
    write_json(os.path.join(base, "invoice.json"), header)
    cols = ["line_no", "destination", "dial_codes", "service", "period_start", "period_end",
            "calls", "minutes", "rate", "amount"]
    write_csv(os.path.join(base, "summary.csv"), cols, [[l[c] for c in cols] for l in lines])
    write_json(os.path.join(base, "envelope.json"), {
        "message_id": "<20261005085812.4411@billing.northbridge.example>",
        "from": "wholesale-billing@northbridge.example", "to": "settlements@orionvx.example",
        "received_at": "2026-10-05T09:00:00Z",
        "subject": f"Invoice {INV} - September 2026 - account {ACCOUNT}",
        "attachments": ["invoice.json", "summary.csv", "invoice.md"]})
    md = [f"# INVOICE {INV}", "", f"**{SELLER['name']}** · VAT GB-VAT-998877665", "",
          f"Bill to: **{BUYER['name']}** · Account {ACCOUNT}  ",
          f"Agreement ref: {AGR_DOC_NO}  ", "Invoice date: 2026-10-05 · Due date: 2026-11-04  ",
          "Service period: 2026-09-01 to 2026-09-30 (UTC) · Currency: USD", "",
          "| Line | Destination | Dial codes | Period | Calls | Minutes | Rate | Amount |",
          "|---:|---|---|---|---:|---:|---:|---:|"]
    for l in lines:
        md.append(f"| {l['line_no']} | {l['destination']} | {l['dial_codes'].replace('|', ', ')} | "
                  f"{l['period_start']} to {l['period_end']} | {l['calls']:,} | {l['minutes']:,} | "
                  f"{l['rate']} | {Decimal(l['amount']):,.2f} |")
    md += ["", f"Subtotal: USD {Decimal(subtotal):,.2f}  ", "Tax: USD 0.00 (reverse charge)  ",
           f"**Total due: USD {Decimal(subtotal):,.2f}**", "",
           "Billing increment 60/60. Payment due 30 days from invoice date. Disputes in writing "
           "within 14 days of invoice date per the agreement. CDR detail available on request."]
    write_text(os.path.join(base, "invoice.md"), "\n".join(md) + "\n")
    return Decimal(subtotal), sum(Decimal(l["amount"]) for l in lines)


def write_contract(root):
    clauses = [
        ("1.1", "Parties and term", "Agreement between NBCS (seller) and ORX (buyer), effective 2025-03-01, evergreen."),
        ("3.1", "Currency", "All charges in USD."),
        ("4.1", "Billing period", "Monthly; calls attributed to a period by start time in UTC."),
        ("4.2", "Billing increment", "60/60: first 60 seconds, then 60-second increments."),
        ("4.3", "Charge rounding", "Line amount = billable minutes x rate, rounded half-up to 2 decimals per invoice line."),
        ("5.1", "Invoicing", "Invoice issued within 5 days after period end, with summary by destination; CDR detail on request."),
        ("5.2", "Payment", "Payment due 30 days from invoice date."),
        ("6.1", "Rate decks", "Seller prices from the NB-VOICE rate deck family; decks may be full or partial."),
        ("6.2", "Change indicators", "Every changed row carries a change flag and effective date."),
        ("6.3", "Rate increase notice", "An increase or new destination takes effect no earlier than the first full UTC day starting at least 7 days after the notice is sent; until then the previous rate applies."),
        ("6.4", "Rate decreases", "A decrease takes effect on the stated effective date, with no notice period."),
        ("8.1", "Dispute window", "Buyer may dispute in writing within 14 days of the invoice date."),
        ("8.2", "Minimum dispute", "Disputes below USD 50.00 per invoice are not accepted."),
        ("8.3", "Undisputed amounts", "Buyer pays the undisputed portion by the due date."),
        ("8.4", "Dispute evidence", "Notice states billed rate, correct rate, period, account ID and supporting CDR detail."),
        ("8.5", "Escalation", "Disputes unresolved after 30 days escalate to commercial management."),
        ("8.6", "Credits", "Upheld disputes are credited on the next invoice."),
    ]
    write_json(os.path.join(root, "contracts", "agreements", f"{AGR}.json"), {
        "agreement_id": AGR, "document_number": AGR_DOC_NO,
        "name": "Wholesale Voice Termination Agreement", "agreement_type": "wholesale_voice",
        "status": "active", "version": 2, "period_start": "2025-03-01", "period_end": None,
        "buyer_party": BUYER, "seller_party": SELLER, "currency": "USD", "billing_cycle": "monthly",
        "billing_timezone": "UTC", "billing_increment": "60/60",
        "rounding": {"charge_decimals": 2, "rounding_mode": "half_up", "level": "invoice_line"},
        "rate_schedule_ref": "NB-VOICE", "rate_notice_days_increase": 7, "rate_notice_days_decrease": 0,
        "invoice_due_days_after_period": 5, "payment_terms_days": 30, "dispute_window_days": 14,
        "dispute_min_amount": "50.00", "pay_undisputed_required": True,
        "dispute_evidence_required": ["billed_rate", "correct_rate", "period", "account_id", "cdr_detail"],
        "escalation_days": 30,
        "clauses": [{"clause_id": c, "title": t, "summary": s} for c, t, s in clauses],
        "amendments": ["AGR-NB-2025-014-A1"],
    })
    doc = [f"# Wholesale Voice Termination Agreement ({AGR_DOC_NO})", "",
           f"Internal ID {AGR}, version 2 (amendment A1 added Guatemala Mobile, 2026-02-01). Fictional, prototype use only.",
           f"Between **{SELLER['name']}** (\"Seller\") and **{BUYER['name']}** (\"Buyer\"). Signed 2025-02-20.", ""]
    long = {
        "6.3": "6.3 **Rate increase notice.** Seller shall give Buyer at least seven (7) days' written notice of any rate increase or new destination. An increase takes effect at 00:00 UTC on the first full day that begins at least seven (7) days after the notice is sent, or on the effective date stated in the deck if later. Where a deck states an earlier date, the previous rate continues to apply until the increase takes effect under this clause.",
        "8.1": "8.1 **Dispute window.** Buyer may dispute any invoice, in whole or in part, by written notice within fourteen (14) days of the invoice date.",
        "8.3": "8.3 **Undisputed amounts.** Buyer shall pay the undisputed portion of an invoice by its due date. Raising a dispute does not suspend payment of undisputed amounts.",
    }
    for c, t, s in clauses:
        doc += [f'<a id="clause-{c}"></a>', long.get(c, f"{c} **{t}.** {s}"), ""]
    write_text(os.path.join(root, "contracts", "documents", f"{AGR}.md"), "\n".join(doc))


def write_rates(root, notice_sent):
    deck_cols = ["dial_code", "destination", "destination_group", "service", "mcc_mnc", "rate", "currency",
                 "effective_date", "end_date", "change_flag", "billing_increment", "connection_fee"]
    full = []
    for g, label, codes, _t in GROUPS:
        for code in codes:
            full.append([code, label, g, "voice", "", OLD[g], "USD", "2026-08-01", "", "UNCHANGED", "60/60", "0.0000"])
    write_csv(os.path.join(root, "rates", "decks", f"{DECK_FULL}.csv"), deck_cols, full)
    write_csv(os.path.join(root, "rates", "decks", f"{DECK_PART}.csv"), deck_cols,
              [["521", "Mexico Mobile", "MX-MOB", "voice", "", f"{MX_NEW}", "USD", "2026-09-15", "",
                "INCREASE", "60/60", "0.0000"]])
    recv = notice_sent + timedelta(minutes=3)
    write_csv(os.path.join(root, "rates", "deck_history.csv"),
              ["deck_id", "rate_schedule_ref", "deck_type", "source_file", "received_at", "loaded_at",
               "loaded_by", "load_status", "total_rows", "min_effective_date", "max_effective_date", "notice_id"],
              [[DECK_FULL, "NB-VOICE", "full", "NBCS_ORX_AZ_20260801.csv", "2026-07-23T16:20:00Z",
                "2026-07-24T10:05:00Z", "pricing.ops@orionvx.example", "loaded", len(full), "2026-08-01", "2026-08-01", ""],
               [DECK_PART, "NB-VOICE", "partial", "NBCS_ORX_PARTIAL_20260907.csv", iso(recv),
                iso(recv + timedelta(hours=2, minutes=11)), "pricing.ops@orionvx.example", "loaded", 1,
                "2026-09-15", "2026-09-15", NOTICE]])
    write_json(os.path.join(root, "rates", "notices", f"{NOTICE}.json"), {
        "notice_id": NOTICE, "from_party": "NBCS", "sent_at": iso(notice_sent), "received_at": iso(recv),
        "channel": "email", "subject": "NBCS rate notification: Mexico Mobile increase",
        "deck_id": DECK_PART,
        "summary_changes": [{"destination_group": "MX-MOB", "old_rate": OLD["MX-MOB"], "new_rate": f"{MX_NEW}",
                             "effective_date": "2026-09-15", "change_flag": "INCREASE"}]})
    write_text(os.path.join(root, "rates", "notices", f"{NOTICE}.eml.md"), "\n".join([
        "From: rates@northbridge.example", "To: pricing@orionvx.example",
        f"Date: {notice_sent:%a, %d %b %Y %H:%M:%S} +0000",
        "Subject: NBCS rate notification: Mexico Mobile increase",
        "Attachment: NBCS_ORX_PARTIAL_20260907.csv", "",
        "Dear partner,", "",
        "Please find attached a partial rate deck for account " + ACCOUNT + ".",
        "Mexico Mobile (521) increases from USD 0.0120 to USD 0.0185 per minute, effective 15 September 2026.",
        "All other destinations are unchanged.", "",
        "Regards,", "Northbridge Carrier Services, Wholesale Pricing", ""]))


def write_usage(root, rows_by_day, daily, rejects):
    cols = ["cdr_id", "call_id", "start_time_utc", "end_time_utc", "duration_sec", "a_number", "b_number",
            "ingress_trunk", "egress_trunk", "egress_carrier_id", "release_cause", "service", "mediation_batch_id"]
    for d, rows in rows_by_day.items():
        write_csv_gz(os.path.join(root, "usage", "cdrs", f"{d}.csv.gz"), cols, rows)
    summ = {}
    for (d, g, code), (n, dur, m) in daily.items():
        s = summ.setdefault((d, g), [0, 0, 0, set()])
        s[0] += n
        s[1] += dur
        s[2] += m
        s[3].update(f"MED-{d:%Y%m%d}-{t}" for t in GL[g][3])
    write_csv(os.path.join(root, "usage", "daily_summary.csv"),
              ["date_utc", "egress_carrier_id", "destination_group", "calls", "duration_sec",
               "billable_minutes", "source_batch_ids"],
              [[d.isoformat(), CARRIER, g, s[0], s[1], s[2], "|".join(sorted(s[3]))]
               for (d, g), s in sorted(summ.items(), key=lambda kv: (kv[0][0], [x[0] for x in GROUPS].index(kv[0][1])))])
    write_csv(os.path.join(root, "usage", "rejects.csv"),
              ["reject_id", "cdr_raw_ref", "start_time_utc", "duration_sec", "b_number", "egress_trunk",
               "reject_reason", "rejected_at", "reprocessed"], rejects)
    tm = [[f"TRK-NB-0{i}", CARRIER, "egress", "2025-03-01", ""] for i in range(1, 7)]
    tm.append(["TRK-NB-07", CARRIER, "egress", "2026-10-01", ""])
    tm.append(["TRK-AL-01", "ALTV", "egress", "2025-06-01", ""])
    write_csv(os.path.join(root, "usage", "trunk_map.csv"),
              ["trunk_id", "carrier_id", "direction", "valid_from", "valid_to"], tm)


def write_rating(root, lines):
    write_json(os.path.join(root, "rating", "runs", f"{RUN}.json"), {
        "run_id": RUN, "period_start": "2026-09-01", "period_end": "2026-09-30", "carrier_id": CARRIER,
        "agreement_id": AGR, "agreement_version": 2, "decks_used": [DECK_FULL, DECK_PART],
        "run_at": "2026-10-01T04:30:00Z", "engine_version": "orx-rate 3.4.1", "status": "complete"})
    write_csv(os.path.join(root, "rating", "runs", RUN, "lines.csv"),
              ["run_id", "date_local", "destination_group", "deck_id", "dial_code_rule", "rate", "calls",
               "billable_minutes", "amount", "currency"], lines)
    return sum(Decimal(l[8]) for l in lines)


def write_empty_stores(root):
    for p in ["cases", "ledger", "carrier-portal/submissions", "carrier-portal/responses"]:
        write_text(os.path.join(root, p, ".keep"), "")


def scenario_yaml(sid, data_root, inherits, expected, extra_events=""):
    inh = f"inherits: {inherits}\n" if inherits else ""
    exp = "\n".join(f"  {k}: {json.dumps(v)}" for k, v in expected.items())
    portal = ""
    if expected.get("decision") in ("partial_dispute", "full_dispute") or expected.get("decision_after_evidence") == "partial_dispute":
        credit = expected.get("supported_after_evidence", expected.get("supported"))
        portal = f"""  - on: portal_submit
    after: P2D
    action: portal_response
    response: {{status: acknowledged}}
  - on: portal_submit
    after: P6D
    action: portal_response
    response: {{status: resolved, outcome: customer_favour, credit_amount: "{credit}"}}
"""
    return f"""id: {sid}
sim_start: 2026-10-05T09:00:00Z
data_root: {data_root}
{inh}events:
  - at: 2026-10-05T09:00:00Z
    action: deliver_invoice
    invoice_id: {INV}
{extra_events}{portal}expected:
{exp}
"""


def main():
    rng = random.Random(SEED)
    for p in (SIM, KEYS):
        shutil.rmtree(p, ignore_errors=True)
    base = os.path.join(SIM, "base")
    rows_by_day, daily, call_index, rejects, rej_calls = build_base(rng)
    sep15 = date(2026, 9, 15)

    write_contract(base)
    write_rates(base, datetime(2026, 9, 7, 14, 0, tzinfo=UTC))
    write_usage(base, rows_by_day, daily, rejects)
    expected = write_rating(base, rating_lines(daily, sep15))
    inv_lines = invoice_lines(daily, REJ_CO_CALLS, REJ_CO_MIN)
    invoiced, _ = write_invoice(base, inv_lines)
    write_empty_stores(base)
    assert expected == Decimal("25000.00") and invoiced == Decimal("27000.00"), (expected, invoiced)

    mx_early_min = sum(v[2] for (d, g, _c), v in daily.items() if g == "MX-MOB" and d < sep15)
    supported = q2(mx_early_min * (MX_NEW - Decimal(OLD["MX-MOB"])))
    assert supported == Decimal("1820.00")

    def pct(a, b):
        return f"{(a / b * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"

    mx_cites = ["sim://invoices/invoice/%s/line/1" % INV, f"sim://rates/deck/{DECK_PART}/row/521",
                f"sim://rates/deck/{DECK_FULL}/row/521", f"sim://rates/notice/{NOTICE}",
                f"sim://contracts/agreement/{AGR}#clause-6.3"] + \
               [f"sim://usage/daily/2026-09-{d:02d}/MX-MOB" for d in range(1, 15)]
    co_rej_ids = [r[0] for r in rejects if r[6] == "UNMAPPED_TRUNK"]
    keys = {}
    common_dq = {
        "duplicate_cdrs_in_usage": 0,
        "rejects": {"UNMAPPED_TRUNK": {"count": REJ_CO_CALLS, "billable_minutes": REJ_CO_MIN,
                                       "egress_trunk": "TRK-NB-07", "dates": "2026-09-21..2026-09-30",
                                       "billable": True, "note": "trunk_map adds TRK-NB-07 only from 2026-10-01"},
                    "DUPLICATE": {"count": 25, "billable": False, "note": "correctly dropped; not an explanation"},
                    "ZERO_DURATION": {"count": 60, "billable": False, "note": "correctly dropped"}},
        "blocking": False,
    }
    keys["base"] = {
        "scenario": "base",
        "thresholds": {"case_open_pct_of_invoiced": "2", "evidence_request_unresolved_pct_of_invoiced": "1",
                       "status": "confirmed by Federico 2026-10-02"},
        "figures": {"invoiced": "27000.00", "expected": "25000.00", "variance": "2000.00",
                    "variance_pct_of_invoice": pct(Decimal(2000), invoiced), "variance_pct_of_expected": "8.00",
                    "supported": "1820.00", "explained_carrier_favour": "180.00", "unresolved": "0.00",
                    "undisputed_payable": "25180.00"},
        "case": {"opens": True, "case_id": "CASE-0001", "correlation_id": f"{CARRIER}:{INV}", "severity": "high",
                 "dispute_deadline": "2026-10-19", "payment_due": "2026-11-04"},
        "step2_data_quality": common_dq,
        "step3_exceptions": [
            {"invoice_line": 1, "destination_group": "MX-MOB", "exception_type": "RATE_BEFORE_EFFECTIVE",
             "invoiced": "11100.00", "expected": "9280.00", "variance": "1820.00",
             "detail": f"{mx_early_min} min on 2026-09-01..14 billed at 0.0185; contracted 0.0120"},
            {"invoice_line": 3, "destination_group": "CO-MOB", "exception_type": "VOLUME",
             "invoiced": "4680.00", "expected": "4500.00", "variance": "180.00",
             "detail": "carrier 312000 min vs our 300000 min; +4000 calls"}],
        "step3_matches": ["line 2 MX-FIX 2400.00", "line 4 BR-MOB 5250.00", "line 5 GT-MOB 3570.00"],
        "step4_causes": [
            {"group": "MX-MOB", "claim_class": ["fact", "calculation"],
             "statement": "Increase noticed 2026-09-07T14:00Z is effective 2026-09-15 under clause 6.3 and the deck row; carrier applied it from 2026-09-01.",
             "cites": mx_cites},
            {"group": "CO-MOB", "claim_class": ["fact"],
             "statement": "Mediation rejected 4000 CO-MOB calls / 12000 billable minutes on TRK-NB-07 (UNMAPPED_TRUNK, not reprocessed); the carrier charge is legitimate and our expected is understated.",
             "cites": ["sim://invoices/invoice/%s/line/3" % INV, "sim://usage/trunk_map/TRK-NB-07"] +
                      [f"sim://usage/reject/{r}" for r in co_rej_ids[:3]] + ["... all 4000 UNMAPPED_TRUNK rejects"]}],
        "step5_calcs": [
            {"name": "supported_mx_mob", "formula": "280000 x (0.0185 - 0.0120)", "result": "1820.00"},
            {"name": "co_mob_explained", "formula": "12000 x 0.0150", "result": "180.00"},
            {"name": "undisputed_payable", "formula": "27000.00 - 1820.00", "result": "25180.00"}],
        "step6_decision": {"decision": "partial_dispute", "amount": "1820.00",
                           "why": ["supported 1820.00 >= dispute_min_amount 50.00 (clause 8.2)",
                                   "unresolved 0.00 <= 1% of invoiced",
                                   "CO-MOB 180.00 is our own mediation fault; pay it and fix trunk_map"],
                           "internal_actions": ["add TRK-NB-07 to trunk_map from 2026-09-21 and reprocess the 4000 rejects"]},
        "step7_submission": {"reason_code": "RATE_BEFORE_EFFECTIVE", "product": "Mexico Mobile", "bill_period": "2026-09",
                             "account_id": ACCOUNT, "invoice_id": INV, "disputed_amount": "1820.00",
                             "billed_rate": "0.0185", "correct_rate": "0.0120",
                             "from_date": "2026-09-01", "through_date": "2026-09-14"},
        "must_not": ["dispute the CO-MOB 180.00", "treat DUPLICATE or ZERO_DURATION rejects as billable",
                     "state any amount that is not a rules-actor calc"],
    }
    write_text(os.path.join(base, "scenario.yaml"), scenario_yaml("base", "sim-data/base", None,
               {"case_opened": True, "supported": "1820.00", "unresolved": "0.00", "decision": "partial_dispute"}))

    # ---------------------------------------------------------------- variants
    def overlay(vid, deleted=(), note=""):
        root = os.path.join(SIM, vid)
        write_json(os.path.join(root, "overlay.json"), {"inherits": "sim-data/base", "deleted": list(deleted),
                                                        "note": note})
        return root

    rejects_no_co = [r for r in rejects if r[6] != "UNMAPPED_TRUNK"]

    # V1: unexplained, immaterial gap.
    v1 = overlay("v1", note="rejects.csv without the CO-MOB UNMAPPED_TRUNK rows")
    write_csv(os.path.join(v1, "usage", "rejects.csv"),
              ["reject_id", "cdr_raw_ref", "start_time_utc", "duration_sec", "b_number", "egress_trunk",
               "reject_reason", "rejected_at", "reprocessed"], rejects_no_co)
    exp_v1 = {"case_opened": True, "supported": "1820.00", "unresolved": "180.00", "decision": "partial_dispute"}
    write_text(os.path.join(v1, "scenario.yaml"), scenario_yaml("v1", "sim-data/v1", "sim-data/base", exp_v1))
    keys["v1"] = {"scenario": "v1", "edit": "CO-MOB rejects removed",
                  "figures": {"invoiced": "27000.00", "expected": "25000.00", "supported": "1820.00",
                              "unresolved": "180.00", "unresolved_pct_of_invoice": pct(Decimal(180), invoiced),
                              "undisputed_payable": "25180.00"},
                  "step6_decision": {"decision": "partial_dispute", "amount": "1820.00",
                                     "why": ["unresolved 180.00 = 0.67% of invoiced, below the 1% evidence threshold",
                                             "180.00 recorded as unresolved, not disputed and not explained"]}}

    # V2: material unexplained gap, resolved by carrier CDR detail.
    v2 = overlay("v2", note="V1 rejects, CO-MOB invoiced at 340000 min, carrier CDR detail delivered on request")
    write_csv(os.path.join(v2, "usage", "rejects.csv"),
              ["reject_id", "cdr_raw_ref", "start_time_utc", "duration_sec", "b_number", "egress_trunk",
               "reject_reason", "rejected_at", "reprocessed"], rejects_no_co)
    # Carrier detail = our CO-MOB calls + the TRK-NB-07 calls + 28000 min of duplicated carrier records.
    co_ours = [c for c in call_index if c[1] == "CO-MOB"]
    co_all = sorted(co_ours + rej_calls, key=lambda c: c[5])
    pool = rng.sample(co_ours, len(co_ours))
    dup, dmin = [], 0
    for c in pool:
        if dmin + c[3] <= 28000:
            dup.append(c)
            dmin += c[3]
        if dmin == 28000:
            break
    assert dmin == 28000
    detail = [(c, False) for c in co_all] + [(c, True) for c in dup]
    detail.sort(key=lambda x: (x[0][5], x[1]))
    det_rows = []
    for i, (c, _isdup) in enumerate(detail, 1):
        rate = Decimal(OLD["CO-MOB"])
        det_rows.append([f"NBCS-{c[0]:%Y%m%d}-{i:07d}", c[5].strftime("%Y-%m-%dT%H:%M:%S"), c[4], c[3],
                         masked(c[6]), "Colombia Mobile", f"{rate}", f"{(rate * c[3]).quantize(Decimal('0.0001'))}", c[7]])
    v2_extra_min = REJ_CO_MIN + 28000
    v2_lines = invoice_lines(daily, REJ_CO_CALLS + len(dup), v2_extra_min)
    v2_inv, _ = write_invoice(v2, v2_lines)
    assert v2_inv == Decimal("27420.00"), v2_inv
    # Staged file: the runner moves it into the inbox when the evidence request fires.
    write_csv_gz(os.path.join(v2, "staged", "invoices", "inbox", INV, "cdr_detail.csv.gz"),
                 ["carrier_cdr_id", "start_time_local", "duration_sec", "billed_minutes", "b_number_masked",
                  "destination", "rate", "amount", "our_trunk_id"], det_rows)
    exp_v2 = {"case_opened": True, "supported": "1820.00", "unresolved": "600.00", "decision": "request_evidence",
              "supported_after_evidence": "2240.00", "unresolved_after_evidence": "0.00",
              "decision_after_evidence": "partial_dispute"}
    ev = f"""  - on: evidence_request
    match: {{to: carrier, item: cdr_detail, destination_group: CO-MOB}}
    after: P3D
    action: deliver_file
    from: staged/invoices/inbox/{INV}/cdr_detail.csv.gz
    path: invoices/inbox/{INV}/cdr_detail.csv.gz
"""
    write_text(os.path.join(v2, "scenario.yaml"), scenario_yaml("v2", "sim-data/v2", "sim-data/base", exp_v2, ev))
    keys["v2"] = {"scenario": "v2", "edit": "V1 plus carrier bills 340000 CO-MOB min; detail staged",
                  "figures_before_evidence": {"invoiced": "27420.00", "expected": "25000.00", "supported": "1820.00",
                                              "unresolved": "600.00",
                                              "unresolved_pct_of_invoice": pct(Decimal(600), v2_inv)},
                  "decision_before_evidence": {"decision": "request_evidence",
                                               "items": [{"to": "carrier", "item": "cdr_detail", "destination_group": "CO-MOB",
                                                          "period": "2026-09"}],
                                               "why": "unresolved 600.00 = 2.19% of invoiced > 1%"},
                  "carrier_detail_findings": {
                      "rows": len(det_rows),
                      "matched_to_our_cdrs": {"calls": len(co_ours), "billable_minutes": 300000},
                      "on_trunk_TRK-NB-07": {"calls": REJ_CO_CALLS, "billable_minutes": REJ_CO_MIN, "amount": "180.00",
                                             "classification": "carrier_favour",
                                             "note": "our trunk, missing from our usage; trunk_map gains TRK-NB-07 only 2026-10-01"},
                      "duplicates": {"calls": len(dup), "billable_minutes": 28000, "amount": "420.00",
                                     "rule": "same start_time_local, b_number_masked and duration_sec as another carrier row",
                                     "classification": "supported", "reason_code": "DUPLICATE"}},
                  "figures_after_evidence": {"supported": "2240.00", "explained_carrier_favour": "180.00",
                                             "unresolved": "0.00", "undisputed_payable": "25180.00"},
                  "decision_after_evidence": {"decision": "partial_dispute", "amount": "2240.00",
                                              "submissions": [{"reason_code": "RATE_BEFORE_EFFECTIVE", "amount": "1820.00"},
                                                              {"reason_code": "DUPLICATE", "amount": "420.00"}]},
                  "note": "Spec section 6 leaves the post-evidence outcome open; the duplicate/trunk split is this dataset's choice."}

    # V3: notice sent late (2026-09-12), increase valid only from 2026-09-20.
    v3 = overlay("v3", note="notice sent 2026-09-12T14:00Z; internal rating applies clause 6.3 from 2026-09-20")
    write_rates(v3, datetime(2026, 9, 12, 14, 0, tzinfo=UTC))
    shutil.rmtree(os.path.join(v3, "rates", "decks"))  # decks themselves are unchanged
    sep20 = date(2026, 9, 20)
    v3_expected = write_rating(v3, rating_lines(daily, sep20))
    mx_pre20 = sum(v[2] for (d, g, _c), v in daily.items() if g == "MX-MOB" and d < sep20)
    v3_supported = q2(mx_pre20 * (MX_NEW - Decimal(OLD["MX-MOB"])))
    mx_15_19 = mx_pre20 - mx_early_min
    exp_v3 = {"case_opened": True, "supported": f"{v3_supported}", "unresolved": "0.00", "decision": "partial_dispute"}
    write_text(os.path.join(v3, "scenario.yaml"), scenario_yaml("v3", "sim-data/v3", "sim-data/base", exp_v3))
    keys["v3"] = {"scenario": "v3", "edit": "notice sent 2026-09-12T14:00Z; deck still says 2026-09-15",
                  "rule": "7 days after 2026-09-12T14:00Z = 2026-09-19T14:00Z; first full UTC day = 2026-09-20",
                  "figures": {"invoiced": "27000.00", "expected": f"{v3_expected}",
                              "variance": f"{Decimal('27000.00') - v3_expected}",
                              "mx_mob_minutes_0901_0919": mx_pre20, "mx_mob_minutes_0915_0919": mx_15_19,
                              "supported": f"{v3_supported}", "explained_carrier_favour": "180.00", "unresolved": "0.00",
                              "undisputed_payable": f"{Decimal('27000.00') - v3_supported}"},
                  "step6_decision": {"decision": "partial_dispute", "amount": f"{v3_supported}",
                                     "submission": {"reason_code": "RATE_BEFORE_EFFECTIVE", "from_date": "2026-09-01",
                                                    "through_date": "2026-09-19"}},
                  "note": "V3 rating run already applies the clause 6.3 date, so step 2's effective-date check passes; the deck's stated 09-15 is the carrier's error."}

    # V4: partial deck missing from the store.
    overlay("v4", deleted=[f"rates/decks/{DECK_PART}.csv"], note="deck file removed; history and notice still reference it")
    write_text(os.path.join(SIM, "v4", "scenario.yaml"), scenario_yaml("v4", "sim-data/v4", "sim-data/base",
               {"case_opened": True, "step2_blocking": True, "decision": "request_evidence"}))
    keys["v4"] = {"scenario": "v4", "edit": f"rates/decks/{DECK_PART}.csv deleted",
                  "step2": {"blocking": True, "check": "Effective dates / required record",
                            "detail": f"deck_history and rating run reference {DECK_PART} but the deck file is absent"},
                  "decision": {"decision": "request_evidence",
                               "items": [{"to": "internal_team", "item": f"rate deck {DECK_PART} and notice {NOTICE}"}]},
                  "must_not": ["compute a supported amount before the deck is restored"]}

    # V5: line amount does not sum.
    v5 = overlay("v5", note="line 4 amount 5520.00, subtotal still 27000.00")
    write_invoice(v5, invoice_lines(daily, REJ_CO_CALLS, REJ_CO_MIN), amount_overrides={4: "5520.00"},
                  subtotal_override="27000.00")
    write_text(os.path.join(v5, "scenario.yaml"), scenario_yaml("v5", "sim-data/v5", "sim-data/base",
               {"case_opened": True, "step2_blocking": True, "decision": "request_evidence"}))
    keys["v5"] = {"scenario": "v5", "edit": "invoice line 4 amount 5520.00; subtotal 27000.00",
                  "step2": {"blocking": True, "check": "Totals",
                            "detail": "lines sum to 27270.00 but subtotal says 27000.00; line 4 is 250000 x 0.0210 = 5250.00, not 5520.00"},
                  "decision": {"decision": "request_evidence", "items": [{"to": "carrier", "item": "corrected invoice"}]}}

    # V6: clean invoice.
    v6 = overlay("v6", note="invoice equals 25000.00: MX-MOB split at 09-15, CO-MOB 300000 min; no CO rejects")
    v6_inv, _ = write_invoice(v6, invoice_lines(daily, 0, 0, mx_split_at=sep15))
    assert v6_inv == Decimal("25000.00")
    write_csv(os.path.join(v6, "usage", "rejects.csv"),
              ["reject_id", "cdr_raw_ref", "start_time_utc", "duration_sec", "b_number", "egress_trunk",
               "reject_reason", "rejected_at", "reprocessed"], rejects_no_co)
    write_text(os.path.join(v6, "scenario.yaml"), scenario_yaml("v6", "sim-data/v6", "sim-data/base",
               {"case_opened": False}))
    keys["v6"] = {"scenario": "v6", "edit": "invoice 25000.00 with six lines; CO rejects removed for consistency",
                  "figures": {"invoiced": "25000.00", "expected": "25000.00", "variance": "0.00"},
                  "case": {"opens": False, "why": "variance 0.00% <= 2%"}}

    for k, v in keys.items():
        write_json(os.path.join(KEYS, f"{k}.json"), v)
    print(json.dumps({"base_expected": str(expected), "base_invoiced": str(invoiced),
                      "cdrs": sum(len(r) for r in rows_by_day.values()), "rejects": len(rejects),
                      "v2_detail_rows": len(det_rows), "v2_dups": len(dup),
                      "v3_expected": str(v3_expected), "v3_supported": str(v3_supported)}, indent=1))


if __name__ == "__main__":
    main()

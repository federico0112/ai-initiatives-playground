#!/usr/bin/env python3
"""Generate the simulated records for the 8% wholesale A2P SMS invoice case.

Matches ../simulated-systems.md (schemas in section 4, seeded scenario in section 6,
layout in section 9). Stdlib only and deterministic: re-running rewrites identical files.
All companies, people, networks, MCC-MNC codes and numbers are fictional.

Outputs (relative to this folder):
  sim-data/base/          full data root for the test case
  answer-keys/base.json   ground truth (kept outside the data root so adapters and
                          agents never see it)
"""
import csv
import gzip
import io
import json
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
BUYER = {"party_id": "ORX", "name": "Orion Messaging Exchange", "role": "buyer"}
SELLER = {"party_id": "NBCS", "name": "Northbridge Carrier Services", "role": "seller"}
AGR = "AGR-NB-2025-014"
AGR_DOC_NO = "NBCS-SMS-2025-0117"
ACCOUNT = "NBCS-ACC-30988"
INV = "NB-INV-2026-09-0412"
DECK_FULL = "NB-SMS-2026-08-01"
DECK_PART = "NB-SMS-2026-09-07"
NOTICE = "RN-NB-2026-0907"
RUN = "RUN-2026-09"
P_START, P_END = date(2026, 9, 1), date(2026, 9, 30)
DAYS = [P_START + timedelta(d) for d in range(30)]

OLD = {"MX-ALT": "0.0120", "MX-VRD": "0.0060", "CO-AND": "0.0150", "BR-CRZ": "0.0210", "GT-QTZ": "0.0850"}
MX_NEW = Decimal("0.0185")
GROUPS = [  # network group, carrier label, MCC-MNC(s), SMPP connection(s), country code, national digits
    ("MX-ALT", "Mexico - Altavia Movil", ["334990"], ["SMPP-NB-01", "SMPP-NB-02"], "52", 10),
    ("MX-VRD", "Mexico - Verdemovil", ["334991"], ["SMPP-NB-06"], "52", 10),
    ("CO-AND", "Colombia - Andina Movil", ["732990"], ["SMPP-NB-03"], "57", 10),
    ("BR-CRZ", "Brazil - Cruzeiro Movel", ["724990", "724991"], ["SMPP-NB-04"], "55", 11),
    ("GT-QTZ", "Guatemala - Quetzal Movil", ["704990"], ["SMPP-NB-05"], "502", 8),
]
GL = {g[0]: g for g in GROUPS}
COUNTRY = {"MX": "Mexico", "CO": "Colombia", "BR": "Brazil", "GT": "Guatemala"}
# Billable segments per (group, sub-period); every total is from spec section 6.
TARGETS = {("MX-ALT", date(2026, 9, 1), date(2026, 9, 14)): 280000,
           ("MX-ALT", date(2026, 9, 15), date(2026, 9, 30)): 320000,
           ("MX-VRD", P_START, P_END): 400000,
           ("CO-AND", P_START, P_END): 300000,
           ("BR-CRZ", P_START, P_END): 250000,
           ("GT-QTZ", P_START, P_END): 42000}
REJ_CO_SEG = 12000                     # UNMAPPED_CONNECTION on SMPP-NB-07, 09-21..09-30
REJ_DAYS = [date(2026, 9, d) for d in range(21, 31)]

CUSTOMERS = [  # customer_id, name, sender_id, traffic type, share
    ("CUST-0101", "Banco Litoral", "BLITORAL", "otp", 30),
    ("CUST-0102", "Rapido Envios", "RAPIDOENV", "transactional", 20),
    ("CUST-0103", "Mercado Sol", "MERCADOSOL", "marketing", 15),
    ("CUST-0104", "Aerolineas Pampa", "AEROPAMPA", "transactional", 10),
    ("CUST-0105", "Salud Mas", "SALUDMAS", "transactional", 10),
    ("CUST-0106", "Pago Facil Andino", "PAGOFACIL", "otp", 15),
]
CUST_CW = [sum(c[4] for c in CUSTOMERS[:i + 1]) for i in range(len(CUSTOMERS))]
# Submit hour (UTC) profile: Latin American business day, quiet 05:00-11:00 UTC.
HOUR_W = [5, 4, 3, 2, 1, 1, 1, 1, 1, 2, 3, 5, 7, 8, 8, 8, 8, 8, 8, 8, 7, 7, 6, 6]
HOUR_CW = [sum(HOUR_W[:i + 1]) for i in range(24)]
SEG_CW = [86, 96, 100]                 # 1, 2 or 3 segments: 86% / 10% / 4%
# Receipt outcome per message: DELIVRD / UNDELIV / EXPIRED / no receipt. Billing ignores it (clause 4.2).
DLR_CW = {"MX-ALT": [945, 975, 995, 1000], "MX-VRD": [950, 978, 996, 1000], "CO-AND": [940, 972, 994, 1000],
          "BR-CRZ": [955, 980, 997, 1000], "GT-QTZ": [930, 965, 992, 1000]}
DLR = ["DELIVRD", "UNDELIV", "EXPIRED", ""]
UNDELIV_ERR = ["001", "011", "022"]
MSG_COLS = ["msg_id", "submitted_at_utc", "customer_id", "sender_id", "dest_masked", "mcc_mnc", "smpp_connection",
            "supplier_id", "submit_status", "supplier_msg_id", "segments", "data_coding", "dlr_stat", "dlr_err",
            "dlr_received_at", "mediation_batch_id"]
REJ_COLS = ["reject_id", "raw_ref", "submitted_at_utc", "segments", "dest_masked", "mcc_mnc", "smpp_connection",
            "submit_status", "supplier_msg_id", "reject_reason", "rejected_at", "reprocessed"]


def q2(x):
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_ms(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


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


def segment_list(rng, total):
    """Per-message segment counts (1-3) summing exactly to total."""
    out, left = [], total
    while left:
        s = min(rng.choices((1, 2, 3), cum_weights=SEG_CW)[0], left)
        out.append(s)
        left -= s
    return out


def dest_masked(rng, g):
    cc, nd = GL[g][4], GL[g][5]
    if g == "BR-CRZ":
        nn = rng.choice(["11", "21", "31", "41", "51", "61", "71", "81"]) + "9" + f"{rng.randrange(10000):04d}"
    elif g == "GT-QTZ":
        nn = str(rng.randint(3, 5)) + f"{rng.randrange(1000):03d}"
    elif g == "CO-AND":
        nn = "3" + f"{rng.randrange(10, 60):02d}" + f"{rng.randrange(1000):03d}"
    else:
        nn = str(rng.randint(2, 9)) + f"{rng.randrange(10 ** 5):05d}"
    assert len(nn) == nd - 4
    return cc + nn + "XXXX"


def submit_times(rng, d, n):
    day0 = datetime(d.year, d.month, d.day, tzinfo=UTC)
    hours = rng.choices(range(24), cum_weights=HOUR_CW, k=n)
    return [day0 + timedelta(milliseconds=h * 3600000 + rng.randrange(3600000)) for h in hours]


def receipt(rng, g, sub):
    stat = rng.choices(DLR, cum_weights=DLR_CW[g])[0]
    if stat == "DELIVRD":
        return stat, "000", iso_ms(sub + timedelta(milliseconds=rng.randint(800, 20000)))
    if stat == "UNDELIV":
        return stat, rng.choice(UNDELIV_ERR), iso_ms(sub + timedelta(milliseconds=rng.randint(1500, 60000)))
    if stat == "EXPIRED":
        return stat, "000", iso_ms(sub + timedelta(hours=4))
    return "", "", ""


def build_base(rng):
    """Write nothing; return per-day message rows plus the aggregates and samples the case needs."""
    units = {}                               # (date, group, mcc_mnc) -> billable segments
    for (g, a, b), total in TARGETS.items():
        days = [d for d in DAYS if a <= d <= b]
        # MX-ALT is flat (20,000 segments/day) so notice-date variants move round amounts.
        wts = [1.0] * len(days) if g == "MX-ALT" else \
            [rng.uniform(0.9, 1.1) * (0.8 if d.weekday() >= 5 else 1.0) for d in days]
        per_day = split_total(rng, total, len(days), 10, wts)
        codes = GL[g][2]
        for d, u in zip(days, per_day):
            parts = [u] if len(codes) == 1 else split_total(rng, u, len(codes), 10, [0.6, 0.4])
            for mm, cu in zip(codes, parts):
                units[(d, g, mm)] = cu
    dup_days = sorted(rng.choice(DAYS) for _ in range(25))
    return units, dup_days


def gen_day(rng, d, units):
    """Message rows for one UTC day, sorted by submit time, and the daily roll-up."""
    recs = []
    for (dd, g, mm), u in units.items():
        if dd != d:
            continue
        segs = segment_list(rng, u)
        for sub, seg in zip(submit_times(rng, d, len(segs)), segs):
            recs.append((sub, g, mm, seg))
    recs.sort(key=lambda r: r[0])
    rows, daily = [], {}
    for i, (sub, g, mm, seg) in enumerate(recs, 1):
        conn = rng.choice(GL[g][3])
        cust = CUSTOMERS[rng.choices(range(len(CUSTOMERS)), cum_weights=CUST_CW)[0]]
        stat, err, dlr_at = receipt(rng, g, sub)
        dc = "8" if seg > 1 and rng.random() < 0.35 else "0"
        rows.append([f"ORX-{d:%Y%m%d}-{i:07d}", iso_ms(sub), cust[0], cust[2], dest_masked(rng, g), mm, conn,
                     CARRIER, "0x00000000", f"{rng.getrandbits(64):016x}", seg, dc, stat, err, dlr_at,
                     f"MED-{d:%Y%m%d}-{conn}"])
        a = daily.setdefault((d, g, mm), [0, 0, 0, 0, 0, 0])
        a[0] += 1
        a[1] += seg
        a[2 + DLR.index(stat)] += 1
    return rows, daily


def build_rejects(rng, msg_sample):
    """The planted SMPP-NB-07 gap plus correctly dropped distractors. msg_sample: rows to duplicate."""
    rejects, rej_msgs = [], []
    seq = 0
    for d, u in zip(REJ_DAYS, split_total(rng, REJ_CO_SEG, len(REJ_DAYS), 1, [1] * len(REJ_DAYS))):
        segs = segment_list(rng, u)
        for sub, seg in sorted(zip(submit_times(rng, d, len(segs)), segs)):
            seq += 1
            dm = dest_masked(rng, "CO-AND")
            smid = f"{rng.getrandbits(64):016x}"
            stat, _err, _at = receipt(rng, "CO-AND", sub)
            rid = f"REJ-{d:%Y%m%d}-{seq:05d}"
            rejects.append([rid, f"RAW-{d:%Y%m%d}-SMPP-NB-07-{seq:05d}", iso_ms(sub), seg, dm, "732990", "SMPP-NB-07",
                            "0x00000000", smid, "UNMAPPED_CONNECTION", iso(sub + timedelta(minutes=17)), "false"])
            rej_msgs.append((sub, smid, dm, seg, "SMPP-NB-07", stat))
    for i, r in enumerate(sorted(msg_sample, key=lambda r: r[1]), 1):
        d = r[1][:10].replace("-", "")
        sub = datetime.fromisoformat(r[1].replace("Z", "+00:00"))
        rejects.append([f"REJ-{d}-D{i:03d}", f"RAW-{d}-{r[6]}-DUP{i:03d}", r[1], r[10], r[4], r[5], r[6],
                        r[8], r[9], "DUPLICATE", iso(sub + timedelta(minutes=17)), "false"])
    for i in range(1, 61):
        g = rng.choice(GROUPS)
        d = rng.choice(DAYS)
        sub = submit_times(rng, d, 1)[0]
        rejects.append([f"REJ-{d:%Y%m%d}-F{i:03d}", f"RAW-{d:%Y%m%d}-{g[3][0]}-F{i:03d}", iso_ms(sub), 1,
                        dest_masked(rng, g[0]), g[2][0], g[3][0], rng.choice(["0x00000058", "0x0000000B", "0x00000045"]),
                        "", "SUBMIT_FAILED", iso(sub + timedelta(minutes=17)), "false"])
    rejects.sort(key=lambda r: (r[2], r[0]))
    return rejects, rej_msgs


def contracted_rate(group, d, increase_from):
    if group == "MX-ALT" and d >= increase_from:
        return MX_NEW, DECK_PART
    return Decimal(OLD[group]), DECK_FULL


def gorder(g):
    return [x[0] for x in GROUPS].index(g)


def rating_lines(daily, increase_from):
    lines = []
    for (d, g, mm) in sorted(daily, key=lambda k: (k[0], gorder(k[1]), k[2])):
        msgs, segs = daily[(d, g, mm)][:2]
        rate, deck = contracted_rate(g, d, increase_from)
        lines.append([RUN, d.isoformat(), g, mm, deck, f"{rate}", msgs, segs, f"{q2(rate * segs)}", "USD"])
    return lines


def invoice_lines(daily, co_extra_msgs, co_extra_seg):
    """Carrier's view: bills all MX-ALT at the new rate (the planted error)."""
    def tot(g, a=P_START, b=P_END):
        n = s = 0
        for (d, gg, _mm), v in daily.items():
            if gg == g and a <= d <= b:
                n += v[0]
                s += v[1]
        return n, s
    mx = GL["MX-ALT"]
    lines = []
    n, s = tot("MX-ALT")
    lines.append([mx[1], mx[2], P_START, P_END, n, s, MX_NEW])
    for g, label, codes, _c, _cc, _nd in GROUPS[1:]:
        n, s = tot(g)
        if g == "CO-AND":
            n, s = n + co_extra_msgs, s + co_extra_seg
        lines.append([label, codes, P_START, P_END, n, s, Decimal(OLD[g])])
    return [{"line_no": i, "destination": l[0], "mcc_mnc": "|".join(l[1]), "service": "sms",
             "period_start": l[2].isoformat(), "period_end": l[3].isoformat(), "messages": l[4],
             "segments": l[5], "rate": f"{l[6]}", "amount": f"{q2(l[6] * l[5])}"}
            for i, l in enumerate(lines, 1)]


def write_invoice(root, lines):
    subtotal = f"{sum(Decimal(l['amount']) for l in lines)}"
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
    cols = ["line_no", "destination", "mcc_mnc", "service", "period_start", "period_end",
            "messages", "segments", "rate", "amount"]
    write_csv(os.path.join(base, "summary.csv"), cols, [[l[c] for c in cols] for l in lines])
    write_json(os.path.join(base, "envelope.json"), {
        "message_id": "<20261005085812.4411@billing.northbridge.example>",
        "from": "wholesale-billing@northbridge.example", "to": "settlements@orion-msg.example",
        "received_at": "2026-10-05T09:00:00Z",
        "subject": f"Invoice {INV} - September 2026 - account {ACCOUNT}",
        "attachments": ["invoice.json", "summary.csv", "invoice.md"]})
    md = [f"# INVOICE {INV}", "", f"**{SELLER['name']}** · VAT GB-VAT-998877665", "",
          f"Bill to: **{BUYER['name']}** · Account {ACCOUNT}  ",
          f"Agreement ref: {AGR_DOC_NO}  ", "Invoice date: 2026-10-05 · Due date: 2026-11-04  ",
          "Service: A2P SMS termination · Period: 2026-09-01 to 2026-09-30 (UTC) · Currency: USD", "",
          "| Line | Destination network | MCC-MNC | Period | Messages | Segments | Rate | Amount |",
          "|---:|---|---|---|---:|---:|---:|---:|"]
    for l in lines:
        md.append(f"| {l['line_no']} | {l['destination']} | {l['mcc_mnc'].replace('|', ', ')} | "
                  f"{l['period_start']} to {l['period_end']} | {l['messages']:,} | {l['segments']:,} | "
                  f"{l['rate']} | {Decimal(l['amount']):,.2f} |")
    md += ["", f"Subtotal: USD {Decimal(subtotal):,.2f}  ", "Tax: USD 0.00 (reverse charge)  ",
           f"**Total due: USD {Decimal(subtotal):,.2f}**", "",
           "Charged per message segment accepted by Northbridge (submit_sm_resp OK), whatever the delivery receipt. "
           "Payment due 30 days from invoice date. Disputes in writing within 14 days of invoice date per the "
           "agreement. Message detail records available on request."]
    write_text(os.path.join(base, "invoice.md"), "\n".join(md) + "\n")
    return Decimal(subtotal), sum(Decimal(l["amount"]) for l in lines)


def write_contract(root):
    clauses = [
        ("1.1", "Parties and term", "Agreement between NBCS (seller) and ORX (buyer), effective 2025-03-01, evergreen."),
        ("2.1", "SMPP connections", "Traffic is exchanged over the SMPP binds in Schedule 2; traffic on a new bind is chargeable from the date it carries traffic."),
        ("3.1", "Currency", "All charges in USD."),
        ("4.1", "Billing period", "Monthly; messages attributed to a period by submit time (submit_sm_resp) in UTC."),
        ("4.2", "Billing unit and basis", "Each message segment accepted by Seller (submit_sm_resp command_status 0) is chargeable, whatever its delivery receipt status."),
        ("4.3", "Charge rounding", "Line amount = segments x rate, rounded half-up to 2 decimals per invoice line."),
        ("4.4", "Rejected submissions", "Messages Seller rejects at submission (non-zero command_status) are not charged."),
        ("5.1", "Invoicing", "Invoice issued within 5 days after period end, with summary by destination network; message detail records on request."),
        ("5.2", "Payment", "Payment due 30 days from invoice date."),
        ("6.1", "Rate decks", "Seller prices from the NB-SMS rate deck family, one rate per destination network (MCC-MNC); decks may be full or partial."),
        ("6.2", "Change indicators", "Every changed row carries a change flag and effective date."),
        ("6.3", "Rate increase notice", "An increase or new destination takes effect no earlier than the first full UTC day starting at least 7 days after the notice is sent; until then the previous rate applies."),
        ("6.4", "Rate decreases", "A decrease takes effect on the stated effective date, with no notice period."),
        ("7.1", "Delivery receipts", "Seller forwards delivery receipts where the destination network returns them; receipts are for information and do not change charges."),
        ("8.1", "Dispute window", "Buyer may dispute in writing within 14 days of the invoice date."),
        ("8.2", "Minimum dispute", "Disputes below USD 50.00 per invoice are not accepted."),
        ("8.3", "Undisputed amounts", "Buyer pays the undisputed portion by the due date."),
        ("8.4", "Dispute evidence", "Notice states billed rate, correct rate, period, account ID and supporting message detail (supplier message IDs)."),
        ("8.5", "Escalation", "Disputes unresolved after 30 days escalate to commercial management."),
        ("8.6", "Credits", "Upheld disputes are credited on the next invoice."),
    ]
    write_json(os.path.join(root, "contracts", "agreements", f"{AGR}.json"), {
        "agreement_id": AGR, "document_number": AGR_DOC_NO,
        "name": "A2P SMS Termination Agreement", "agreement_type": "a2p_sms",
        "status": "active", "version": 2, "period_start": "2025-03-01", "period_end": None,
        "buyer_party": BUYER, "seller_party": SELLER, "currency": "USD", "billing_cycle": "monthly",
        "billing_timezone": "UTC", "billing_unit": "segment", "billing_basis": "submitted",
        "rounding": {"charge_decimals": 2, "rounding_mode": "half_up", "level": "invoice_line"},
        "rate_schedule_ref": "NB-SMS", "rate_notice_days_increase": 7, "rate_notice_days_decrease": 0,
        "invoice_due_days_after_period": 5, "payment_terms_days": 30, "dispute_window_days": 14,
        "dispute_min_amount": "50.00", "pay_undisputed_required": True,
        "dispute_evidence_required": ["billed_rate", "correct_rate", "period", "account_id", "message_detail"],
        "escalation_days": 30,
        "clauses": [{"clause_id": c, "title": t, "summary": s} for c, t, s in clauses],
        "amendments": ["AGR-NB-2025-014-A1"],
    })
    doc = [f"# A2P SMS Termination Agreement ({AGR_DOC_NO})", "",
           f"Internal ID {AGR}, version 2 (amendment A1 added Guatemala - Quetzal Movil, 2026-02-01). Fictional, prototype use only.",
           f"Between **{SELLER['name']}** (\"Seller\") and **{BUYER['name']}** (\"Buyer\"). Signed 2025-02-20.", ""]
    long = {
        "4.2": "4.2 **Billing unit and basis.** Charges are per message segment. A concatenated message counts one segment per part (153 GSM-7 or 67 UCS-2 characters per part). Every segment Seller accepts at submission (submit_sm_resp with command_status 0) is chargeable, whether or not a delivery receipt is later returned and whatever its status.",
        "6.3": "6.3 **Rate increase notice.** Seller shall give Buyer at least seven (7) days' written notice of any rate increase or new destination. An increase takes effect at 00:00 UTC on the first full day that begins at least seven (7) days after the notice is sent, or on the effective date stated in the deck if later. Where a deck states an earlier date, the previous rate continues to apply until the increase takes effect under this clause.",
        "8.1": "8.1 **Dispute window.** Buyer may dispute any invoice, in whole or in part, by written notice within fourteen (14) days of the invoice date.",
        "8.3": "8.3 **Undisputed amounts.** Buyer shall pay the undisputed portion of an invoice by its due date. Raising a dispute does not suspend payment of undisputed amounts.",
    }
    for c, t, s in clauses:
        doc += [f'<a id="clause-{c}"></a>', long.get(c, f"{c} **{t}.** {s}"), ""]
    write_text(os.path.join(root, "contracts", "documents", f"{AGR}.md"), "\n".join(doc))


DECK_COLS = ["mcc_mnc", "mcc", "mnc", "country", "destination", "network_group", "service", "rate", "currency",
             "effective_date", "end_date", "change_flag", "billing_unit"]


def deck_row(g, mm, rate, eff, flag):
    return [mm, mm[:3], mm[3:], COUNTRY[g[:2]], GL[g][1], g, "sms", rate, "USD", eff, "", flag, "segment"]


def write_rates(root, notice_sent):
    full = [deck_row(g, mm, OLD[g], "2026-08-01", "UNCHANGED") for g, _l, codes, *_ in GROUPS for mm in codes]
    write_csv(os.path.join(root, "rates", "decks", f"{DECK_FULL}.csv"), DECK_COLS, full)
    write_csv(os.path.join(root, "rates", "decks", f"{DECK_PART}.csv"), DECK_COLS,
              [deck_row("MX-ALT", "334990", f"{MX_NEW}", "2026-09-15", "INCREASE")])
    recv = notice_sent + timedelta(minutes=3)
    write_csv(os.path.join(root, "rates", "deck_history.csv"),
              ["deck_id", "rate_schedule_ref", "deck_type", "source_file", "received_at", "loaded_at",
               "loaded_by", "load_status", "total_rows", "min_effective_date", "max_effective_date", "notice_id"],
              [[DECK_FULL, "NB-SMS", "full", "NBCS_ORX_SMS_AZ_20260801.csv", "2026-07-23T16:20:00Z",
                "2026-07-24T10:05:00Z", "pricing.ops@orion-msg.example", "loaded", len(full), "2026-08-01", "2026-08-01", ""],
               [DECK_PART, "NB-SMS", "partial", "NBCS_ORX_SMS_PARTIAL_20260907.csv", iso(recv),
                iso(recv + timedelta(hours=2, minutes=11)), "pricing.ops@orion-msg.example", "loaded", 1,
                "2026-09-15", "2026-09-15", NOTICE]])
    write_json(os.path.join(root, "rates", "notices", f"{NOTICE}.json"), {
        "notice_id": NOTICE, "from_party": "NBCS", "sent_at": iso(notice_sent), "received_at": iso(recv),
        "channel": "email", "subject": "NBCS SMS rate notification: Mexico - Altavia Movil increase",
        "deck_id": DECK_PART,
        "summary_changes": [{"network_group": "MX-ALT", "mcc_mnc": "334990", "old_rate": OLD["MX-ALT"],
                             "new_rate": f"{MX_NEW}", "effective_date": "2026-09-15", "change_flag": "INCREASE"}]})
    write_text(os.path.join(root, "rates", "notices", f"{NOTICE}.eml.md"), "\n".join([
        "From: rates@northbridge.example", "To: pricing@orion-msg.example",
        f"Date: {notice_sent:%a, %d %b %Y %H:%M:%S} +0000",
        "Subject: NBCS SMS rate notification: Mexico - Altavia Movil increase",
        "Attachment: NBCS_ORX_SMS_PARTIAL_20260907.csv", "",
        "Dear partner,", "",
        "Please find attached a partial SMS rate deck for account " + ACCOUNT + ".",
        "Mexico - Altavia Movil (MCC-MNC 334-990) increases from USD 0.0120 to USD 0.0185 per message segment, "
        "effective 15 September 2026.",
        "All other networks are unchanged.", "",
        "Regards,", "Northbridge Carrier Services, Wholesale Messaging Pricing", ""]))


def write_usage_tables(root, daily, rejects):
    summ = {}
    for (d, g, _mm), v in daily.items():
        s = summ.setdefault((d, g), [0] * 6 + [set()])
        for i in range(6):
            s[i] += v[i]
        s[6].update(f"MED-{d:%Y%m%d}-{c}" for c in GL[g][3])
    write_csv(os.path.join(root, "usage", "daily_summary.csv"),
              ["date_utc", "supplier_id", "network_group", "messages", "billable_segments", "delivered",
               "undelivered", "expired", "no_receipt", "source_batch_ids"],
              [[d.isoformat(), CARRIER, g] + s[:6] + ["|".join(sorted(s[6]))]
               for (d, g), s in sorted(summ.items(), key=lambda kv: (kv[0][0], gorder(kv[0][1])))])
    write_csv(os.path.join(root, "usage", "rejects.csv"), REJ_COLS, rejects)
    cm = [[f"SMPP-NB-0{i}", f"orx_nb_0{i}", CARRIER, "egress", "2025-03-01", ""] for i in range(1, 7)]
    cm[4][4] = "2026-02-01"  # Guatemala bind added with amendment A1
    cm.append(["SMPP-NB-07", "orx_nb_07", CARRIER, "egress", "2026-10-01", ""])
    cm.append(["SMPP-PR-01", "orx_pr_01", "PRTE", "egress", "2025-06-01", ""])
    write_csv(os.path.join(root, "usage", "connection_map.csv"),
              ["connection_id", "system_id", "supplier_id", "direction", "valid_from", "valid_to"], cm)


def write_rating(root, lines):
    write_json(os.path.join(root, "rating", "runs", f"{RUN}.json"), {
        "run_id": RUN, "period_start": "2026-09-01", "period_end": "2026-09-30", "carrier_id": CARRIER,
        "agreement_id": AGR, "agreement_version": 2, "decks_used": [DECK_FULL, DECK_PART],
        "run_at": "2026-10-01T04:30:00Z", "engine_version": "orx-rate 3.4.1", "status": "complete"})
    write_csv(os.path.join(root, "rating", "runs", RUN, "lines.csv"),
              ["run_id", "date_local", "network_group", "mcc_mnc", "deck_id", "rate", "messages",
               "billable_segments", "amount", "currency"], lines)
    return sum(Decimal(l[8]) for l in lines)


def write_empty_stores(root):
    for p in ["cases", "ledger", "carrier-portal/submissions", "carrier-portal/responses"]:
        write_text(os.path.join(root, p, ".keep"), "")


def scenario_yaml(sid, data_root, expected):
    exp = "\n".join(f"  {k}: {json.dumps(v)}" for k, v in expected.items())
    portal = ""
    if expected.get("decision") in ("partial_dispute", "full_dispute"):
        credit = expected["supported"]
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
events:
  - at: 2026-10-05T09:00:00Z
    action: deliver_invoice
    invoice_id: {INV}
{portal}expected:
{exp}
"""


def main():
    rng = random.Random(SEED)
    for p in (SIM, KEYS):
        shutil.rmtree(p, ignore_errors=True)
    base = os.path.join(SIM, "base")
    units, dup_days = build_base(rng)
    sep15 = date(2026, 9, 15)

    # Messages are written one day at a time; keep only the duplicate sample.
    daily, dup_sample, n_msgs = {}, [], 0
    for d in DAYS:
        rows, dd = gen_day(rng, d, units)
        daily.update(dd)
        n_msgs += len(rows)
        write_csv_gz(os.path.join(base, "usage", "messages", f"{d}.csv.gz"), MSG_COLS, rows)
        dup_sample += rng.sample(rows, dup_days.count(d))
    rejects, rej_msgs = build_rejects(rng, dup_sample)

    write_contract(base)
    write_rates(base, datetime(2026, 9, 7, 14, 0, tzinfo=UTC))
    write_usage_tables(base, daily, rejects)
    expected = write_rating(base, rating_lines(daily, sep15))
    inv_lines = invoice_lines(daily, len(rej_msgs), REJ_CO_SEG)
    invoiced, _ = write_invoice(base, inv_lines)
    write_empty_stores(base)
    assert expected == Decimal("25000.00") and invoiced == Decimal("27000.00"), (expected, invoiced)

    mx_early = sum(v[1] for (d, g, _m), v in daily.items() if g == "MX-ALT" and d < sep15)
    supported = q2(mx_early * (MX_NEW - Decimal(OLD["MX-ALT"])))
    assert supported == Decimal("1820.00")
    dlr_tot = [sum(v[2 + i] for v in daily.values()) for i in range(4)]

    def pct(a, b):
        return f"{(a / b * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"

    mx_cites = ["sim://invoices/invoice/%s/line/1" % INV, f"sim://rates/deck/{DECK_PART}/row/334990",
                f"sim://rates/deck/{DECK_FULL}/row/334990", f"sim://rates/notice/{NOTICE}",
                f"sim://contracts/agreement/{AGR}#clause-6.3"] + \
               [f"sim://usage/daily/2026-09-{d:02d}/MX-ALT" for d in range(1, 15)]
    co_rej_ids = [r[0] for r in rejects if r[9] == "UNMAPPED_CONNECTION"]
    keys = {}
    common_dq = {
        "duplicate_supplier_msg_ids_in_usage": 0,
        "rejects": {"UNMAPPED_CONNECTION": {"count": len(rej_msgs), "billable_segments": REJ_CO_SEG,
                                            "smpp_connection": "SMPP-NB-07", "dates": "2026-09-21..2026-09-30",
                                            "billable": True,
                                            "note": "connection_map adds SMPP-NB-07 only from 2026-10-01"},
                    "DUPLICATE": {"count": 25, "billable": False, "note": "correctly dropped; not an explanation"},
                    "SUBMIT_FAILED": {"count": 60, "billable": False,
                                      "note": "rejected by the carrier at submit (clause 4.4); correctly dropped"}},
        "delivery_receipts": {"delivered": dlr_tot[0], "undelivered": dlr_tot[1], "expired": dlr_tot[2],
                              "no_receipt": dlr_tot[3],
                              "note": "billing is on submit (clause 4.2); undelivered messages are billable"},
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
            {"invoice_line": 1, "network_group": "MX-ALT", "exception_type": "RATE_BEFORE_EFFECTIVE",
             "invoiced": "11100.00", "expected": "9280.00", "variance": "1820.00",
             "detail": f"{mx_early} segments on 2026-09-01..14 billed at 0.0185; contracted 0.0120"},
            {"invoice_line": 3, "network_group": "CO-AND", "exception_type": "VOLUME",
             "invoiced": "4680.00", "expected": "4500.00", "variance": "180.00",
             "detail": f"carrier 312000 segments vs our 300000; +{len(rej_msgs)} messages"}],
        "step3_matches": ["line 2 MX-VRD 2400.00", "line 4 BR-CRZ 5250.00", "line 5 GT-QTZ 3570.00"],
        "step4_causes": [
            {"group": "MX-ALT", "claim_class": ["fact", "calculation"],
             "statement": "Increase noticed 2026-09-07T14:00Z is effective 2026-09-15 under clause 6.3 and the deck row; carrier applied it from 2026-09-01.",
             "cites": mx_cites},
            {"group": "CO-AND", "claim_class": ["fact"],
             "statement": f"Mediation rejected {len(rej_msgs)} CO-AND messages / 12000 billable segments on SMPP-NB-07 (UNMAPPED_CONNECTION, not reprocessed); the carrier charge is legitimate and our expected is understated.",
             "cites": ["sim://invoices/invoice/%s/line/3" % INV, "sim://usage/connection_map/SMPP-NB-07"] +
                      [f"sim://usage/reject/{r}" for r in co_rej_ids[:3]] +
                      [f"... all {len(rej_msgs)} UNMAPPED_CONNECTION rejects"]}],
        "step5_calcs": [
            {"name": "supported_mx_alt", "formula": "280000 x (0.0185 - 0.0120)", "result": "1820.00"},
            {"name": "co_and_explained", "formula": "12000 x 0.0150", "result": "180.00"},
            {"name": "undisputed_payable", "formula": "27000.00 - 1820.00", "result": "25180.00"}],
        "step6_decision": {"decision": "partial_dispute", "amount": "1820.00",
                           "why": ["supported 1820.00 >= dispute_min_amount 50.00 (clause 8.2)",
                                   "unresolved 0.00 <= 1% of invoiced",
                                   "CO-AND 180.00 is our own mediation fault; pay it and fix connection_map"],
                           "internal_actions": [f"add SMPP-NB-07 to connection_map from 2026-09-21 and reprocess the {len(rej_msgs)} rejects"]},
        "step7_submission": {"reason_code": "RATE_BEFORE_EFFECTIVE", "product": "A2P SMS - Mexico - Altavia Movil (334-990)",
                             "bill_period": "2026-09", "account_id": ACCOUNT, "invoice_id": INV,
                             "disputed_amount": "1820.00", "billed_rate": "0.0185", "correct_rate": "0.0120",
                             "from_date": "2026-09-01", "through_date": "2026-09-14"},
        "must_not": ["dispute the CO-AND 180.00", "treat DUPLICATE or SUBMIT_FAILED rejects as billable",
                     "dispute undelivered, expired or receipt-less messages (billing is on submit, clause 4.2)",
                     "state any amount that is not a rules-actor calc"],
    }
    write_text(os.path.join(base, "scenario.yaml"), scenario_yaml("base", "sim-data/base",
               {"case_opened": True, "supported": "1820.00", "unresolved": "0.00", "decision": "partial_dispute"}))

    for k, v in keys.items():
        write_json(os.path.join(KEYS, f"{k}.json"), v)
    print(json.dumps({"base_expected": str(expected), "base_invoiced": str(invoiced),
                      "messages": n_msgs, "rejects": len(rejects), "co_reject_messages": len(rej_msgs)}, indent=1))


if __name__ == "__main__":
    main()

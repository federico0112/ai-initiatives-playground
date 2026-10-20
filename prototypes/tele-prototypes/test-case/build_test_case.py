"""Build one end-to-end A2P SMS test case for Prototype 1 with raw files for every external system S1-S7.

Story: Northbridge SMS invoice NB-INV-2026-09-0412 (USD 27,000.00) vs expected 25,000.00.
Every figure is read from ../dataset/sim-data/base (run ../dataset/generate_dataset.py first)
and S5-S7 documents come from ../raw-examples/out (run ../raw-examples/build_examples.py first).

The bulk files are written at industry size:
  S2  full A-Z SMS rate deck (one row per network MCC-MNC, ~1,000 rows)
  S3  30 daily mediation message files (~1.35M messages with delivery receipts) + full reject log
  S5  carrier message detail (MDR) for the whole month, 4 gzipped pipe-delimited files

Run:  python3 build_test_case.py      -> writes ./out/ (everything) and "./P1 SMS Test Case - large files.zip" (files > 25 KB)
Stdlib only, deterministic.
"""
import base64
import csv
import gzip
import hashlib
import io
import json
import random
import shutil
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "dataset" / "sim-data" / "base"
RAW = HERE.parent / "raw-examples" / "out"
KEYS = HERE.parent / "dataset" / "answer-keys"
OUT = HERE / "out"
ZIP = HERE / "P1 SMS Test Case - large files.zip"
BIG = 25_000  # files above this size cannot reliably go through the text-only Dropbox connector; they ship in ZIP
sys.path.insert(0, str(HERE.parent / "raw-examples"))
from build_examples import Pdf  # noqa: E402  minimal ASCII PDF writer

S1, S2, S3, S4 = "S1 Contract repository", "S2 Rate deck store", "S3 Usage store (mediation)", "S4 Rated charges"
S5, S6, S7, AK = "S5 Carrier invoice inbox", "S6 Case store", "S7 Carrier dispute", "Answer key (keep away from agents)"

ACCOUNT, DOC_NO, AGR, INV = "NBCS-ACC-30988", "NBCS-SMS-2025-0117", "AGR-NB-2025-014", "NB-INV-2026-09-0412"
BUYER, DOM = "Orion Messaging Exchange", "orion-msg.example"
BST = timedelta(hours=1)  # London local time used on Northbridge message detail (September = BST)
# our MCC-MNC -> (network group, carrier label, invoice rate); the carrier billed all Altavia at the new rate
NETS = {"334990": ("MX-ALT", "Mexico - Altavia Movil", Decimal("0.0185")),
        "334991": ("MX-VRD", "Mexico - Verdemovil", Decimal("0.0060")),
        "732990": ("CO-AND", "Colombia - Andina Movil", Decimal("0.0150")),
        "724990": ("BR-CRZ", "Brazil - Cruzeiro Movel", Decimal("0.0210")),
        "724991": ("BR-CRZ", "Brazil - Cruzeiro Movel", Decimal("0.0210")),
        "704990": ("GT-QTZ", "Guatemala - Quetzal Movil", Decimal("0.0850"))}
OLD = {"MX-ALT": "0.0120", "MX-VRD": "0.0060", "CO-AND": "0.0150", "BR-CRZ": "0.0210", "GT-QTZ": "0.0850"}
SENDERS = ["BLITORAL", "RAPIDOENV", "MERCADOSOL", "AEROPAMPA", "SALUDMAS", "PAGOFACIL"]


def write(rel, data):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("ascii")
    p.write_bytes(data)
    return data


def gz(data):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as g:
        g.write(data)
    return buf.getvalue()


def csv_text(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def read_csv(rel):
    with open(BASE / rel, newline="") as f:
        return list(csv.DictReader(f))


def wrap(s, n):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return lines + [cur] if cur else lines


# ======================= S1 contract repository =======================
CONTRACT_TEXT = [
    ("1", "DEFINITIONS AND TERM", [
        ("1.1", "Parties and term", "This Agreement is made between Northbridge Carrier Services Ltd (\"Seller\", party NBCS) and Orion Messaging Exchange S.A. (\"Buyer\", party ORX). It takes effect on 1 March 2025 and continues until terminated by either party on ninety (90) days' written notice."),
        ("1.2", "Definitions", "\"Rate Deck\" means a file issued by Seller listing destination networks by MCC-MNC, rates per message segment and effective dates. \"Segment\" means one part of a message: up to 160 GSM-7 or 70 UCS-2 characters for a single message, 153 or 67 per part of a concatenated message. \"Business Day\" means a day other than Saturday, Sunday or a public holiday in England."),
        ("1.3", "Service", "Seller terminates application-to-person (A2P) SMS traffic submitted by Buyer over the SMPP connections listed in Schedule 2 to the destination networks in the current Rate Deck."),
    ]),
    ("2", "INTERCONNECTION", [
        ("2.1", "SMPP connections", "Each party shall maintain the SMPP v3.4 binds set out in Schedule 2. New binds are added by written connection order; traffic on a new bind is chargeable from the date it carries traffic."),
        ("2.2", "Quality", "Seller shall use reasonable endeavours to maintain delivery rates and delivery receipt latency in line with industry practice for each destination network."),
        ("2.3", "Content and fraud", "Buyer shall not submit unsolicited, unlawful or artificially inflated traffic. Each party shall notify the other without delay of suspected SMS fraud."),
    ]),
    ("3", "CHARGES", [
        ("3.1", "Currency", "All charges are stated and invoiced in United States dollars (USD)."),
        ("3.2", "Taxes", "Charges exclude VAT and similar taxes, which are added where applicable. International wholesale SMS termination is outside the scope of UK VAT."),
    ]),
    ("4", "BILLING", [
        ("4.1", "Billing period", "The billing period is the calendar month. Messages are attributed to a billing period by their submit time (submit_sm_resp) in UTC."),
        ("4.2", "Billing unit and basis", "Charges are per message segment. Every segment Seller accepts at submission (submit_sm_resp with command_status 0) is chargeable, whether or not a delivery receipt is later returned and whatever its status."),
        ("4.3", "Charge rounding", "The amount of each invoice line equals the segments multiplied by the rate, rounded half-up to two (2) decimals per invoice line."),
        ("4.4", "Rejected submissions", "Messages Seller rejects at submission (non-zero command_status) are not charged."),
    ]),
    ("5", "INVOICING AND PAYMENT", [
        ("5.1", "Invoicing", "Seller shall issue an invoice within five (5) days after the end of each billing period, with a summary by destination network. Message detail records (MDR) with Seller's message IDs are supplied on request within five (5) Business Days."),
        ("5.2", "Payment", "Buyer shall pay each invoice within thirty (30) days of the invoice date."),
        ("5.3", "Late payment", "Undisputed amounts paid late bear interest at 2% above the Bank of England base rate."),
    ]),
    ("6", "RATES", [
        ("6.1", "Rate decks", "Seller prices from the NB-SMS rate deck family, one rate per destination network (MCC-MNC). Seller may issue full (A-Z) or partial Rate Decks by email to Buyer's pricing contact."),
        ("6.2", "Change indicators", "Every changed row in a Rate Deck shall carry a change indicator (New, Increase, Decrease, Unchanged, Closed) and an effective date."),
        ("6.3", "Rate increase notice", "Seller shall give Buyer at least seven (7) days' written notice of any rate increase or new destination. An increase takes effect at 00:00 UTC on the first full day that begins at least seven (7) days after the notice is sent, or on the effective date stated in the deck if later. Where a deck states an earlier date, the previous rate continues to apply until the increase takes effect under this clause."),
        ("6.4", "Rate decreases", "A decrease takes effect on the stated effective date, with no notice period."),
    ]),
    ("7", "DELIVERY RECEIPTS AND LIABILITY", [
        ("7.1", "Delivery receipts", "Seller forwards delivery receipts (DLR) where the destination network returns them. Receipts are for information and do not change charges."),
        ("7.2", "Limitation", "Neither party is liable for indirect or consequential loss. Each party's total liability in a contract year is limited to the charges paid in that year."),
    ]),
    ("8", "DISPUTES", [
        ("8.1", "Dispute window", "Buyer may dispute any invoice, in whole or in part, by written notice within fourteen (14) days of the invoice date."),
        ("8.2", "Minimum dispute", "Disputes below USD 50.00 per invoice are not accepted."),
        ("8.3", "Undisputed amounts", "Buyer shall pay the undisputed portion of an invoice by its due date. Raising a dispute does not suspend payment of undisputed amounts."),
        ("8.4", "Dispute evidence", "A dispute notice shall be made on Seller's Dispute Notification Form and state the billed rate, the correct rate, the period, Buyer's account ID and supporting message detail with Seller's message IDs."),
        ("8.5", "Escalation", "Disputes unresolved after thirty (30) days escalate to commercial management of both parties."),
        ("8.6", "Credits", "Upheld disputes are credited by credit note against the next invoice."),
    ]),
    ("9", "GENERAL", [
        ("9.1", "Notices", "Notices are given by email to the contacts in Schedule 3 and are effective when sent."),
        ("9.2", "Governing law", "This Agreement is governed by the laws of England and Wales."),
        ("9.3", "Entire agreement", "This Agreement, its Schedules and any signed amendment form the entire agreement between the parties."),
    ]),
]


def s1_contract_pdf():
    p = Pdf(DOC_NO + " A2P SMS Termination Agreement")
    state = {"y": 0, "n": 0}

    def new_page():
        p.page()
        state["n"] += 1
        p.text(40, 805, DOC_NO + "  A2P SMS Termination Agreement  -  CONFIDENTIAL", 7)
        p.line(40, 798, 555, 798, 0.4)
        p.line(40, 60, 555, 60, 0.4)
        p.text(40, 48, "Initials: Seller ____  Buyer ____", 7)
        state["y"] = 770

    def para(s, size=9, f="R", indent=0, gap=4):
        for ln in wrap(s, int(92 * 9 / size) - indent // 5):
            if state["y"] < 80:
                new_page()
            p.text(40 + indent, state["y"], ln, size, f)
            state["y"] -= size + 3
        state["y"] -= gap

    new_page()
    p.text(297 - 140, 690, "A2P SMS TERMINATION AGREEMENT", 15, "B")
    p.text(297 - 60, 665, "Ref. " + DOC_NO, 10)
    for i, s in enumerate(["between", "NORTHBRIDGE CARRIER SERVICES LTD", "12 Harbour Exchange Square, London E14 9GE, United Kingdom",
                           "Company No. 08812345", "", "and", "ORION MESSAGING EXCHANGE S.A.",
                           "Av. Corrientes 1200, C1043 Buenos Aires, Argentina", "CUIT 30-71234567-8", "",
                           "Dated 20 February 2025"]):
        p.text(150, 600 - i * 16, s, 10, "B" if s.isupper() else "R")
    p.text(150, 380, "Internal reference (Buyer CLM): " + AGR, 8)
    p.text(150, 368, "Amendment A1 (Guatemala - Quetzal Movil, 1 February 2026) attached separately.", 8)
    p.text(150, 340, "All parties, networks, MCC-MNC codes and figures are fictional (prototype test data).", 7)
    new_page()
    for num, title, clauses in CONTRACT_TEXT:
        para("%s. %s" % (num, title), 10, "B", gap=2)
        for cid, ct, body in clauses:
            para("%s  %s. %s" % (cid, ct, body), indent=10)
    para("SCHEDULE 1 - RATES", 10, "B", gap=2)
    para("Rates are those in the most recent Rate Deck of the NB-SMS family loaded under clause 6. "
         "At signature the following destination networks were priced (USD per message segment):", indent=10)
    for row in [("Mexico - Altavia Movil", "334-990", "0.0120"), ("Mexico - Verdemovil", "334-991", "0.0060"),
                ("Colombia - Andina Movil", "732-990", "0.0150"), ("Brazil - Cruzeiro Movel", "724-990", "0.0210"),
                ("Brazil - Cruzeiro Movel (legacy MNC)", "724-991", "0.0210")]:
        if state["y"] < 80:
            new_page()
        p.text(70, state["y"], row[0], 9, "M")
        p.text(330, state["y"], row[1], 9, "M")
        p.text(420, state["y"], row[2], 9, "M")
        state["y"] -= 12
    state["y"] -= 6
    para("SCHEDULE 2 - SMPP CONNECTIONS", 10, "B", gap=2)
    para("Host smpp.northbridge.example port 2775 (TLS). Binds (system_id): orx_nb_01 and orx_nb_02 (Mexico - Altavia Movil), "
         "orx_nb_03 (Colombia), orx_nb_04 (Brazil), orx_nb_05 (Guatemala, from Amendment A1), orx_nb_06 (Mexico - Verdemovil). "
         "Bind type transceiver, window size 10, throughput 200 messages per second per bind.", indent=10)
    para("SCHEDULE 3 - CONTACTS", 10, "B", gap=2)
    for s in ["Seller billing: wholesale-billing@northbridge.example", "Seller rates: rates@northbridge.example",
              "Seller disputes: disputes@northbridge.example", "Buyer settlements: settlements@" + DOM,
              "Buyer pricing: pricing@" + DOM]:
        para(s, indent=10, gap=0)
    new_page()
    state["y"] = 700
    para("SIGNED for and on behalf of the parties:", 10, "B", gap=20)
    for x, who in [(40, "NORTHBRIDGE CARRIER SERVICES LTD"), (310, "ORION MESSAGING EXCHANGE S.A.")]:
        p.text(x, 640, who, 9, "B")
        p.text(x, 600, "/s/ ________________________", 9)
        p.text(x, 585, "Name: " + ("Harriet Cole" if x == 40 else "Martin Ibarra"), 9)
        p.text(x, 572, "Title: " + ("Director, Wholesale Messaging" if x == 40 else "Head of Carrier Relations"), 9)
        p.text(x, 559, "Date: 20 February 2025", 9)
    for i, ops in enumerate(p.pages, 1):
        p.ops = ops
        p.text(555, 48, "Page %d of %d" % (i, len(p.pages)), 7, right=True)
    return write(S1 + "/01_%s_A2P_SMS_Agreement_signed.pdf" % DOC_NO, p.bytes())


def s1_amendment_pdf():
    p = Pdf(DOC_NO + "-A1 Amendment 1")
    p.page()
    p.text(40, 790, "AMENDMENT No. 1 TO A2P SMS TERMINATION AGREEMENT " + DOC_NO, 11, "B")
    p.line(40, 780, 555, 780)
    y = 755
    for s in ["Between Northbridge Carrier Services Ltd and Orion Messaging Exchange S.A.", "",
              "1. With effect from 1 February 2026 Schedule 1 is extended with:",
              "      Guatemala - Quetzal Movil    704-990    USD 0.0850 per segment",
              "2. Schedule 2 is extended with SMPP bind orx_nb_05 for Guatemala traffic.",
              "3. All other terms of the Agreement remain unchanged.", "",
              "Buyer CLM reference: AGR-NB-2025-014-A1 (agreement version 2).", "",
              "Signed: /s/ Harriet Cole (Seller)   /s/ Martin Ibarra (Buyer)   26 January 2026"]:
        p.text(40, y, s, 9, "M" if s.startswith("      ") else "R")
        y -= 15
    p.text(40, 48, "Fictional company, prototype data only", 7)
    return write(S1 + "/02_%s-A1_Amendment_Guatemala.pdf" % DOC_NO, p.bytes())


def s1_clm():
    agr = json.loads((BASE / "contracts/agreements/AGR-NB-2025-014.json").read_text())
    export = {"_source": "CLM export, GET /api/v2/agreements/AGR-NB-2025-014 (Buyer contract repository)",
              "exported_at": "2026-10-05T09:02:11Z", "agreement": agr,
              "documents": [{"name": "01_%s_A2P_SMS_Agreement_signed.pdf" % DOC_NO, "type": "signed_contract"},
                            {"name": "02_%s-A1_Amendment_Guatemala.pdf" % DOC_NO, "type": "amendment"}]}
    write(S1 + "/03_clm_agreement_AGR-NB-2025-014.json", json.dumps(export, indent=2) + "\n")
    rnd = random.Random(14)
    carriers = ["Altavia Telecom", "Bluewave Carrier", "Cordillera Networks", "Delta Messaging Partners", "Equinoxe Telecom",
                "Fjord Interconnect", "Granite Telecom", "Helios Wholesale", "Isthmus Carrier", "Jade Global Messaging",
                "Kestrel Networks", "Lumora Telecom", "Meridian Interconnect", "Nimbus Carrier", "Oceanic Messaging",
                "Pampas Telecom", "Quetzal Networks", "Riverside Carrier", "Solara Wholesale", "Tundra Telecom",
                "Umbra Interconnect", "Vireo Messaging", "Westgate Carrier", "Xenon Telecom", "Yucatan Networks", "Zephyr SMS"]
    rows = [[AGR, DOC_NO, "NBCS", "Northbridge Carrier Services", "a2p_sms", "active", 2, "2025-03-01", "",
             "USD", "monthly", "segment", "submitted", 7, 14, 30, "50.00", "NB-SMS", "c.ramos@" + DOM]]
    for c in carriers:
        cid = "".join(w[0] for w in c.split())[:2].upper() + c.split()[0][2:4].upper()
        typ = rnd.choice(["a2p_sms"] * 3 + ["wholesale_voice"])
        start = datetime(2023, 1, 1) + timedelta(days=rnd.randrange(1000))
        status = rnd.choice(["active"] * 5 + ["expired"])
        sms = typ == "a2p_sms"
        rows.append(["AGR-%s-%d-%03d" % (cid[:2], start.year, rnd.randrange(1, 60)),
                     "%s-%s-%d-%04d" % (cid, "SMS" if sms else "WVT", start.year, rnd.randrange(1, 999)),
                     cid, c, typ, status, rnd.randint(1, 4), start.strftime("%Y-%m-%d"),
                     (start + timedelta(days=730)).strftime("%Y-%m-%d") if status == "expired" else "",
                     rnd.choice(["USD", "USD", "EUR"]), "monthly", "segment" if sms else "60/60",
                     rnd.choice(["submitted", "submitted", "delivered"]) if sms else "connected",
                     rnd.choice([0, 7, 14, 30]), rnd.choice([14, 30, 45, 60]), rnd.choice([15, 30, 45]),
                     rnd.choice(["0.00", "50.00", "100.00", "250.00"]), cid + ("-SMS" if sms else "-VOICE"),
                     rnd.choice(["c.ramos", "l.mendez", "p.ortiz"]) + "@" + DOM])
    write(S1 + "/04_clm_contract_register.csv", csv_text(
        ["agreement_id", "document_number", "carrier_id", "carrier_name", "agreement_type", "status", "version", "period_start",
         "period_end", "currency", "billing_cycle", "billing_unit", "billing_basis", "rate_notice_days_increase",
         "dispute_window_days", "payment_terms_days", "dispute_min_amount", "rate_schedule_ref", "owner"], rows))


# ======================= S2 rate deck store =======================
COUNTRIES = """Afghanistan 412;Albania 276;Algeria 603;Andorra 213;Angola 631;Argentina 722;Armenia 283;Australia 505;Austria 232;
Azerbaijan 400;Bahrain 426;Bangladesh 470;Belarus 257;Belgium 206;Bolivia 736;Bosnia and Herzegovina 218;Botswana 652;
Bulgaria 284;Cambodia 456;Cameroon 624;Canada 302;Chile 730;China 460;Costa Rica 712;Croatia 219;Cuba 368;Cyprus 280;
Czech Republic 230;Denmark 238;Dominican Republic 370;Ecuador 740;Egypt 602;El Salvador 706;Estonia 248;Ethiopia 636;
Finland 244;France 208;Georgia 282;Germany 262;Ghana 620;Greece 202;Honduras 708;Hong Kong 454;Hungary 216;Iceland 274;
India 404;Indonesia 510;Iraq 418;Ireland 272;Israel 425;Italy 222;Ivory Coast 612;Jamaica 338;Japan 440;Jordan 416;
Kazakhstan 401;Kenya 639;Kuwait 419;Latvia 247;Lebanon 415;Lithuania 246;Luxembourg 270;Malaysia 502;Malta 278;Moldova 259;
Morocco 604;Mozambique 643;Myanmar 414;Nepal 429;Netherlands 204;New Zealand 530;Nicaragua 710;Nigeria 621;Norway 242;
Oman 422;Pakistan 410;Panama 714;Paraguay 744;Peru 716;Philippines 515;Poland 260;Portugal 268;Qatar 427;Romania 226;
Russia 250;Saudi Arabia 420;Senegal 608;Serbia 220;Singapore 525;Slovakia 231;Slovenia 293;South Africa 655;South Korea 450;
Spain 214;Sri Lanka 413;Sweden 240;Switzerland 228;Taiwan 466;Tanzania 640;Thailand 520;Tunisia 605;Turkey 286;Uganda 641;
Ukraine 255;United Arab Emirates 424;United Kingdom 234;United States 310;Uruguay 748;Venezuela 734;Vietnam 452;Zambia 645;
Zimbabwe 648"""
OP_A = ["Alta", "Bravo", "Celeste", "Delta", "Estrella", "Faro", "Global", "Horizonte", "Iris", "Jaguar", "Kappa", "Luna",
        "Maya", "Nova", "Orbit", "Pulse", "Quanta", "Rio", "Sol", "Terra", "Union", "Vista", "Wave", "Zenit"]
OP_B = ["Mobile", "Telecom", "Cell", "Wireless", "Connect", "Net", "Link", "One", "Movil", "Tel"]
SAFE = {  # our six networks plus fictional neighbours in the same countries
    "Mexico": [("Mexico - Altavia Movil", "334990", "0.0120"), ("Mexico - Verdemovil", "334991", "0.0060"),
               ("Mexico - Faro Movil (MVNO)", "334992", "0.0145")],
    "Colombia": [("Colombia - Andina Movil", "732990", "0.0150"), ("Colombia - Caribe Cel", "732991", "0.0128")],
    "Brazil": [("Brazil - Cruzeiro Movel", "724990", "0.0210"), ("Brazil - Cruzeiro Movel", "724991", "0.0210"),
               ("Brazil - Ipe Telecom", "724992", "0.0188"), ("Brazil - Sabia Cel", "724993", "0.0241")],
    "Guatemala": [("Guatemala - Quetzal Movil", "704990", "0.0850"), ("Guatemala - Ceiba Tel", "704991", "0.0790")],
}


def az_rows():
    rnd = random.Random(801)
    rows = []
    countries = [c.strip().rsplit(" ", 1) for c in COUNTRIES.replace("\n", "").split(";")]
    for name, mcc in sorted(countries + [["Mexico", "334"], ["Colombia", "732"], ["Brazil", "724"], ["Guatemala", "704"]]):
        if name in SAFE:
            for dest, mm, rate in SAFE[name]:
                rows.append((name, dest, mm[:3], mm[3:], Decimal(rate), "Unchanged"))
            continue
        size = rnd.choice([2, 3, 4, 5, 6, 8, 10, 14, 20])
        base_rate = rnd.uniform(0.004, 0.09)
        used = set()
        mnc_len = 3 if name in ("United States", "Canada", "India") else 2
        for i in range(size):
            for _ in range(50):
                mnc = "%0*d" % (mnc_len, rnd.randint(1, 10 ** mnc_len - 1 if mnc_len == 2 else 899))
                if mnc not in used:
                    break
            used.add(mnc)
            dest = "%s - %s %s" % (name, rnd.choice(OP_A), rnd.choice(OP_B)) + (" (MVNO)" if i >= 3 and rnd.random() < 0.3 else "")
            rate = Decimal(str(round(base_rate * rnd.uniform(0.7, 1.6), 4)))
            flag = rnd.choices(["Unchanged", "Increase", "Decrease", "New"], [88, 5, 5, 2])[0]
            rows.append((name, dest, mcc, mnc, rate, flag))
    return rows


DECK_HEAD = ["Country", "Network", "MCC", "MNC", "MCC-MNC", "Rate (USD/SMS)", "Effective Date", "Change", "Billing Unit"]


def s2():
    rows = az_rows()
    head = ("Northbridge Carrier Services Ltd - A2P SMS Rate Deck (A-Z)\n"
            "Customer: Orion Messaging Exchange S.A.,Account: %s,Product: A2P SMS Premium Direct\n"
            "Currency: USD,Billing: per segment submitted,Issued: 23/07/2026\n\n" % ACCOUNT)
    body = csv_text(DECK_HEAD, [(c, d, mcc, mnc, mcc + mnc, "%.4f" % r, "01/08/2026", f, "Segment")
                                for c, d, mcc, mnc, r, f in rows])
    az = write(S2 + "/02_NBCS_ORX_SMS_AZ_20260801.csv", head + body)
    part = ("Northbridge Carrier Services Ltd - A2P SMS Rate Deck (PARTIAL - changes only)\n"
            "Customer: Orion Messaging Exchange S.A.,Account: %s,Product: A2P SMS Premium Direct\n"
            "Currency: USD,Billing: per segment submitted,Issued: 07/09/2026\n\n" % ACCOUNT
            + csv_text(DECK_HEAD, [("Mexico", "Mexico - Altavia Movil", "334", "990", "334990", "0.0185", "15/09/2026",
                                    "Increase", "Segment")]))
    part_b = write(S2 + "/03_NBCS_ORX_SMS_PARTIAL_20260907.csv", part)
    b64 = base64.encodebytes(part_b).decode()
    eml = """Return-Path: <rates@northbridge.example>
Message-ID: <20260907140000.7731@mail.northbridge.example>
Date: Mon, 07 Sep 2026 14:00:00 +0000
From: Northbridge Wholesale Messaging Pricing <rates@northbridge.example>
To: pricing@{dom}
Subject: NBCS SMS rate notification: Mexico - Altavia Movil increase - account {acc}
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="=_nbcs_rn_0907"

--=_nbcs_rn_0907
Content-Type: text/plain; charset=us-ascii

Dear partner,

Please find attached a partial A2P SMS rate deck for account {acc}.

  Mexico - Altavia Movil (MCC-MNC 334-990): USD 0.0120 -> USD 0.0185 per segment
  Effective date: 15/09/2026

All other networks remain unchanged. This notice is sent under
clause 6 of agreement {doc}.

Regards,
Northbridge Carrier Services, Wholesale Messaging Pricing
rates@northbridge.example

--=_nbcs_rn_0907
Content-Type: text/csv; name="NBCS_ORX_SMS_PARTIAL_20260907.csv"
Content-Disposition: attachment; filename="NBCS_ORX_SMS_PARTIAL_20260907.csv"
Content-Transfer-Encoding: base64

{b64}
--=_nbcs_rn_0907--
""".format(acc=ACCOUNT, b64=b64, dom=DOM, doc=DOC_NO)
    write(S2 + "/01_email_rate_notice_RN-NB-2026-0907.eml", eml)
    write(S2 + "/04_rate_tool_deck_load_history.csv", csv_text(
        ["deck_id", "rate_schedule_ref", "deck_type", "source_file", "received_at", "loaded_at", "loaded_by", "load_status",
         "total_rows", "min_effective_date", "max_effective_date", "notice_id"],
        [["NB-SMS-2026-08-01", "NB-SMS", "full", "NBCS_ORX_SMS_AZ_20260801.csv", "2026-07-23T16:20:00Z", "2026-07-24T10:05:00Z",
          "pricing.ops@" + DOM, "loaded", len(rows), "2026-08-01", "2026-08-01", ""],
         ["NB-SMS-2026-09-07", "NB-SMS", "partial", "NBCS_ORX_SMS_PARTIAL_20260907.csv", "2026-09-07T14:03:00Z",
          "2026-09-07T16:14:00Z", "pricing.ops@" + DOM, "loaded", 1, "2026-09-15", "2026-09-15", "RN-NB-2026-0907"]]))
    write(S2 + "/05_rate_tool_network_group_map.csv", csv_text(
        ["rate_schedule_ref", "mcc_mnc", "carrier_destination", "network_group", "mapped_by", "mapped_at"],
        [["NB-SMS", mm, NETS[mm][1], NETS[mm][0], "pricing.ops@" + DOM, "2026-07-24T10:05:00Z"] for mm in NETS]))
    return rows, az


# ======================= S3 usage + S4 rating =======================
def s3_s4():
    counts = defaultdict(lambda: [0, 0])  # (date, connection) -> messages, segments
    files = []
    for g in sorted((BASE / "usage/messages").glob("*.csv.gz")):
        data = gzip.decompress(g.read_bytes())
        day = g.name[:10]
        name = "messages/MED_ORX_NBCS_SMS_%s.csv.gz" % day.replace("-", "")
        write(S3 + "/" + name, gz(data))
        for r in csv.DictReader(io.StringIO(data.decode())):
            k = (day, r["smpp_connection"])
            counts[k][0] += 1
            counts[k][1] += int(r["segments"])
        files.append(name)
    shutil.copy(BASE / "usage/daily_summary.csv", OUT / S3 / "daily_summary_NBCS_SMS_202609.csv")
    shutil.copy(BASE / "usage/rejects.csv", OUT / S3 / "rejects_NBCS_SMS_202609.csv")
    shutil.copy(BASE / "usage/connection_map.csv", OUT / S3 / "smpp_connection_map.csv")
    rej = defaultdict(lambda: defaultdict(int))
    for r in read_csv("usage/rejects.csv"):
        rej[(r["submitted_at_utc"][:10], r["smpp_connection"])][r["reject_reason"]] += 1
    rows = []
    for (day, conn) in sorted(set(counts) | set(rej)):
        n, segs = counts.get((day, conn), [0, 0])
        rj = rej.get((day, conn), {})
        rows.append(["MED-%s-%s" % (day.replace("-", ""), conn), day, "smsc-gw1.orx.example", conn, n + sum(rj.values()), n,
                     sum(rj.values()), "|".join("%s:%d" % kv for kv in sorted(rj.items())), segs,
                     "MED_ORX_NBCS_SMS_%s.csv.gz" % day.replace("-", "") if n else "", "%sT01:10:00Z" % day])
    write(S3 + "/mediation_batch_log_SMS_202609.csv", csv_text(
        ["batch_id", "date_utc", "source_gateway", "smpp_connection", "records_in", "records_out", "records_rejected",
         "reject_reasons", "segments_out", "output_file", "closed_at"], rows))
    (OUT / S4).mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE / "rating/runs/RUN-2026-09.json", OUT / S4 / "RUN-2026-09_header.json")
    shutil.copy(BASE / "rating/runs/RUN-2026-09/lines.csv", OUT / S4 / "RUN-2026-09_rated_lines.csv")
    return files


# ======================= S5 carrier message detail =======================
def s5_mdr_detail():
    rnd = random.Random(908)
    recs = []  # (utc submit, supplier msg id, sender, dest, mcc_mnc, segments, connection, dlr)
    for g in sorted((BASE / "usage/messages").glob("*.csv.gz")):
        for r in csv.DictReader(io.StringIO(gzip.decompress(g.read_bytes()).decode())):
            recs.append((r["submitted_at_utc"], r["supplier_msg_id"], r["sender_id"], r["dest_masked"], r["mcc_mnc"],
                         int(r["segments"]), r["smpp_connection"], r["dlr_stat"] or "PENDING"))
    for r in read_csv("usage/rejects.csv"):
        if r["reject_reason"] == "UNMAPPED_CONNECTION":  # carried by Northbridge, dropped by our mediation
            recs.append((r["submitted_at_utc"], r["supplier_msg_id"], rnd.choice(SENDERS), r["dest_masked"], r["mcc_mnc"],
                         int(r["segments"]), r["smpp_connection"],
                         rnd.choices(["DELIVRD", "UNDELIV", "EXPIRED"], [94, 4, 2])[0]))
    recs.sort()
    lines, totals = [], defaultdict(lambda: [0, 0, Decimal(0)])
    for i, (st, smid, snd, dest, mm, seg, conn, dlr) in enumerate(recs, 1):
        g, label, rate = NETS[mm]
        charge = rate * seg
        t = datetime.strptime(st[:19], "%Y-%m-%dT%H:%M:%S") + BST
        lines.append("D|NBCS-%s-%07d|%s|%s|BST|%s|%s|%s|%s|%s|%d|%.6f|%.4f|%s|%s" % (
            st[:10].replace("-", ""), i, t.strftime("%d/%m/%Y"), t.strftime("%H:%M:%S"), smid, snd, dest, mm, label,
            seg, rate, charge, "orx_nb_" + conn[-2:], dlr))
        tot = totals[g]
        tot[0] += 1
        tot[1] += seg
        tot[2] += charge
    n = len(lines)
    parts, ctl_rows = 4, []
    per = -(-n // parts)
    for k in range(parts):
        chunk = lines[k * per:(k + 1) * per]
        charge = sum(Decimal(l.split("|")[12]) for l in chunk)
        txt = ("H|NBCS|ORX|%s|202609|SMS|%03d|%03d|20261008T101512\n" % (ACCOUNT, k + 1, parts) + "\n".join(chunk)
               + "\nT|%d|%.4f\n" % (len(chunk), charge))
        name = "06_NBCS_ORX_SMS_MDR_202609_%03dof%03d.txt.gz" % (k + 1, parts)
        data = write(S5 + "/" + name, gz(txt.encode()))
        ctl_rows.append((name[3:], len(data), hashlib.md5(data).hexdigest(), len(chunk), charge))
    ctl = ("INVOICE=%s\nACCOUNT=%s\nPERIOD=202609\nPRODUCT=A2P_SMS\nCONTENT=MDR_DETAIL (requested by customer 2026-10-06)\nFILES=%d\n"
           % (INV, ACCOUNT, parts)
           + "".join("%s|%d|md5:%s|records:%d|charge:%.4f\n" % r for r in ctl_rows)
           + "RECORDS=%d\nCHARGE=%.4f\nCREATED=20261008T101512Z\n" % (n, sum(r[4] for r in ctl_rows)))
    write(S5 + "/06_sftp_NBCS_ORX_SMS_MDR_202609.ctl", ctl)
    write(S5 + "/06_sftp_listing_mdr_detail.txt",
          "# SFTP drop on /inbound/northbridge/2026-10/ on 08 Oct 2026 (message detail sent on request)\n"
          "# Fields: type|record id|date|time|tz|supplier msg id|sender|destination|mcc-mnc|network|segments|rate|charge|smpp bind|dlr status\n"
          "# Times in the files are London local time (BST, UTC+1); the contract bills by UTC submit time.\n"
          + "".join("-rw-r--r--  1 nbcs  ftp  %9d Oct  8 10:15  %s\n" % (r[1], r[0]) for r in ctl_rows)
          + "-rw-r--r--  1 nbcs  ftp  %9d Oct  8 10:16  NBCS_ORX_SMS_MDR_202609.ctl\n" % len(ctl))
    return totals, n


def copy_raw():
    for sub, dst in [("S5-carrier-invoice-inbox", S5), ("S6-case-store", S6), ("S7-carrier-dispute", S7)]:
        for f in sorted((RAW / sub).iterdir()):
            if "_sample" in f.name:
                continue  # replaced by the full detail files
            (OUT / dst).mkdir(parents=True, exist_ok=True)
            shutil.copy(f, OUT / dst / f.name)
    (OUT / AK).mkdir(parents=True, exist_ok=True)
    shutil.copy(KEYS / "base.json", OUT / AK / "answer_key_base.json")


def checks(deck_rows, totals, n_detail):
    inv = json.loads((BASE / "invoices/inbox" / INV / "invoice.json").read_text())
    groups = ["MX-ALT", "MX-VRD", "CO-AND", "BR-CRZ", "GT-QTZ"]
    # Carrier message detail must roll up to every invoice line exactly.
    for g, l in zip(groups, inv["lines"]):
        c, s, a = totals[g]
        assert (c, s, "%.2f" % a) == (l["messages"], l["segments"], l["amount"]), (g, c, s, a)
    assert "%.2f" % sum(t[2] for t in totals.values()) == inv["total"] == "27000.00"
    # Rating run = 25,000.00.
    lines = read_csv("rating/runs/RUN-2026-09/lines.csv")
    assert "%.2f" % sum(Decimal(l["amount"]) for l in lines) == "25000.00"
    # Our six networks are in the A-Z deck at the rating run's rates, and no MCC-MNC appears twice.
    deck = {r[2] + r[3]: r[4] for r in deck_rows}
    assert len(deck) == len(deck_rows)
    assert all(deck[mm] == Decimal(OLD[NETS[mm][0]]) for mm in NETS)
    return {"carrier_detail_records": n_detail, "deck_rows": len(deck_rows)}


README = """# P1 test case - Northbridge A2P SMS invoice, September 2026 (S1-S7)

One complete test case for the invoice dispute prototype: the raw files each external system would really hold.
The service is wholesale A2P SMS termination: rates per message segment by destination network (MCC-MNC),
billed on every segment the carrier accepts at submit, whatever the delivery receipt says.
All companies, people, networks and numbers are fictional. Built by build_test_case.py (repo: prototypes/tele-prototypes/test-case/).

**The case.** Northbridge Carrier Services bills Orion Messaging Exchange USD 27,000.00 for September 2026
(invoice NB-INV-2026-09-0412). Our own records say 25,000.00. Variance 2,000.00 (7.41% of invoice), so a case opens.

| Cause | Amount | Right answer |
|---|---:|---|
| Mexico - Altavia Movil (334-990) billed at the new rate 0.0185 per segment from 1 Sep; the notice only allows it from 15 Sep (clause 6.3) | 1,820.00 | Dispute |
| Colombia - Andina Movil +12,000 segments ({co_msgs:,} messages) on SMPP bind SMPP-NB-07, which our mediation rejected (connection map starts 1 Oct) | 180.00 | Pay, fix connection map |

Dispute 1,820.00, pay 25,180.00 by 04 Nov. The full expected outcome is in the answer key folder; keep it away from agents.

Traps on purpose: {undeliv:,} messages have no DELIVRD receipt but are still billable (clause 4.2); 25 duplicate
submissions and 60 submit failures were dropped correctly by mediation; Brazil - Cruzeiro Movel has two MCC-MNCs
(724-990 and legacy 724-991) under one invoice line.

## Timeline

| Date | Event | System |
|---|---|---|
| 20 Feb 2025 | Agreement signed; amendment A1 adds Guatemala 26 Jan 2026 | S1 |
| 23 Jul 2026 | A-Z SMS rate deck received, loaded 24 Jul | S2 |
| 01-30 Sep | Traffic; mediation writes one message file per day | S3 |
| 07 Sep 14:00 UTC | Rate notice: Altavia Movil up, "effective 15/09" | S2 |
| 21 Sep | New SMPP bind SMPP-NB-07 starts carrying Colombia traffic | S3 |
| 01 Oct | Rating run RUN-2026-09 = 25,000.00 | S4 |
| 05 Oct | Invoice email + PDF + summary, SFTP copy, e-invoice XML | S5 |
| 05 Oct | Case opened (Jira SETL-482) | S6 |
| 06 Oct | Dispute form + packet emailed | S7 |
| 07 Oct | Carrier acknowledges, dispute NB-DSP-0098341 | S7 (arrives in S5) |
| 08 Oct | Carrier sends full message detail (MDR) by SFTP | S5 |
| 20-21 Oct | Resolution letter, credit note 1,820.00 | S7, S5 |

## What is in each folder

| System | Files | Size in industry terms |
|---|---|---|
| S1 Contract repository | Signed agreement PDF, amendment PDF, CLM record JSON, contract register | Full size |
| S2 Rate deck store | Rate notice email (deck attached), A-Z SMS deck, partial deck, load history, network group map | A-Z deck: {deck_rows:,} networks (MCC-MNC) |
| S3 Usage store (mediation) | 30 daily message files (gzip), daily summary, reject log, SMPP connection map, batch log | {msgs:,} messages with delivery receipts; {rejects:,} rejects |
| S4 Rated charges | Rating run header + {rated} rated lines (day x network x MCC-MNC) | Full size |
| S5 Carrier invoice inbox | Invoice email, PDF, carrier traffic summary, UBL XML, SFTP drops, message detail (4 gzip files), other-carrier notice, credit note | Message detail: {detail:,} records |
| S6 Case store | Jira create/issue/fields/history, field map, Salesforce case, tracker sheet | Full size |
| S7 Carrier dispute | Dispute email, carrier form, evidence packet, ack, rejection (negative test), resolution letter, portal export | Full size |

## How the figures tie out (checked by the build script)

- Carrier message detail rolls up to every invoice line exactly (messages, segments, amount) and totals 27,000.00.
- Our messages roll up to the daily summary; the rated lines total 25,000.00.
- The carrier detail = our messages + the {co_msgs:,} SMPP-NB-07 messages our mediation rejected; records match ours by supplier message ID.
- Carrier detail times are London time (BST): messages after 23:00 UTC on 30 Sep show as 01/10. The contract bills by UTC.

## Large files

{big_rows}

Spreadsheets are CSV (the real ones are often .xlsx). Dates on carrier files are dd/mm/yyyy. MCC-MNC codes ending
99x are fictional; other MCC-MNC rows in the A-Z deck use real country codes with made-up network names.
"""


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    s1_contract_pdf()
    s1_amendment_pdf()
    s1_clm()
    deck_rows, _ = s2()
    s3_s4()
    totals, n = s5_mdr_detail()
    copy_raw()
    print(checks(deck_rows, totals, n))
    big = [p for p in sorted(OUT.rglob("*")) if p.is_file() and p.stat().st_size > BIG]
    msg = [p for p in big if p.parent.name == "messages"]
    big_rows = "These are not in the Dropbox folder itself; they come in the zip \"%s\".\n%d files, %.0f MB in total (compressed):\n\n| File | Size |\n|---|---:|\n" % (
        ZIP.name, len(big), sum(p.stat().st_size for p in big) / 1e6) + "".join(
        "| %s | %.2f MB |\n" % (p.relative_to(OUT), p.stat().st_size / 1e6) for p in big if p not in msg) + \
        "| %s/messages/ (%d daily files) | %.1f MB |\n" % (S3, len(msg), sum(p.stat().st_size for p in msg) / 1e6)
    daily = read_csv("usage/daily_summary.csv")
    rejects = read_csv("usage/rejects.csv")
    write("00_README.md", README.format(
        deck_rows=len(deck_rows), msgs=sum(int(r["messages"]) for r in daily), rejects=len(rejects), detail=n,
        co_msgs=sum(r["reject_reason"] == "UNMAPPED_CONNECTION" for r in rejects),
        undeliv=sum(int(r["undelivered"]) + int(r["expired"]) + int(r["no_receipt"]) for r in daily),
        rated=len(read_csv("rating/runs/RUN-2026-09/lines.csv")), big_rows=big_rows))
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print("%10d  %s" % (p.stat().st_size, p.relative_to(OUT)))
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_STORED) as z:  # contents are already gzip-compressed
        for p in big:
            z.write(p, str(p.relative_to(OUT)))
    print("zip", ZIP.stat().st_size)


if __name__ == "__main__":
    main()

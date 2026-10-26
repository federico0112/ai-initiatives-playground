"""Build raw example files for the Prototype 1 external systems S5, S6 and S7.

S5 = carrier invoice inbox, S6 = case store (Jira / Salesforce / spreadsheet),
S7 = carrier dispute channel (email + carrier form, replies back through S5).

Every figure is read from the Northbridge A2P SMS case in ../dataset/sim-data/base
(invoice NB-INV-2026-09-0412, agreement AGR-NB-2025-014, CASE-0001); run
../dataset/generate_dataset.py first.
Layouts follow ../inbox-raw-examples.md and ../case-and-dispute-raw-examples.md.

All output is pure ASCII so the text-only Dropbox connector can upload it
(PDFs are uncompressed, standard fonts, no embedded fonts).

Run:  python3 build_examples.py   -> writes ./out/
"""
import base64
import csv
import io
import json
import random
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).parent / "out"
BASE = Path(__file__).resolve().parent.parent / "dataset" / "sim-data" / "base"

# ---------- case figures (from sim-data/base and answer-keys/base.json) ----------
INV = "NB-INV-2026-09-0412"
ACCOUNT = "NBCS-ACC-30988"
CONTRACT = "NBCS-SMS-2025-0117"
CASE = "CASE-0001"
DSP = "NB-DSP-0098341"
CN = "NB-CN-2026-10-0077"
BUYER = "Orion Messaging Exchange"
DOM = "orion-msg.example"


def _load():
    inv = json.loads((BASE / "invoices/inbox" / INV / "invoice.json").read_text())
    with open(BASE / "usage/daily_summary.csv", newline="") as f:
        daily = list(csv.DictReader(f))
    # label, MCC-MNC(s), messages, segments, rate, amount
    lines = [(l["destination"], l["mcc_mnc"].replace("|", ", "), l["messages"], l["segments"],
              "%.6f" % Decimal(l["rate"]), l["amount"]) for l in inv["lines"]]
    mx = [r for r in daily if r["network_group"] == "MX-ALT" and r["date_utc"] < "2026-09-15"]
    return inv, lines, mx


INVOICE, LINES, MX_DAYS = _load()
TOTAL = INVOICE["total"]
DISPUTED = "1820.00"
UNDISPUTED = "25180.00"
MX_EARLY_SEG = sum(int(r["billable_segments"]) for r in MX_DAYS)     # 20,000 segments/day x 14 days
MX_EARLY_MSGS = sum(int(r["messages"]) for r in MX_DAYS)
assert TOTAL == "27000.00" and MX_EARLY_SEG == 280000


def money(s):
    return "{:,.2f}".format(float(s))


def write(rel, data):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.replace("\r\n", "\n").encode("ascii")
    data.decode("ascii")  # fail loudly on non-ASCII
    p.write_bytes(data)
    return data


def jdump(obj):
    return json.dumps(obj, indent=2) + "\n"


# ---------- minimal ASCII PDF writer ----------
class Pdf:
    FONTS = {"R": "Helvetica", "B": "Helvetica-Bold", "M": "Courier"}

    def __init__(self, title):
        self.title, self.pages = title, []

    def page(self):
        self.ops = []
        self.pages.append(self.ops)

    def text(self, x, y, s, size=9, f="R", right=False):
        if right:  # rough right-align: Helvetica avg width 0.5em, digits 0.556em
            x -= len(s) * size * (0.6 if f == "M" else 0.53)
        s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        self.ops.append("BT /%s %s Tf %.1f %.1f Td (%s) Tj ET" % (f, size, x, y, s))

    def line(self, x1, y1, x2, y2, w=0.6):
        self.ops.append("%.1f w %.1f %.1f m %.1f %.1f l S" % (w, x1, y1, x2, y2))

    def box(self, x, y, w, h, gray=0.92):
        self.ops.append("%.2f g %.1f %.1f %.1f %.1f re f 0 g" % (gray, x, y, w, h))

    def bytes(self):
        objs = ["<< /Type /Catalog /Pages 2 0 R >>", None]
        font_ids = {}
        for k, name in self.FONTS.items():
            objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % name)
            font_ids[k] = len(objs)
        res = "<< /Font << %s >> >>" % " ".join("/%s %d 0 R" % (k, i) for k, i in font_ids.items())
        kids = []
        for ops in self.pages:
            stream = "\n".join(ops)
            objs.append("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
            objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources %s /Contents %d 0 R >>"
                        % (res, len(objs)))
            kids.append(len(objs))
        objs[1] = "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), len(kids))
        objs.append("<< /Title (%s) /Producer (raw-examples build_examples.py) >>" % self.title)
        out, offs = "%PDF-1.4\n", []
        for i, body in enumerate(objs, 1):
            offs.append(len(out))
            out += "%d 0 obj\n%s\nendobj\n" % (i, body)
        x = len(out)
        out += "xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        out += "".join("%010d 00000 n \n" % o for o in offs)
        out += "trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, len(objs), x)
        return out.encode("ascii")


def nb_letterhead(p, doc_title):
    p.text(40, 790, "NORTHBRIDGE CARRIER SERVICES LTD", 13, "B")
    p.text(40, 776, "12 Harbour Exchange Square, London E14 9GE, United Kingdom", 8)
    p.text(40, 765, "VAT Reg: GB 998 8776 65   wholesale-billing@northbridge.example", 8)
    p.text(555, 790, doc_title, 16, "B", right=True)
    p.line(40, 755, 555, 755, 1.2)


def nb_footer(p, page="Page 1 of 1"):
    p.line(40, 60, 555, 60)
    p.text(40, 48, "Northbridge Carrier Services Ltd - fictional company, prototype data only", 7)
    p.text(555, 48, page, 7, right=True)


# ================= S5 carrier invoice inbox =================
def s5_invoice_pdf():
    p = Pdf("Invoice " + INV)
    p.page()
    nb_letterhead(p, "INVOICE")
    kv = [("Invoice No:", INV), ("Invoice Date:", "05/10/2026"), ("Due Date:", "04/11/2026"),
          ("Account:", ACCOUNT), ("Contract:", CONTRACT), ("Currency:", "USD")]
    for i, (k, v) in enumerate(kv):
        p.text(360, 735 - i * 13, k, 9, "B")
        p.text(440, 735 - i * 13, v, 9)
    p.text(40, 735, "Bill To:", 9, "B")
    for i, s in enumerate([BUYER + " S.A.", "Av. Corrientes 1234, C1043 Buenos Aires, Argentina",
                           "CUIT 30-71234567-9", "Attn: Settlements - settlements@" + DOM]):
        p.text(40, 722 - i * 12, s, 9)
    p.text(40, 640, "Service: A2P SMS Termination   Period: 01/09/2026 - 30/09/2026 (GMT)", 9, "B")
    y = 615
    p.box(40, y - 4, 515, 16)
    cols = [(44, "Destination network", False), (250, "MCC-MNC", False), (360, "Messages", True),
            (430, "Segments", True), (490, "Rate/SMS", True), (551, "Amount", True)]
    for x, h, r in cols:
        p.text(x, y, h, 9, "B", right=r)
    y -= 20
    for label, mm, msgs, segs, rate, amt in LINES:
        p.text(44, y, label, 9)
        p.text(250, y, mm, 9)
        p.text(360, y, "{:,}".format(msgs), 9, right=True)
        p.text(430, y, "{:,}".format(segs), 9, right=True)
        p.text(490, y, rate, 9, right=True)
        p.text(551, y, money(amt), 9, right=True)
        y -= 15
    p.line(40, y + 6, 555, y + 6)
    y -= 10
    for k, v, f in [("Sub Total", money(TOTAL), "R"), ("VAT 0% - Outside the scope of UK VAT", "0.00", "R"),
                    ("TOTAL DUE", "USD " + money(TOTAL), "B")]:
        p.text(490, y, k, 9, f, right=True)
        p.text(551, y, v, 9, f, right=True)
        y -= 14
    y -= 20
    for s in ["Payment terms: 30 days net. Charged per message segment accepted (submit_sm_resp OK),",
              "irrespective of delivery receipt status.",
              "Bank: Example Bank plc   IBAN GB00 EXMP 0000 0000 0000 00   SWIFT EXMPGB2L",
              "Please quote the invoice number with your remittance.",
              "Disputes: in writing within 14 days of invoice date to disputes@northbridge.example",
              "Message detail records (MDR) available on request."]:
        p.text(40, y, s, 8)
        y -= 12
    nb_footer(p)
    return write("S5-carrier-invoice-inbox/02_NB-INV-2026-09-0412.pdf", p.bytes())


def s5_summary_csv():
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Northbridge Carrier Services Ltd"] + [""] * 6)
    w.writerow(["A2P SMS Traffic Summary - Customer: %s (%s)" % (BUYER, ACCOUNT)] + [""] * 6)
    w.writerow(["Invoice: " + INV] + [""] * 6)
    w.writerow(["Period: 01/09/2026 00:00 - 30/09/2026 23:59 GMT"] + [""] * 6)
    w.writerow([""] * 7)
    w.writerow(["Destination Network", "MCC-MNC", "Service", "Messages", "Segments", "Rate (USD/SMS)", "Charge (USD)"])
    for label, mm, msgs, segs, rate, amt in LINES:
        w.writerow([label, mm, "A2P SMS MT", "{:,}".format(msgs), "{:,}".format(segs), rate, money(amt)])
    w.writerow([""] * 7)
    w.writerow(["TOTAL", "", "", "{:,}".format(sum(l[2] for l in LINES)), "{:,}".format(sum(l[3] for l in LINES)),
                "", money(TOTAL)])
    return write("S5-carrier-invoice-inbox/03_ORX_SMS_Traffic_Summary_Sep2026.csv", buf.getvalue())


def b64(data):
    s = base64.b64encode(data).decode()
    return "\n".join(s[i:i + 76] for i in range(0, len(s), 76))


def s5_invoice_eml(pdf, summary):
    body = """Return-Path: <wholesale-billing@northbridge.example>
Received: from mx2.northbridge.example (mx2.northbridge.example [203.0.113.24])
        by mx.{dom} with ESMTPS id 4f1a9c
        for <settlements@{dom}>; Mon, 05 Oct 2026 09:00:00 +0000
Message-ID: <20261005085812.4411@billing.northbridge.example>
Date: Mon, 05 Oct 2026 09:58:12 +0100
From: "Northbridge Wholesale Billing" <wholesale-billing@northbridge.example>
To: settlements@{dom}
Cc: carrier-relations@northbridge.example
Subject: Invoice {inv} - A2P SMS - September 2026 - account {acc}
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==NB_6c1e2f=="

--==NB_6c1e2f==
Content-Type: text/plain; charset="us-ascii"

Dear Customer,

Please find attached invoice {inv} for A2P SMS termination
during September 2026, together with the traffic summary.

Amount due: USD 27,000.00   Due date: 04/11/2026

Disputes must be raised in writing within 14 days of the invoice date to
disputes@northbridge.example quoting the invoice number. Message detail
records are available on request.

This is an automated message, please do not reply.
Northbridge Carrier Services Ltd - Wholesale Billing

--==NB_6c1e2f==
Content-Type: application/pdf; name="{inv}.pdf"
Content-Disposition: attachment; filename="{inv}.pdf"
Content-Transfer-Encoding: base64

{pdf}

--==NB_6c1e2f==
Content-Type: text/csv; name="ORX_SMS_Traffic_Summary_Sep2026.csv"
Content-Disposition: attachment; filename="ORX_SMS_Traffic_Summary_Sep2026.csv"
Content-Transfer-Encoding: base64

{csv}

--==NB_6c1e2f==--
""".format(inv=INV, acc=ACCOUNT, dom=DOM, pdf=b64(pdf), csv=b64(summary))
    write("S5-carrier-invoice-inbox/01_email_invoice_NB-INV-2026-09-0412.eml", body)


def s5_ubl_xml():
    lines = []
    for i, (label, mm, msgs, segs, rate, amt) in enumerate(LINES, 1):
        lines.append("""  <cac:InvoiceLine>
    <cbc:ID>{i}</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">{segs}</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="USD">{amt}</cbc:LineExtensionAmount>
    <cac:InvoicePeriod><cbc:StartDate>2026-09-01</cbc:StartDate><cbc:EndDate>2026-09-30</cbc:EndDate></cac:InvoicePeriod>
    <cac:Item>
      <cbc:Description>A2P SMS MT segments. Messages: {msgs}. MCC-MNC: {mm}</cbc:Description>
      <cbc:Name>{label}</cbc:Name>
      <cac:ClassifiedTaxCategory><cbc:ID>O</cbc:ID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="USD">{rate}</cbc:PriceAmount><cbc:BaseQuantity unitCode="C62">1</cbc:BaseQuantity></cac:Price>
  </cac:InvoiceLine>""".format(i=i, segs=segs, amt=amt, msgs=msgs, mm=mm, label=label, rate=rate.rstrip("0")))
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0</cbc:CustomizationID>
  <cbc:ProfileID>urn:fdc:peppol.eu:2017:poacc:billing:01:1.0</cbc:ProfileID>
  <cbc:ID>{inv}</cbc:ID>
  <cbc:IssueDate>2026-10-05</cbc:IssueDate>
  <cbc:DueDate>2026-11-04</cbc:DueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:Note>A2P SMS termination, charged per segment accepted at submit. Disputes within 14 days of invoice date.</cbc:Note>
  <cbc:DocumentCurrencyCode>USD</cbc:DocumentCurrencyCode>
  <cbc:BuyerReference>{acc}</cbc:BuyerReference>
  <cac:InvoicePeriod>
    <cbc:StartDate>2026-09-01</cbc:StartDate>
    <cbc:EndDate>2026-09-30</cbc:EndDate>
  </cac:InvoicePeriod>
  <cac:ContractDocumentReference><cbc:ID>{con}</cbc:ID></cac:ContractDocumentReference>
  <cac:AdditionalDocumentReference>
    <cbc:ID>{inv}.pdf</cbc:ID>
    <cbc:DocumentDescription>Invoice PDF (see 02_{inv}.pdf; embedded copy omitted)</cbc:DocumentDescription>
  </cac:AdditionalDocumentReference>
  <cac:AccountingSupplierParty><cac:Party>
    <cbc:EndpointID schemeID="9932">GB998877665</cbc:EndpointID>
    <cac:PostalAddress>
      <cbc:StreetName>12 Harbour Exchange Square</cbc:StreetName><cbc:CityName>London</cbc:CityName>
      <cbc:PostalZone>E14 9GE</cbc:PostalZone><cac:Country><cbc:IdentificationCode>GB</cbc:IdentificationCode></cac:Country>
    </cac:PostalAddress>
    <cac:PartyTaxScheme><cbc:CompanyID>GB998877665</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>
    <cac:PartyLegalEntity><cbc:RegistrationName>Northbridge Carrier Services Ltd</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cbc:EndpointID schemeID="0000">AR30712345679</cbc:EndpointID>
    <cac:PostalAddress>
      <cbc:StreetName>Av. Corrientes 1234</cbc:StreetName><cbc:CityName>Buenos Aires</cbc:CityName>
      <cbc:PostalZone>C1043</cbc:PostalZone><cac:Country><cbc:IdentificationCode>AR</cbc:IdentificationCode></cac:Country>
    </cac:PostalAddress>
    <cac:PartyLegalEntity><cbc:RegistrationName>{buyer} S.A.</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingCustomerParty>
  <cac:PaymentMeans>
    <cbc:PaymentMeansCode>58</cbc:PaymentMeansCode>
    <cbc:PaymentID>{inv}</cbc:PaymentID>
    <cac:PayeeFinancialAccount><cbc:ID>GB00EXMP00000000000000</cbc:ID></cac:PayeeFinancialAccount>
  </cac:PaymentMeans>
  <cac:PaymentTerms><cbc:Note>30 days net</cbc:Note></cac:PaymentTerms>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="USD">0.00</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="USD">{tot}</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="USD">0.00</cbc:TaxAmount>
      <cac:TaxCategory><cbc:ID>O</cbc:ID><cbc:TaxExemptionReason>Outside the scope of VAT</cbc:TaxExemptionReason>
        <cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="USD">{tot}</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="USD">{tot}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="USD">{tot}</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="USD">{tot}</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
{lines}
</Invoice>
""".format(inv=INV, acc=ACCOUNT, con=CONTRACT, tot=TOTAL, buyer=BUYER, lines="\n".join(lines))
    write("S5-carrier-invoice-inbox/04_NB-INV-2026-09-0412_ubl.xml", xml)


def s5_sftp_ctl(pdf, summary):
    import hashlib
    ctl = """INVOICE={inv}
ACCOUNT={acc}
PERIOD=202609
PRODUCT=A2P_SMS
FILES=2
NBCS_ORX_SMS_INV_202609_{inv}.pdf|{ps}|md5:{pm}
NBCS_ORX_SMS_SUM_202609_v1.csv|{ss}|md5:{sm}
TOTAL=27000.00
CURRENCY=USD
CREATED=20261005T085744Z
""".format(inv=INV, acc=ACCOUNT, ps=len(pdf), pm=hashlib.md5(pdf).hexdigest(),
           ss=len(summary), sm=hashlib.md5(summary).hexdigest())
    listing = """# SFTP drop as seen on /inbound/northbridge/2026-10/  (same invoice, no email)
# The .ctl file is written last; the reader waits for it before taking the set.
-rw-r--r--  1 nbcs  ftp  {ps:>7} Oct  5 08:57  NBCS_ORX_SMS_INV_202609_{inv}.pdf
-rw-r--r--  1 nbcs  ftp  {ss:>7} Oct  5 08:57  NBCS_ORX_SMS_SUM_202609_v1.csv
-rw-r--r--  1 nbcs  ftp  {cs:>7} Oct  5 08:58  NBCS_ORX_SMS_202609.ctl
""".format(inv=INV, ps=len(pdf), ss=len(summary), cs=len(ctl))
    write("S5-carrier-invoice-inbox/05_sftp_NBCS_ORX_SMS_202609.ctl", ctl)
    write("S5-carrier-invoice-inbox/05_sftp_listing.txt", listing)


MDR_FIELDS = ("type|record id|date|time|tz|supplier msg id|sender|destination|mcc-mnc|network|segments|"
              "rate|charge|smpp bind|dlr status")


def s5_mdr_detail():
    """Sample of the carrier's pipe-delimited message detail file for Altavia, 1 Sep (London time)."""
    rnd = random.Random(412)
    rows, t, charge_total = [], 3600 + 32, Decimal(0)  # 01:00:32 BST = 00:00:32 UTC
    for i in range(1, 41):
        seg = rnd.choices((1, 2, 3), cum_weights=(86, 96, 100))[0]
        charge = Decimal("0.0185") * seg
        hh, mm, ss = t // 3600, (t % 3600) // 60, t % 60
        rows.append("D|NBCS-20260901-%07d|01/09/2026|%02d:%02d:%02d|BST|%016x|%s|52%06dXXXX|334990|Mexico - Altavia Movil|%d|0.018500|%.4f|orx_nb_0%d|%s"
                    % (i, hh, mm, ss, rnd.getrandbits(64), rnd.choice(["BLITORAL", "RAPIDOENV", "PAGOFACIL", "MERCADOSOL"]),
                       rnd.randrange(200000, 999999), seg, charge, 1 + i % 2,
                       rnd.choices(["DELIVRD", "UNDELIV", "EXPIRED"], [94, 4, 2])[0]))
        charge_total += charge
        t += rnd.randint(0, 4)
    txt = ("# SAMPLE: first 40 records of file 001 of 004, sent on request after the dispute.\n"
           "# Trailer below counts this sample only. Fields: " + MDR_FIELDS + "\n"
           "H|NBCS|ORX|%s|202609|SMS|001|004|20261008T101512\n" % ACCOUNT
           + "\n".join(rows) + "\nT|%d|%.4f\n" % (len(rows), charge_total))
    write("S5-carrier-invoice-inbox/06_NBCS_ORX_SMS_MDR_202609_001of004_sample.txt", txt)


def s5_portal_notification():
    write("S5-carrier-invoice-inbox/07_email_portal_notification_other_carrier.eml", """Message-ID: <a81f22c0.77120@billing.atlasmsg.example>
Date: Fri, 02 Oct 2026 06:15:40 +0200
From: "Atlas Global Messaging Billing" <no-reply@billing.atlasmsg.example>
To: settlements@{dom}
Subject: Your SMS invoice for Sep-2026 is now available
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"

Dear customer,

Your invoice INV/26/0098812 (Customer ID 77120) for the period
01-Sep-2026 to 30-Sep-2026 is available at:
https://portal.atlasmsg.example/billing/invoices/0098812

Total: 41.336,20 EUR
Due:   01.11.2026

Atlas Global Messaging - Billing (fictional, prototype data)
""".format(dom=DOM))


def s5_credit_note_pdf():
    p = Pdf("Credit note " + CN)
    p.page()
    nb_letterhead(p, "CREDIT NOTE")
    kv = [("Credit Note No:", CN), ("Date:", "21/10/2026"), ("Account:", ACCOUNT),
          ("Original Invoice:", INV), ("Dispute ID:", DSP), ("Currency:", "USD")]
    for i, (k, v) in enumerate(kv):
        p.text(340, 735 - i * 13, k, 9, "B")
        p.text(440, 735 - i * 13, v, 9)
    p.text(40, 735, "Issued To:", 9, "B")
    for i, s in enumerate([BUYER + " S.A.", "Av. Corrientes 1234, C1043 Buenos Aires, Argentina", "Your ref: " + CASE]):
        p.text(40, 722 - i * 12, s, 9)
    y = 630
    p.box(40, y - 4, 515, 16)
    for x, h, r in [(44, "Description", False), (330, "Segments", True), (420, "Rate diff/SMS", True), (551, "Credit", True)]:
        p.text(x, y, h, 9, "B", right=r)
    y -= 20
    p.text(44, y, "SMS Mexico - Altavia Movil (334-990), 01/09/2026 - 14/09/2026", 9)
    p.text(44, y - 11, "Rate adjustment 0.018500 -> 0.012000 (dispute upheld)", 8)
    p.text(330, y - 22, "{:,}".format(MX_EARLY_SEG), 9, right=True)
    p.text(420, y - 22, "0.006500", 9, right=True)
    p.text(551, y - 22, "-" + money(DISPUTED), 9, right=True)
    y -= 46
    p.line(40, y + 10, 555, y + 10)
    for k, v, f in [("Sub Total", "-" + money(DISPUTED), "R"), ("VAT 0%", "0.00", "R"),
                    ("TOTAL CREDIT", "USD -" + money(DISPUTED), "B")]:
        p.text(450, y - 4, k, 9, f, right=True)
        p.text(551, y - 4, v, 9, f, right=True)
        y -= 14
    p.text(40, y - 30, "This credit will be applied against your October 2026 invoice. No payment is due on this document.", 8)
    nb_footer(p)
    write("S5-carrier-invoice-inbox/08_NB-CN-2026-10-0077_credit_note.pdf", p.bytes())


# ================= S6 case store =================
CF = {  # this Jira site's custom field IDs (discovered with GET /rest/api/3/field)
    "invoice_id": "customfield_10231", "carrier": "customfield_10232", "invoiced_amount": "customfield_10233",
    "disputed_amount": "customfield_10234", "carrier_claim_id": "customfield_10235", "recommendation": "customfield_10236",
    "expected_amount": "customfield_10237", "dispute_deadline": "customfield_10238", "internal_case_id": "customfield_10239",
}
JIRA = "https://orionmsg.atlassian.example"


def adf(*paras):
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]} for t in paras]}


def s6_jira():
    create = {"fields": {
        "project": {"key": "SETL"},
        "issuetype": {"name": "Invoice Dispute"},
        "summary": "Northbridge SMS %s - 7.41%% over expected" % INV,
        "priority": {"name": "High"},
        "labels": ["northbridge", "a2p-sms", "rate-before-effective"],
        "duedate": "2026-10-19",
        "description": adf("Invoice 27,000.00 vs expected 25,000.00 (variance 2,000.00, 7.41% of invoice).",
                           "Supported dispute 1,820.00: Mexico - Altavia Movil (334-990) rate 0.0185 per segment applied 01-14 Sep; notice RN-NB-2026-0907 makes it effective 15 Sep (clause 6.3).",
                           "Colombia - Andina Movil 180.00 explained by our own mediation rejects on SMPP bind SMPP-NB-07. Not disputed."),
        CF["invoice_id"]: INV, CF["carrier"]: {"value": "Northbridge Carrier Services"},
        CF["invoiced_amount"]: 27000.0, CF["expected_amount"]: 25000.0, CF["disputed_amount"]: 1820.0,
        CF["recommendation"]: {"value": "Partial dispute"}, CF["dispute_deadline"]: "2026-10-19",
        CF["internal_case_id"]: CASE,
    }}
    write("S6-case-store/01_jira_create_issue_request.json",
          jdump({"_request": "POST %s/rest/api/3/issue" % JIRA, "body": create}))

    issue = {
        "expand": "renderedFields,names,schema,operations,editmeta,changelog",
        "id": "104733", "key": "SETL-482",
        "self": "%s/rest/api/3/issue/104733" % JIRA,
        "fields": dict(create["fields"], **{
            "project": {"id": "10041", "key": "SETL", "name": "Carrier Settlements"},
            "issuetype": {"id": "10087", "name": "Invoice Dispute", "subtask": False},
            "status": {"id": "10112", "name": "Awaiting Carrier", "statusCategory": {"id": 4, "key": "indeterminate", "name": "In Progress"}},
            "priority": {"id": "2", "name": "High"},
            "resolution": None,
            "assignee": {"accountId": "5f1c9e0a4b2d7e0069a3e21", "displayName": "Maria Gomez", "emailAddress": "maria.gomez@" + DOM},
            "reporter": {"accountId": "712020:9ab3f0c2-7d41-4e55-bb1a-0c2f9d7e8a10", "displayName": "Assurance Agent", "accountType": "app"},
            "created": "2026-10-05T09:00:41.000+0000",
            "updated": "2026-10-07T16:12:03.000+0000",
            CF["carrier_claim_id"]: DSP,
            "issuelinks": [],
            "attachment": [
                {"id": "88812", "filename": "dispute-packet-CASE-0001.pdf", "mimeType": "application/pdf", "size": 0,
                 "created": "2026-10-06T10:05:12.000+0000",
                 "content": "%s/rest/api/3/attachment/content/88812" % JIRA},
                {"id": "88813", "filename": "NBCS_Dispute_Notification_Form.csv", "mimeType": "text/csv", "size": 0,
                 "created": "2026-10-06T10:05:13.000+0000",
                 "content": "%s/rest/api/3/attachment/content/88813" % JIRA}],
            "comment": {"startAt": 0, "maxResults": 50, "total": 3, "comments": [
                {"id": "301101", "author": {"displayName": "Assurance Agent"}, "created": "2026-10-05T09:04:10.000+0000",
                 "body": adf("Rejects on SMPP-NB-07 explain the 180.00 Colombia gap. Not disputed. Internal action: add SMPP-NB-07 to connection_map from 2026-09-21 and reprocess the UNMAPPED_CONNECTION rejects.")},
                {"id": "301144", "author": {"displayName": "Maria Gomez"}, "created": "2026-10-06T09:55:02.000+0000",
                 "body": adf("Approved. Send to Northbridge. Pay undisputed 25,180.00 by 04/11.")},
                {"id": "301190", "author": {"displayName": "Assurance Agent"}, "created": "2026-10-07T16:12:03.000+0000",
                 "body": adf("Carrier acknowledged. Dispute ID NB-DSP-0098341, status Received - under review.")}]},
        }),
    }

    fields = [
        {"id": "summary", "name": "Summary", "custom": False, "schema": {"type": "string", "system": "summary"}},
        {"id": "status", "name": "Status", "custom": False, "schema": {"type": "status", "system": "status"}},
        {"id": "duedate", "name": "Due date", "custom": False, "schema": {"type": "date", "system": "duedate"}},
    ]
    meta = [("invoice_id", "Invoice Number", "string", "com.atlassian.jira.plugin.system.customfieldtypes:textfield"),
            ("carrier", "Carrier", "option", "com.atlassian.jira.plugin.system.customfieldtypes:select"),
            ("invoiced_amount", "Invoiced Amount (USD)", "number", "com.atlassian.jira.plugin.system.customfieldtypes:float"),
            ("disputed_amount", "Disputed Amount (USD)", "number", "com.atlassian.jira.plugin.system.customfieldtypes:float"),
            ("carrier_claim_id", "Carrier Dispute Ref", "string", "com.atlassian.jira.plugin.system.customfieldtypes:textfield"),
            ("recommendation", "Recommendation", "option", "com.atlassian.jira.plugin.system.customfieldtypes:select"),
            ("expected_amount", "Expected Amount (USD)", "number", "com.atlassian.jira.plugin.system.customfieldtypes:float"),
            ("dispute_deadline", "Dispute Deadline", "date", "com.atlassian.jira.plugin.system.customfieldtypes:datepicker"),
            ("internal_case_id", "Assurance Case ID", "string", "com.atlassian.jira.plugin.system.customfieldtypes:textfield")]
    for key, name, typ, custom in meta:
        fid = CF[key]
        fields.append({"id": fid, "key": fid, "name": name, "custom": True, "orderable": True, "navigable": True,
                       "searchable": True, "clauseNames": ["cf[%s]" % fid.split("_")[1], name],
                       "schema": {"type": typ, "custom": custom, "customId": int(fid.split("_")[1])}})
    write("S6-case-store/03_jira_fields_metadata.json",
          jdump({"_request": "GET %s/rest/api/3/field" % JIRA, "body": fields}))

    changelog = {"self": "%s/rest/api/3/issue/SETL-482/changelog?startAt=0" % JIRA,
                 "maxResults": 100, "startAt": 0, "total": 4, "isLast": True, "values": [
        {"id": "551201", "author": {"displayName": "Assurance Agent"}, "created": "2026-10-05T09:00:41.000+0000",
         "items": [{"field": "status", "fieldtype": "jira", "from": None, "fromString": None, "to": "10110", "toString": "Triage"}]},
        {"id": "551233", "author": {"displayName": "Assurance Agent"}, "created": "2026-10-05T09:31:18.000+0000",
         "items": [{"field": "status", "fieldtype": "jira", "from": "10110", "fromString": "Triage", "to": "10111", "toString": "Pending Approval"},
                   {"field": "Disputed Amount (USD)", "fieldtype": "custom", "fieldId": CF["disputed_amount"], "from": None, "fromString": None, "to": None, "toString": "1820.0"}]},
        {"id": "551260", "author": {"displayName": "Maria Gomez"}, "created": "2026-10-06T09:55:02.000+0000",
         "items": [{"field": "status", "fieldtype": "jira", "from": "10111", "fromString": "Pending Approval", "to": "10113", "toString": "Approved - Send"}]},
        {"id": "551305", "author": {"displayName": "Assurance Agent"}, "created": "2026-10-07T16:12:03.000+0000",
         "items": [{"field": "status", "fieldtype": "jira", "from": "10113", "fromString": "Approved - Send", "to": "10112", "toString": "Awaiting Carrier"},
                   {"field": "Carrier Dispute Ref", "fieldtype": "custom", "fieldId": CF["carrier_claim_id"], "from": None, "fromString": None, "to": None, "toString": DSP}]},
    ]}
    write("S6-case-store/04_jira_issue_SETL-482_changelog.json", jdump(changelog))

    fmap = {
        "tool": "jira", "site": JIRA, "project": "SETL", "issuetype": "Invoice Dispute",
        "confirmed_by": "analyst (Maria Gomez)", "confirmed_at": "2026-10-01",
        "fields": {k: v for k, v in CF.items()},
        "option_values": {"recommendation": {"approve": "Approve", "partial_dispute": "Partial dispute",
                                              "full_dispute": "Full dispute", "request_evidence": "Request evidence"}},
        "status_map": {"opened": "Triage", "awaiting_approval": "Pending Approval", "approved": "Approved - Send",
                       "submitted": "Awaiting Carrier", "carrier_rejected": "Carrier Rejected",
                       "resolved_credit": "Credit Received", "closed": "Closed"},
        "_note": "Our internal case shape stays fixed; this per-company map translates it to this Jira site.",
    }
    write("S6-case-store/05_field_map_orionmsg_jira.json", jdump(fmap))
    return issue


def s6_salesforce_and_sheet():
    sf = {
        "attributes": {"type": "Case", "url": "/services/data/v61.0/sobjects/Case/500Hn00001XyZ9aIAF"},
        "Id": "500Hn00001XyZ9aIAF", "CaseNumber": "00031877", "RecordTypeId": "012Hn000000ABCdIAK",
        "Subject": "Northbridge SMS %s - 7.41%% over expected" % INV,
        "Description": "Invoice 27,000.00 vs expected 25,000.00. Supported dispute 1,820.00 (Altavia Movil 334-990 SMS rate applied before 15 Sep).",
        "Status": "Submitted to Carrier", "Priority": "High", "Origin": "Assurance Agent",
        "Type": "Billing Dispute", "Reason": "Rate Applied Early",
        "AccountId": "001Hn00002NbCsQIAV", "OwnerId": "005Hn000004MgOmIAK",
        "CreatedDate": "2026-10-05T09:00:41.000+0000", "LastModifiedDate": "2026-10-07T16:12:03.000+0000",
        "IsClosed": False, "ParentId": None,
        "Product__c": "A2P SMS Termination",
        "Invoice_Number__c": INV, "Invoiced_Amount__c": 27000.00, "Expected_Amount__c": 25000.00,
        "Disputed_Amount__c": 1820.00, "Undisputed_Payable__c": 25180.00, "Carrier_Claim_ID__c": DSP,
        "Dispute_Deadline__c": "2026-10-19", "Recommendation__c": "Partial dispute", "Assurance_Case_ID__c": CASE,
    }
    write("S6-case-store/06_salesforce_case_00031877.json",
          jdump({"_request": "GET /services/data/v61.0/sobjects/Case/500Hn00001XyZ9aIAF", "body": sf}))
    write("S6-case-store/07_dispute_tracker.csv",
          "Carrier,Product,Invoice,Bill Month,Invoiced,Disputed,Reason,Sent,Carrier Ref,Status,Credit,Notes\n"
          'Northbridge,A2P SMS,%s,Sep-26,"27,000.00","1,820.00",Altavia rate early,06/10/2026,%s,Open,,Ack 07/10\n'
          'Atlas,A2P SMS,INV/26/0098812,Sep-26,"41,336.20","612.40",Dup MDRs,08/10/2026,,Sent,,chase if no ack by 12/10\n' % (INV, DSP))


# ================= S7 carrier dispute channel =================
def s7_form_csv():
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    rows = [["DISPUTE NOTIFICATION FORM - NORTHBRIDGE CARRIER SERVICES", "", ""],
            ["1. Customer name", BUYER, ""], ["2. Account number", ACCOUNT, ""],
            ["3. Contact name / email", "Settlements team", "settlements@" + DOM],
            ["4. Customer dispute ref", CASE, ""], ["5. Invoice number", INV, ""], ["6. Invoice date", "05/10/2026", ""],
            ["7. Bill period", "01/09/2026 - 30/09/2026", ""], ["8. Product", "A2P SMS termination", ""],
            ["9. Dispute type", "Rate", ""],
            ["10. Dispute reason", "Rate increase applied before effective date (Agreement cl. 6.3)", ""],
            ["11. Date submitted", "06/10/2026", ""], ["12. TOTAL DISPUTED AMOUNT (USD)", money(DISPUTED), ""],
            ["", "", ""], ["DETAIL", "", ""]]
    w.writerows(rows)
    w.writerow(["Destination network", "MCC-MNC", "From", "To", "Segments", "Billed rate", "Correct rate", "Billed amount",
                "Should-bill amount", "Difference"])
    w.writerow(["Mexico - Altavia Movil", "334-990", "01/09/2026", "14/09/2026", "{:,}".format(MX_EARLY_SEG), "0.0185", "0.0120",
                money(MX_EARLY_SEG * 0.0185), money(MX_EARLY_SEG * 0.0120), money(DISPUTED)])
    w.writerow(["TOTAL", "", "", "", "", "", "", "", "", money(DISPUTED)])
    return write("S7-carrier-dispute/02_NBCS_Dispute_Notification_Form.csv", buf.getvalue())


def s7_packet_pdf():
    p = Pdf("Dispute packet " + CASE)
    p.page()
    p.text(40, 790, "ORION MESSAGING EXCHANGE S.A. - SETTLEMENTS", 13, "B")
    p.text(40, 776, "Av. Corrientes 1234, C1043 Buenos Aires, Argentina   settlements@" + DOM, 8)
    p.text(555, 790, "DISPUTE PACKET", 15, "B", right=True)
    p.line(40, 765, 555, 765, 1.2)
    kv = [("Our reference", CASE), ("Carrier", "Northbridge Carrier Services Ltd"), ("Account", ACCOUNT),
          ("Invoice", INV + " dated 05/10/2026"), ("Agreement", CONTRACT + " (internal AGR-NB-2025-014)"),
          ("Product", "A2P SMS termination"),
          ("Disputed amount", "USD " + money(DISPUTED)), ("Undisputed, to be paid by 04/11/2026", "USD " + money(UNDISPUTED)),
          ("Dispute window", "14 days from invoice date (cl. 8.1): deadline 19/10/2026"), ("Submitted", "06/10/2026")]
    y = 742
    for k, v in kv:
        p.text(40, y, k, 9, "B")
        p.text(230, y, v, 9)
        y -= 14
    y -= 10
    p.text(40, y, "1. Claim", 11, "B")
    y -= 16
    for s in ["Invoice line 1 (Mexico - Altavia Movil, 334-990) bills 600,000 segments at USD 0.0185 for all of September.",
              "Rate notice RN-NB-2026-0907 was sent on 07/09/2026 14:00 UTC. Under clause 6.3 an increase takes effect",
              "at 00:00 UTC on the first full day at least 7 days after notice, so 15/09/2026 (also the deck effective date).",
              "From 01/09 to 14/09 the previous rate of USD 0.0120 applies (deck NB-SMS-2026-08-01, MCC-MNC 334990)."]:
        p.text(40, y, s, 9)
        y -= 12
    y -= 12
    p.text(40, y, "2. Calculation", 11, "B")
    y -= 18
    p.box(40, y - 4, 515, 16)
    heads = [(44, "Period", False), (200, "Segments", True), (280, "Billed", True), (360, "Correct", True),
             (440, "Billed amt", True), (551, "Difference", True)]
    for x, h, r in heads:
        p.text(x, y, h, 9, "B", right=r)
    y -= 18
    vals = ["01/09 - 14/09/2026", "{:,}".format(MX_EARLY_SEG), "0.0185", "0.0120", money(MX_EARLY_SEG * 0.0185), money(DISPUTED)]
    for (x, _, r), v in zip(heads, vals):
        p.text(x, y, v, 9, right=r)
    y -= 16
    p.text(40, y, "280,000 segments x (0.0185 - 0.0120) = USD 1,820.00", 9, "M")
    y -= 24
    p.text(40, y, "3. Evidence (our records, daily, Altavia Movil on SMPP-NB-01/02)", 11, "B")
    y -= 18
    p.box(40, y - 4, 515, 16)
    for x, h, r in [(44, "Date (UTC)", False), (200, "Messages", True), (300, "Segments", True), (420, "At 0.0120", True), (551, "At 0.0185", True)]:
        p.text(x, y, h, 9, "B", right=r)
    y -= 15
    for r in MX_DAYS:
        sg = int(r["billable_segments"])
        for x, v, rt in [(44, "%s/09/2026" % r["date_utc"][8:], False), (200, "{:,}".format(int(r["messages"])), True),
                         (300, "{:,}".format(sg), True), (420, money(sg * 0.0120), True), (551, money(sg * 0.0185), True)]:
            p.text(x, y, v, 8, right=rt)
        y -= 12
    p.line(40, y + 8, 555, y + 8)
    for x, v, r in [(44, "Total", False), (200, "{:,}".format(MX_EARLY_MSGS), True), (300, "{:,}".format(MX_EARLY_SEG), True),
                    (420, "3,360.00", True), (551, "5,180.00", True)]:
        p.text(x, y - 4, v, 8, "B", right=r)
    y -= 30
    p.text(40, y, "Attached: NBCS_Dispute_Notification_Form, MX-ALT_daily_usage_0901-0914.csv. Message detail available on request.", 8)
    p.text(40, y - 12, "Approved by: Maria Gomez, Settlements Analyst, 06/10/2026 09:55 UTC.", 8)
    p.line(40, 60, 555, 60)
    p.text(40, 48, "Orion Messaging Exchange S.A. - fictional company, prototype data only", 7)
    p.text(555, 48, "Page 1 of 1", 7, right=True)
    return write("S7-carrier-dispute/03_dispute-packet-CASE-0001.pdf", p.bytes())


def s7_emails(form, packet):
    usage = "date_utc,network_group,mcc_mnc,messages,billable_segments\n" + "".join(
        "%s,MX-ALT,334990,%s,%s\n" % (r["date_utc"], r["messages"], r["billable_segments"]) for r in MX_DAYS)
    write("S7-carrier-dispute/01_email_dispute_submission.eml", """Message-ID: <CASE-0001.20261006101504@{dom}>
Date: Tue, 06 Oct 2026 10:15:04 +0000
From: "Orion Messaging Exchange - Settlements" <settlements@{dom}>
To: disputes@northbridge.example
Cc: wholesale-billing@northbridge.example
Subject: Billing dispute - {acc} - {inv} - A2P SMS Sep 2026 - our ref {case}
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="==ORX_d17a=="

--==ORX_d17a==
Content-Type: text/plain; charset="us-ascii"

Hello Northbridge Billing,

Please find attached our dispute against invoice {inv} dated
05/10/2026, for USD 1,820.00. The Mexico - Altavia Movil (334-990) increase
(notice RN-NB-2026-0907) took effect on 15/09/2026 per clause 6.3, but line 1
bills 280,000 segments from 01/09 to 14/09 at the new rate. We will pay the
undisputed USD 25,180.00 by the due date.

Regards,
Orion Messaging Exchange - Settlements

--==ORX_d17a==
Content-Type: text/csv; name="NBCS_Dispute_Notification_Form.csv"
Content-Disposition: attachment; filename="NBCS_Dispute_Notification_Form.csv"
Content-Transfer-Encoding: base64

{form}

--==ORX_d17a==
Content-Type: application/pdf; name="dispute-packet-CASE-0001.pdf"
Content-Disposition: attachment; filename="dispute-packet-CASE-0001.pdf"
Content-Transfer-Encoding: base64

{packet}

--==ORX_d17a==
Content-Type: text/csv; name="MX-ALT_daily_usage_0901-0914.csv"
Content-Disposition: attachment; filename="MX-ALT_daily_usage_0901-0914.csv"
Content-Transfer-Encoding: base64

{usage}

--==ORX_d17a==--
""".format(acc=ACCOUNT, inv=INV, case=CASE, dom=DOM, form=b64(form), packet=b64(packet), usage=b64(usage.encode())))

    write("S7-carrier-dispute/04_email_ack_NB-DSP-0098341.eml", """Message-ID: <dsp0098341.ack@disputes.northbridge.example>
In-Reply-To: <CASE-0001.20261006101504@{dom}>
Date: Wed, 07 Oct 2026 16:09:51 +0100
From: "Northbridge Disputes" <disputes@northbridge.example>
To: settlements@{dom}
Subject: RE: Billing dispute - {acc} - {inv} [Ref: {dsp}]
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"

Your dispute has been received and logged.

Dispute ID:        {dsp}
Your reference:    {case}
Account:           {acc}
Product:           A2P SMS termination
Invoice / date:    {inv} / 05-Oct-2026
Date received:     06-Oct-2026
Amount disputed:   USD 1,820.00
Status:            Received - under review

Please quote the Dispute ID in all correspondence. We aim to resolve
disputes within 30 days of receipt.

Northbridge Carrier Services Ltd - Disputes Team
""".format(acc=ACCOUNT, inv=INV, case=CASE, dsp=DSP, dom=DOM))

    write("S7-carrier-dispute/05_email_rejected_total_mismatch_TESTCASE.eml", """Message-ID: <dsp0098341.rej@disputes.northbridge.example>
In-Reply-To: <CASE-0001.20261006101504@{dom}>
Date: Wed, 07 Oct 2026 11:42:07 +0100
From: "Northbridge Disputes" <disputes@northbridge.example>
To: settlements@{dom}
Subject: RE: Billing dispute - {acc} - {inv} [Ref: {dsp}] REJECTED
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"

TEST CASE ONLY: not part of the main CASE-0001 story. Shows a carrier rejecting
a form whose detail does not add up, to exercise the reopen path.

Dispute ID:   {dsp}
Status:       Rejected - incomplete
Reason:       Detail total (1,802.00) does not equal field 12 (1,820.00).

Please correct the form and resubmit quoting Dispute ID {dsp}.
The original submission date will be kept for the dispute window.

Northbridge Carrier Services Ltd - Disputes Team
""".format(acc=ACCOUNT, inv=INV, dsp=DSP, dom=DOM))


def s7_resolution_pdf():
    p = Pdf("Dispute resolution " + DSP)
    p.page()
    nb_letterhead(p, "DISPUTE RESOLUTION")
    p.text(40, 735, BUYER + " S.A. - Settlements", 9)
    p.text(40, 723, "settlements@" + DOM, 9)
    p.text(555, 735, "London, 20/10/2026", 9, right=True)
    p.text(40, 690, "DISPUTE RESOLUTION NOTIFICATION", 12, "B")
    kv = [("Dispute ID", DSP), ("Your ref", CASE), ("Account", ACCOUNT), ("Invoice", INV + " (05/10/2026)"),
          ("Product", "A2P SMS termination"),
          ("Date received", "06/10/2026"), ("Dispute type", "Rate"), ("Amount disputed", "USD 1,820.00"),
          ("Outcome", "RESOLVED - CUSTOMER FAVOUR"), ("Amount in customer favour", "USD 1,820.00"),
          ("Amount in Northbridge favour", "USD 0.00"), ("Credit period", "from 01/09/2026 through 14/09/2026"),
          ("Credit note", CN + ", applied to your next invoice")]
    y = 665
    for k, v in kv:
        p.text(40, y, k, 9, "B")
        p.text(220, y, v, 9, "B" if k == "Outcome" else "R")
        y -= 15
    y -= 15
    for s in ["Following review, we confirm that the Mexico - Altavia Movil (334-990) increase notified on",
              "07/09/2026 (RN-NB-2026-0907) should have applied from 15/09/2026 under clause 6.3 of the agreement.",
              "Segments submitted from 01/09/2026 to 14/09/2026 have been re-rated at USD 0.0120 per segment.",
              "", "This dispute is now closed. Please contact disputes@northbridge.example quoting the",
              "Dispute ID if you have any questions.", "", "Northbridge Carrier Services Ltd - Disputes Team"]:
        p.text(40, y, s, 9)
        y -= 13
    nb_footer(p)
    write("S7-carrier-dispute/06_NB-DSP-0098341_resolution_letter.pdf", p.bytes())


def s7_portal_json():
    write("S7-carrier-dispute/07_portal_claim_export_alt_carrier_profile.json", jdump({
        "_note": "Only for carriers that run a dispute portal. Same claim as the email route.",
        "ticketNumber": "DSP-2026-0098341", "accountNumber": ACCOUNT, "invoiceNumber": INV,
        "product": "A2P SMS", "category": "Billing - Rate", "subCategory": "Incorrect rate applied",
        "network": {"mccMnc": "334990", "name": "Mexico - Altavia Movil"},
        "disputedAmount": {"value": 1820.00, "currency": "USD"},
        "billPeriod": {"from": "2026-09-01", "to": "2026-09-30"},
        "claimPeriod": {"from": "2026-09-01", "to": "2026-09-14"},
        "customerReference": CASE, "status": "In Review", "createdOn": "2026-10-06T10:14:22+01:00",
        "attachments": ["NBCS_Dispute_Notification_Form.csv", "dispute-packet-CASE-0001.pdf"],
        "messages": [{"from": "Northbridge Billing", "at": "2026-10-07T09:02:10+01:00", "text": "Received, under review."}],
    }))


def main():
    pdf = s5_invoice_pdf()
    summary = s5_summary_csv()
    s5_invoice_eml(pdf, summary)
    s5_ubl_xml()
    s5_sftp_ctl(pdf, summary)
    s5_mdr_detail()
    s5_portal_notification()
    s5_credit_note_pdf()
    form = s7_form_csv()
    packet = s7_packet_pdf()
    s7_emails(form, packet)
    s7_resolution_pdf()
    s7_portal_json()
    issue = s6_jira()
    for a in issue["fields"]["attachment"]:  # real sizes of the files the issue links to
        a["size"] = len(packet) if a["filename"].endswith(".pdf") else len(form)
    write("S6-case-store/02_jira_issue_SETL-482.json",
          jdump({"_request": "GET %s/rest/api/3/issue/SETL-482" % JIRA, "body": issue}))
    s6_salesforce_and_sheet()
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print("%7d  %s" % (p.stat().st_size, p.relative_to(OUT)))


if __name__ == "__main__":
    main()

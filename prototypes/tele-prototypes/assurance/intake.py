"""Intake: turn an uploaded carrier invoice file into the invoice record the engine expects.

Three formats arrive from carriers in practice, and all three are accepted here:

  json  the native invoice record, already in engine shape
  xml   UBL 2.1 / EN 16931 e-invoice, the usual e-invoicing exchange format
  csv   the carrier's traffic summary, which carries volumes but no invoice header

A CSV has no issue date, agreement reference or party details, so the fields the engine requires
are resolved from the account's agreement in the reference data. Every such fill-in is returned as
an inference note and shown to the user, so nothing is quietly invented.
"""
import csv
import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from xml.etree import ElementTree

NS = {"cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
      "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"}


class IntakeError(ValueError):
    """The upload could not be read as a carrier invoice."""


def parse(filename, data, systems=None):
    """Returns (invoice, notes). `systems` is only needed to complete a CSV traffic summary."""
    name = (filename or "").lower()
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    head = text.lstrip()[:400]
    if name.endswith(".json") or head.startswith("{"):
        return _from_json(text), []
    if name.endswith(".xml") or head.startswith("<?xml") or "<Invoice" in head:
        inv = _from_ubl(text)
        notes = _resolve_parties(inv, systems)
        _require(inv)
        return inv, notes
    if name.endswith(".csv") or "," in head.split("\n", 1)[0]:
        return _from_csv(text, systems)
    raise IntakeError("Unsupported file. Upload the invoice as JSON, UBL XML or the carrier's CSV traffic summary.")


def _num(s):
    """Reads a figure the way carriers write them: 1,604,000 or 27,000.00 or 0.018500."""
    return Decimal(str(s).replace(",", "").replace("$", "").strip() or "0")


def _rate(s):
    """Rates are written to the deck's 4 decimals, so 0.006 and 0.0060 read the same."""
    d = _num(s)
    q = d.quantize(Decimal("0.0001"))
    return f"{q:f}" if q == d else f"{d:f}"


def _money(x):
    return f"{Decimal(x):.2f}"


# ---------- native JSON ----------

def _from_json(text):
    try:
        inv = json.loads(text)
    except ValueError as e:
        raise IntakeError(f"Not valid JSON: {e}")
    if not isinstance(inv, dict) or not isinstance(inv.get("lines"), list):
        raise IntakeError("JSON invoice needs an object with a `lines` array.")
    _require(inv)
    return inv


# Screening reads these before step 2's own required-field check can report on them, so an upload
# missing any of them is rejected here with a readable message instead of failing inside the engine.
SCREENING_NEEDS = ["invoice_id", "agreement_ref", "period_start", "period_end", "currency", "total", "issue_date"]


def _require(inv):
    missing = [k for k in SCREENING_NEEDS if not inv.get(k)]
    if missing:
        raise IntakeError("The invoice is missing " + ", ".join(missing) +
                          ". These are needed to find the contract and the billing period to check against.")


# ---------- UBL 2.1 e-invoice ----------

def _t(node, path, default=None):
    found = node.find(path, NS)
    return default if found is None or found.text is None else found.text.strip()


def _from_ubl(text):
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        raise IntakeError(f"Not valid XML: {e}")
    cur = _t(root, "cbc:DocumentCurrencyCode", "USD")
    lines = []
    for el in root.findall("cac:InvoiceLine", NS):
        desc = _t(el, "cac:Item/cbc:Description", "") or ""
        msgs = re.search(r"Messages:\s*([\d,]+)", desc)
        mcc = re.search(r"MCC-MNC:\s*([\d,\s]+)", desc)
        lines.append({
            "line_no": int(_t(el, "cbc:ID", len(lines) + 1)),
            "destination": _t(el, "cac:Item/cbc:Name", ""),
            "mcc_mnc": "|".join(m.strip() for m in mcc.group(1).split(",") if m.strip()) if mcc else "",
            "service": "sms",
            "period_start": _t(el, "cac:InvoicePeriod/cbc:StartDate"),
            "period_end": _t(el, "cac:InvoicePeriod/cbc:EndDate"),
            "messages": int(_num(msgs.group(1))) if msgs else 0,
            "segments": int(_num(_t(el, "cbc:InvoicedQuantity", "0"))),
            "rate": _rate(_t(el, "cac:Price/cbc:PriceAmount", "0")),
            "amount": _money(_num(_t(el, "cbc:LineExtensionAmount", "0"))),
        })
    if not lines:
        raise IntakeError("No <cac:InvoiceLine> elements found; is this a UBL invoice?")
    total = _t(root, "cac:LegalMonetaryTotal/cbc:PayableAmount", "0")
    taxable = _t(root, "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount", total)
    period = root.find("cac:InvoicePeriod", NS)
    return {
        "invoice_id": _t(root, "cbc:ID", ""),
        "issue_date": _t(root, "cbc:IssueDate"),
        "due_date": _t(root, "cbc:DueDate"),
        "seller_party": {
            "party_id": _t(root, "cac:AccountingSupplierParty/cac:Party/cbc:EndpointID", ""),
            "name": _t(root, "cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", ""),
            "tax_id": _t(root, "cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID", ""),
        },
        "buyer_party": {
            "party_id": _t(root, "cac:AccountingCustomerParty/cac:Party/cbc:EndpointID", ""),
            "name": _t(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", ""),
            "tax_id": _t(root, "cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID", ""),
        },
        "account_id": _t(root, "cbc:BuyerReference", ""),
        "agreement_ref": _t(root, "cac:ContractDocumentReference/cbc:ID", ""),
        "period_start": _t(period, "cbc:StartDate") if period is not None else lines[0]["period_start"],
        "period_end": _t(period, "cbc:EndDate") if period is not None else lines[0]["period_end"],
        "currency": cur,
        "line_count": len(lines),
        "subtotal": _money(_num(taxable)),
        "tax_total": _money(_num(total) - _num(taxable)),
        "total": _money(_num(total)),
        "lines": lines,
    }


# ---------- carrier CSV traffic summary ----------

CSV_HEADERS = ("destination network", "mcc-mnc", "service", "messages", "segments")


def _from_csv(text, systems):
    rows = list(csv.reader(io.StringIO(text)))
    header_at = next((i for i, r in enumerate(rows)
                      if [c.strip().lower() for c in r[:2]] == list(CSV_HEADERS[:2])), None)
    if header_at is None:
        raise IntakeError("No 'Destination Network, MCC-MNC, ...' header row found; is this a traffic summary?")
    cols = [c.strip().lower() for c in rows[header_at]]
    ix = {k: next((i for i, c in enumerate(cols) if c.startswith(k)), None)
          for k in ("messages", "segments", "rate", "charge")}

    preamble = " ".join(c for r in rows[:header_at] for c in r)
    invoice_id = _search(r"Invoice:\s*([A-Za-z0-9\-]+)", preamble, "invoice id", "Invoice: <id>")
    account_id = _search(r"\(([A-Z0-9\-]+)\)", preamble, "account id", "Customer: Name (ACCOUNT-ID)")
    start, end = _csv_period(preamble)
    currency = (re.search(r"\(([A-Z]{3})\)", " ".join(rows[header_at])) or
                re.search(r"Charge \(([A-Z]{3})\)", preamble))
    currency = currency.group(1) if currency else "USD"

    lines, total = [], Decimal("0")
    for r in rows[header_at + 1:]:
        if not r or not r[0].strip() or r[0].strip().upper() == "TOTAL":
            continue
        amount = _num(r[ix["charge"]]) if ix["charge"] is not None else Decimal("0")
        total += amount
        lines.append({
            "line_no": len(lines) + 1,
            "destination": r[0].strip(),
            "mcc_mnc": "|".join(m.strip() for m in r[1].split(",") if m.strip()),
            "service": "sms",
            "period_start": start, "period_end": end,
            "messages": int(_num(r[ix["messages"]])) if ix["messages"] is not None else 0,
            "segments": int(_num(r[ix["segments"]])) if ix["segments"] is not None else 0,
            "rate": _rate(r[ix["rate"]]) if ix["rate"] is not None else "0",
            "amount": _money(amount),
        })
    if not lines:
        raise IntakeError("The traffic summary has a header but no network rows.")

    inv = {"invoice_id": invoice_id, "account_id": account_id, "period_start": start, "period_end": end,
           "currency": currency, "line_count": len(lines), "subtotal": _money(total), "tax_total": "0.00",
           "total": _money(total), "lines": lines}
    carrier = rows[0][0].strip() if rows and rows[0] else ""
    notes = _complete_from_agreement(inv, carrier, systems)
    _require(inv)
    return inv, notes


def _resolve_parties(inv, systems):
    """Maps the exchange-format party identifiers onto our own.

    A UBL EndpointID is a tax-scheme identifier, not the party id our rating run is keyed by, so
    the carrier and buyer are resolved through the agreement named on the invoice.
    """
    if systems is None or not inv.get("agreement_ref"):
        return []
    agr = systems.contracts.find_by_ref(inv["agreement_ref"])
    if agr is None:
        return [f"No agreement on file matching reference {inv['agreement_ref']}; step 2 will flag it."]
    notes = []
    for role in ("seller_party", "buyer_party"):
        theirs, ours = inv.get(role) or {}, agr[role]
        if theirs.get("party_id") != ours["party_id"]:
            notes.append(f"{role.split('_')[0].title()} identified as {ours['party_id']} ({ours['name']}) "
                         f"via agreement {agr['document_number']}; the file carries the tax identifier "
                         f"{theirs.get('party_id') or 'none'}.")
        inv[role] = dict(theirs, party_id=ours["party_id"], name=ours["name"],
                         tax_id=theirs.get("tax_id") or ours.get("tax_id", ""))
    return notes


def _search(pattern, text, what, example):
    m = re.search(pattern, text)
    if not m:
        raise IntakeError(f"Could not read the {what} from the summary header (expected a line like '{example}').")
    return m.group(1)


def _csv_period(preamble):
    m = re.search(r"Period:\s*(\d{2})/(\d{2})/(\d{4})[^-]*-\s*(\d{2})/(\d{2})/(\d{4})", preamble)
    if not m:
        raise IntakeError("Could not read the billing period (expected 'Period: DD/MM/YYYY ... - DD/MM/YYYY ...').")
    d1, m1, y1, d2, m2, y2 = m.groups()
    return f"{y1}-{m1}-{d1}", f"{y2}-{m2}-{d2}"


SUFFIXES = ("ltd", "ltda", "limited", "inc", "llc", "plc", "gmbh", "sa", "s a", "sas", "bv", "nv", "ag", "pte", "co")


def _norm_party(name):
    """Company names for matching: lowercase, no punctuation, no trailing legal-form suffix."""
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words)


def _complete_from_agreement(inv, carrier_name, systems):
    """Fills the header fields a traffic summary cannot carry, from the carrier's agreement.

    A summary names the carrier but not the contract, so the agreement is matched on the seller's
    name and supplies the reference, the parties, and the invoice dates its own terms imply. Each
    fill-in is returned as a note so the user sees what came from the file and what came from us.
    """
    agr = _agreement_for_carrier(carrier_name, systems)
    if agr is None:
        raise IntakeError(
            f"No agreement on file for carrier {carrier_name!r}. A traffic summary carries no contract "
            "reference, so there is nothing to check it against. Upload the JSON or UBL invoice instead.")
    inv["agreement_ref"] = agr["document_number"]
    inv["seller_party"] = dict(agr["seller_party"])
    inv["buyer_party"] = dict(agr["buyer_party"])
    inv["currency"] = inv.get("currency") or agr["currency"]
    issue = date.fromisoformat(inv["period_end"]) + timedelta(days=int(agr["invoice_due_days_after_period"]))
    inv["issue_date"] = issue.isoformat()
    inv["due_date"] = (issue + timedelta(days=int(agr["payment_terms_days"]))).isoformat()
    return [
        f"Matched to agreement {agr['document_number']} ({agr['agreement_id']}) on the carrier name; a traffic "
        f"summary carries no contract reference. Parties and currency come from that contract.",
        f"Issue date taken as {inv['issue_date']} and payment due {inv['due_date']}, from the contract's own terms "
        f"(invoice {agr['invoice_due_days_after_period']} days after period end, {agr['payment_terms_days']} day "
        f"payment terms). The dispute deadline is counted from the issue date, so confirm it against the invoice.",
    ]


def _agreement_for_carrier(carrier_name, systems):
    want = _norm_party(carrier_name)
    if systems is None or not want:
        return None
    for name in systems.root.listdir("contracts/agreements"):
        agr = systems.contracts.get_agreement(name[:-5])
        if not agr:
            continue
        seller = _norm_party(agr["seller_party"]["name"])
        if seller and (want.startswith(seller) or seller.startswith(want)):
            return agr
    return None

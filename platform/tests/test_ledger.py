import pytest

from telco_platform.ledger import Ledger, LedgerError, calc_cite, numbers_in

RULES = {"kind": "rules", "name": "reconcile", "version": "1"}
AGENT = {"kind": "agent", "name": "cause-analyst", "version": "1"}
INVOICE = "sim://invoices/invoice/NB-INV-2026-09-0412/line/1"


def entry(**kw):
    base = {"case_id": "CASE-0001", "at": "2026-10-05T09:00:00Z", "step": 3, "actor": AGENT,
            "kind": "statement", "claim_class": "fact", "text": "Line 1 bills Mexico Mobile.", "cites": [INVOICE]}
    return {**base, **kw}


def calc(**kw):
    fields = {"actor": RULES, "kind": "calc", "claim_class": None, "text": "Supported MX-MOB dispute.",
              "calc": {"calc_id": "CALC-0001-001", "formula": "minutes * (billed - contracted)",
                       "inputs": {"minutes": "280000", "billed": "0.0185", "contracted": "0.0120"},
                       "result": "1820.00", "unit": "USD"}}
    return entry(**{**fields, **kw})


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path)


def test_append_assigns_ids_and_reads_back(ledger):
    a = ledger.append(entry())
    b = ledger.append(entry(text="Second."))
    assert (a["entry_id"], b["entry_id"]) == ("LED-0001-0001", "LED-0001-0002")
    assert [e["text"] for e in ledger.read("CASE-0001")] == ["Line 1 bills Mexico Mobile.", "Second."]


def test_agent_number_must_come_from_cited_calc(ledger):
    with pytest.raises(LedgerError, match="1820.00"):
        ledger.append(entry(text="We can dispute 1,820.00."))
    ledger.append(calc())
    ok = ledger.append(entry(text="We can dispute 1,820.00 at 0.0185 vs 0.0120.",
                             cites=[INVOICE, calc_cite("CALC-0001-001")]))
    assert ok["entry_id"] == "LED-0001-0002"


def test_agent_cannot_write_calc(ledger):
    with pytest.raises(LedgerError, match="rules actor"):
        ledger.append(calc(actor=AGENT))


def test_rules_check_may_state_record_values(ledger):
    ledger.append(entry(actor=RULES, kind="check", text="Invoice total 27,000.00 equals sum of lines."))


def test_cites_required_except_missing_evidence(ledger):
    with pytest.raises(LedgerError, match="cites may be empty"):
        ledger.append(entry(cites=[]))
    ledger.append(entry(cites=[], claim_class="missing_evidence", text="No carrier CDR detail for CO-MOB."))


def test_supersedes_must_exist_and_hypothesis_fields_allowed(ledger):
    with pytest.raises(LedgerError, match="supersedes"):
        ledger.append(entry(supersedes="LED-0001-0009"))
    first = ledger.append(entry(hypothesis_id="H1", contradicts=["sim://notices/notice/MN-NB-2026-0922"]))
    ledger.append(entry(supersedes=first["entry_id"], text="Corrected."))


def test_numbers_in_ignores_clause_numbers_and_dates():
    assert numbers_in("clause 6.3 on 2026-09-15, 600,000 minutes") == []
    assert [str(n) for n in numbers_in("94.0% fell to 68% and 1,820.00 USD")] == ["94.0", "68", "1820.00"]

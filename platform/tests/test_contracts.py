from pathlib import Path

import pytest
from pypdf import PdfReader

from telco_platform.contracts.compare import compare_terms
from telco_platform.contracts.extract import TermRule, extract_terms
from telco_platform.contracts.pdf import parse_contract
from telco_platform.contracts.schema import validate_agreement
from telco_platform.ledger import Ledger
from telco_platform.sim.render_pdf import render_markdown_pdf

SAMPLE = Path(__file__).parent / "fixtures" / "sample_agreement.md"
RULES = [
    TermRule("delivery_target_pct", r"at least ([\d.]+)%", "5.1"),
    TermRule("ack_minutes", r"within \w+ \((\d+)\) minutes", "5.2", int),
    TermRule("credit_pct", r"credits (\d+)%", None, int),
    TermRule("termination_days", r"terminate on (\d+) days"),
]


@pytest.fixture
def parsed(tmp_path):
    # Pad with filler clauses so the document runs onto a second page.
    filler = "\n\n".join(f"9.{i} **Filler {i}.** " + "Boilerplate text. " * 30 for i in range(1, 15))
    pdf = render_markdown_pdf(SAMPLE.read_text() + "\n\n" + filler, tmp_path / "agr.pdf")
    return parse_contract(pdf, "AGR-TST-SMS-001", system="suppliers")


def test_parse_finds_every_clause_with_cites(parsed):
    ids = [c.data["clause_id"] for c in parsed.clauses]
    assert ids[:4] == ["1.1", "5.1", "5.2", "7.1"] and len(ids) == 18
    c = parsed.clause("5.1")
    assert c.cite_id == "sim://suppliers/agreement/AGR-TST-SMS-001#clause-5.1"
    assert c.data["title"] == "Delivery target"
    assert c.data["text"].endswith("at least 48 hours in advance.")   # wrapped lines joined
    assert "Test Messaging Ltd" in parsed.preamble
    assert parsed.clause("9.14").data["page"] > 1


def test_extract_terms_cites_clause(parsed):
    terms, missing = extract_terms(parsed.clauses, RULES)
    assert {t.name: t.value for t in terms} == {"delivery_target_pct": "95.0", "ack_minutes": 15, "credit_pct": 10}
    assert missing == ["termination_days"]
    assert next(t for t in terms if t.name == "credit_pct").cite_id.endswith("#clause-7.1")


def test_compare_flags_mismatch_and_writes_ledger_check(parsed, tmp_path):
    terms, _ = extract_terms(parsed.clauses, RULES)
    record = {"delivery_target_pct": "95.00", "ack_minutes": 30, "sla_credit_pct": 10}
    mismatches = compare_terms(terms, record, {"credit_pct": "sla_credit_pct"})
    assert [(m.name, m.pdf_value, m.record_value) for m in mismatches] == [("ack_minutes", 15, 30)]
    entry = mismatches[0].ledger_entry("INC-0001", "2026-09-22T17:10:00Z", 2, "sim://suppliers/agreement/AGR-TST-SMS-001")
    assert Ledger(tmp_path).append(entry)["kind"] == "check"


def test_validate_agreement():
    good = {"agreement_id": "A", "document_number": "D", "name": "N", "agreement_type": "a2p_sms", "status": "active",
            "version": 1, "period_start": "2025-03-01", "buyer_party": {}, "seller_party": {}, "currency": "USD",
            "clauses": [{"clause_id": "5.1"}]}
    good["buyer_party"] = good["seller_party"] = {"party_id": "X"}
    assert validate_agreement(good) == []
    assert validate_agreement({**good, "status": "draft"}, extra=("sla",)) == ["missing sla", "unknown status 'draft'"]


def test_render_is_byte_reproducible(tmp_path):
    a = render_markdown_pdf(SAMPLE.read_text(), tmp_path / "a.pdf")
    b = render_markdown_pdf(SAMPLE.read_text(), tmp_path / "b.pdf")
    assert a.read_bytes() == b.read_bytes()
    # The creation date is fixed, not the wall clock, so re-runs on another day match too.
    assert PdfReader(str(a)).metadata.creation_date.year == 2026
    assert PdfReader(str(a)).metadata.creation_date.strftime("%m-%d") == "01-01"

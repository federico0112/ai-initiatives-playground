"""Supplier contracts: render the generated agreement to PDF, parse it with the shared parser,
extract the SMS SLA terms and check them against the structured agreement record."""

import json

import pytest

from contracts.terms import SLA_RULES, sla_record
from telco_platform.contracts.compare import compare_terms
from telco_platform.contracts.extract import extract_terms
from telco_platform.contracts.pdf import parse_contract
from telco_platform.sim.overlay import DataRoot


def load(sim, agreement_id):
    root = DataRoot(sim / "base")
    record = json.loads(root.require(f"suppliers/agreements/{agreement_id}.json").read_text())
    parsed = parse_contract(root.require(record["signed_pdf"]), agreement_id, system="suppliers")
    return record, parsed


def test_northbridge_pdf_terms_match_record(sim_data):
    record, parsed = load(sim_data, "AGR-NB-SMS-2025-031")
    terms, missing = extract_terms(parsed.clauses, SLA_RULES)
    assert missing == []
    assert compare_terms(terms, sla_record(record)) == []
    by_name = {t.name: t for t in terms}
    assert by_name["p1_restoration_minutes"].value == 240
    assert by_name["delivery_target_pct"].cite_id == "sim://suppliers/agreement/AGR-NB-SMS-2025-031#clause-4.1"


def test_pacifica_has_fewer_terms_and_they_match(sim_data):
    record, parsed = load(sim_data, "AGR-PR-SMS-2026-007")
    terms, missing = extract_terms(parsed.clauses, SLA_RULES)
    assert {"delivery_target_pct", "p1_raw_delivery_below_pct", "p1_response_minutes"} <= {t.name for t in terms}
    assert "service_credit_pct" in missing
    assert compare_terms(terms, sla_record(record)) == []


def test_cobalt_has_no_sla(sim_data):
    record, parsed = load(sim_data, "AGR-CB-SMS-2026-012")
    terms, _ = extract_terms(parsed.clauses, SLA_RULES)
    assert sla_record(record) == {}
    assert not any(t.name.startswith("p1_") for t in terms)


def test_changed_record_shows_up_as_mismatch(sim_data):
    record, parsed = load(sim_data, "AGR-NB-SMS-2025-031")
    record["sla"]["delivery_target_pct"] = "92.0"
    terms, _ = extract_terms(parsed.clauses, SLA_RULES)
    mismatches = compare_terms(terms, sla_record(record))
    assert [(m.name, str(m.pdf_value), m.record_value) for m in mismatches] == [("delivery_target_pct", "95.0", "92.0")]

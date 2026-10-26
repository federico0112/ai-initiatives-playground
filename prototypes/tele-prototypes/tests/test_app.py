"""Tests for the Assurance Resolution Agents prototype: endpoints, the ledger rules, the analyst
gate, and the base scenario scored against its answer key."""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from app import app  # noqa: E402
from assurance.ledger import Ledger, LedgerError  # noqa: E402
from assurance.runner import Clock, DATASET, list_scenarios, run_scenario  # noqa: E402
from assurance.store import D, DataRoot, Systems, parse_dt  # noqa: E402
from evaluate import evaluate, load_key  # noqa: E402
from version import __version__  # noqa: E402

SCENARIOS = ["base"]


@pytest.fixture(scope="session", autouse=True)
def dataset():
    """The simulated data is not committed; generate it once if it is missing (about a minute)."""
    if not os.path.exists(os.path.join(DATASET, "answer-keys", "base.json")):
        subprocess.run([sys.executable, os.path.join(DATASET, "generate_dataset.py")], check=True, cwd=DATASET,
                       stdout=subprocess.DEVNULL)


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "healthy"}


def test_version_endpoint(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert response.json["version"] == __version__
    assert len(__version__.split(".")) == 3


def test_index_lists_scenarios(client):
    response = client.get("/")
    assert response.status_code == 200
    for s in SCENARIOS:
        assert f"/scenario/{s}".encode() in response.data


def test_scenarios_found():
    assert list_scenarios() == SCENARIOS


@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_scenario_matches_answer_key(scenario_id):
    result = run_scenario(scenario_id)
    failed = [(name, detail) for name, ok, detail in evaluate(result, load_key(scenario_id)) if not ok]
    assert not failed


def test_every_amount_is_a_rules_calc():
    result = run_scenario("base")
    for e in result["ledger"]:
        if e["kind"] == "calc":
            assert e["actor"]["kind"] == "rules"
        else:
            assert "calc" not in e
        if e["kind"] == "statement":
            assert e["claim_class"] in ("fact", "calculation", "interpretation", "missing_evidence")


def _ledger(tmp_path):
    systems = Systems(DataRoot(os.path.join(DATASET, "sim-data", "base"), str(tmp_path)))
    return Ledger(systems, "CASE-0001", Clock(parse_dt("2026-10-05T09:00:00Z")))


def test_ledger_rejects_uncited_amount(tmp_path):
    led = _ledger(tmp_path)
    agent = {"kind": "agent", "name": "test", "version": "0"}
    with pytest.raises(LedgerError):
        led.append(4, agent, "statement", "The carrier overbilled by 1,820.00.", ["sim://invoices/invoice/X/line/1"],
                   claim_class="interpretation")
    c = led.calc(5, {"kind": "rules", "name": "t", "version": "0"}, "supported", "a x b", {}, D("1820.00"), "USD",
                 "Supported 1,820.00.", ["sim://invoices/invoice/X/line/1"])
    led.append(4, agent, "statement", "The carrier overbilled by 1,820.00.", [c], claim_class="calculation")


def test_ledger_rejects_agent_calc_and_missing_cites(tmp_path):
    led = _ledger(tmp_path)
    agent = {"kind": "agent", "name": "test", "version": "0"}
    with pytest.raises(LedgerError):
        led.calc(4, agent, "x", "guess", {}, D("1.00"), "USD", "x", ["sim://cases/case/CASE-0001"])
    with pytest.raises(LedgerError):
        led.append(4, agent, "statement", "Probably a billing error.", [], claim_class="interpretation")
    led.append(4, agent, "statement", "No rate notice on file.", [], claim_class="missing_evidence")


def test_case_stops_at_analyst_gate_until_decided(client):
    response = client.get("/scenario/base")
    assert response.status_code == 200
    assert b"analyst review" in response.data
    assert b"Return with notes" in response.data
    result = run_scenario("base", decisions=[])
    assert result["status"] == "analyst_review"
    assert result["submissions"] == []


def test_analyst_edit_lowers_claim(client):
    form = {"decisions": "[]", "action": "edit", "amount": "1500.00", "analyst": "maria", "reason": "keep goodwill"}
    response = client.post("/scenario/base/decide", data=form)
    assert response.status_code == 302
    page = client.get(response.headers["Location"])
    assert b"1,500.00" in page.data
    result = run_scenario("base", decisions=[{"action": "edit", "amount": "1500.00", "analyst": "maria"}])
    assert [s["disputed_amount"] for s in result["submissions"]] == ["1500.00"]
    assert result["case"]["analyst_decision"]["edits"] == {"approved_amount": {"from": "1820.00", "to": "1500.00"}}


def test_analyst_cannot_raise_amount_above_supported(client):
    decisions = json.dumps([{"action": "edit", "amount": "2000.00", "analyst": "maria"}])
    assert client.get("/scenario/base", query_string={"decisions": decisions}).status_code == 400


def test_return_revises_recommendation():
    result = run_scenario("base", decisions=[{"action": "return", "analyst": "maria", "reason": "check Colombia"},
                                             {"action": "approve", "analyst": "maria"}])
    assert result["status"] == "resolved"
    kinds = [(e["step"], e["kind"]) for e in result["ledger"]]
    assert kinds.count((6, "decision")) == 2
    assert result["case"]["notes"][0]["text"] == "check Colombia"


def test_api_run_returns_case(client):
    response = client.post("/api/run/base", json={})
    assert response.status_code == 200
    assert response.json["status"] == "resolved"

import json
import random
from datetime import timedelta
from decimal import Decimal

import pytest

from telco_platform.sim.clock import SimClock, iso, parse_duration
from telco_platform.sim.datagen import masked, q2, split_total, write_csv_gz
from telco_platform.sim.overlay import DataRoot
from telco_platform.sim.runner import Runner, deliver_file, load_scenario
from telco_platform.sim.visibility import mask_pending, visible


def write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def scenarios(tmp_path):
    """dataset/sim-data/{base,v1,v2}: v1 inherits 'sim-data/base' (prototype one style), v2 'base' (prototype two)."""
    sim = tmp_path / "dataset" / "sim-data"
    write(sim / "base/rates/deck.csv", "base")
    write(sim / "base/rates/notice.json", "{}")
    write(sim / "base/usage/rejects.csv", "base")
    write(sim / "v1/overlay.json", json.dumps({"inherits": "sim-data/base", "deleted": ["rates/deck.csv"]}))
    write(sim / "v1/usage/rejects.csv", "v1")
    write(sim / "v2/overlay.json", json.dumps({"inherits": "base", "deleted": []}))
    return sim


def test_overlay_resolution(scenarios):
    v1 = DataRoot(scenarios / "v1")
    assert v1.path("rates/deck.csv") is None
    assert v1.require("usage/rejects.csv").read_text() == "v1"
    assert v1.require("rates/notice.json").parent.parent.name == "base"
    assert v1.list("rates") == ["rates/notice.json"]
    assert DataRoot(scenarios / "v2").require("rates/deck.csv").read_text() == "base"
    with pytest.raises(FileNotFoundError):
        v1.require("rates/deck.csv")


def test_durations_and_clock():
    assert parse_duration("P3D") == timedelta(days=3)
    assert parse_duration("PT15M") == timedelta(minutes=15)
    assert parse_duration("P1DT2H") == timedelta(days=1, hours=2)
    with pytest.raises(ValueError):
        parse_duration("3 days")
    c = SimClock("2026-10-05T09:00:00Z")
    c.advance(timedelta(hours=1))
    assert iso(c.now()) == "2026-10-05T10:00:00Z"
    with pytest.raises(ValueError):
        c.advance_to("2026-10-05T09:00:00Z")


def test_visibility_hides_future_and_pending_fields():
    msgs = [{"id": 1, "submitted_at": "2026-09-22T17:00:00Z", "dlr_stat": "DELIVRD", "dlr_received_at": "2026-09-22T17:20:00Z"},
            {"id": 2, "submitted_at": "2026-09-22T18:00:00Z"}]
    seen = visible(msgs, "submitted_at", "2026-09-22T17:10:00Z")
    assert [m["id"] for m in seen] == [1]
    pending = mask_pending(seen[0], "dlr_received_at", ["dlr_stat"], "2026-09-22T17:10:00Z")
    assert pending["dlr_stat"] == "" and seen[0]["dlr_stat"] == "DELIVRD"
    assert mask_pending(seen[0], "dlr_received_at", ["dlr_stat"], "2026-09-22T17:30:00Z")["dlr_stat"] == "DELIVRD"


SCENARIO = {
    "id": "base",
    "sim_start": "2026-10-05T09:00:00Z",
    "events": [
        {"at": "2026-10-05T09:00:00Z", "action": "deliver_invoice", "invoice_id": "INV-1"},
        {"on": "evidence_request", "match": {"to": "carrier", "item": "cdr_detail"}, "after": "P3D",
         "action": "deliver_file", "from": "staged/detail.csv", "path": "invoices/detail.csv"},
        {"on": "portal_submit", "after": "P2D", "action": "portal_response", "response": {"status": "acknowledged"}},
        {"on": "portal_submit", "after": "P6D", "action": "portal_response", "response": {"status": "resolved"}},
        {"on": "escalation_submitted", "to": "SUP-NB", "at": "2026-10-05T12:00:00Z", "after": "PT1M",
         "action": "portal_response", "response": {"status": "late"}},
    ],
    "expected": {"supported": "1820.00"},
}


def test_runner_replays_scenario(tmp_path):
    write(tmp_path / "staged/detail.csv", "detail")
    log = []
    handlers = {
        "deliver_invoice": lambda e, r: log.append(("invoice", e["invoice_id"], iso(r.now))),
        "deliver_file": deliver_file(tmp_path),
        "portal_response": lambda e, r: log.append((e["response"]["status"], e["trigger"]["claim_id"], iso(r.now))),
    }
    r = Runner(SCENARIO, handlers)
    assert r.run_due()[0]["action"] == "deliver_invoice"
    assert r.expected == {"supported": "1820.00"}

    r.emit("evidence_request", to="internal_team", item="cdr_detail")   # filtered out
    r.emit("evidence_request", to="carrier", item="cdr_detail")
    r.wait()
    assert iso(r.now) == "2026-10-08T09:00:00Z"
    assert (tmp_path / "invoices/detail.csv").read_text() == "detail"

    r.emit("portal_submit", claim_id="CLM-00001")
    r.wait()
    r.wait()
    assert log[-2:] == [("acknowledged", "CLM-00001", "2026-10-10T09:00:00Z"),
                        ("resolved", "CLM-00001", "2026-10-14T09:00:00Z")]
    assert r.wait() == []


def test_runner_at_is_floor_and_to_filters(tmp_path):
    seen = []
    r = Runner({**SCENARIO, "events": SCENARIO["events"][-1:]},
               {"portal_response": lambda e, r: seen.append(iso(r.now))})
    r.emit("escalation_submitted", to="SUP-PR", claim_id="X")
    assert r.next_due() is None
    r.emit("escalation_submitted", to="SUP-NB", claim_id="X")
    r.wait(until="2026-10-05T11:00:00Z")
    assert seen == [] and iso(r.now) == "2026-10-05T11:00:00Z"
    r.wait()
    assert seen == ["2026-10-05T12:00:00Z"]


def test_runner_accepts_unquoted_on_key_from_yaml(tmp_path):
    f = tmp_path / "scenario.yaml"
    f.write_text("sim_start: 2026-10-05T09:00:00Z\n"
                 "events:\n"
                 "  - on: portal_submit\n"
                 "    after: P2D\n"
                 "    action: portal_response\n")
    fired = []
    r = Runner(load_scenario(f), {"portal_response": lambda e, r: fired.append(iso(r.now))})
    r.emit("portal_submit", claim_id="CLM-00001")
    r.wait()
    assert fired == ["2026-10-07T09:00:00Z"]


def test_runner_rejects_unhandled_actions():
    with pytest.raises(ValueError, match="deliver_invoice"):
        Runner(SCENARIO, {})


def test_datagen_helpers(tmp_path):
    parts = split_total(random.Random(1), 280000, 14, unit=1000)
    assert sum(parts) == 280000 and all(p % 1000 == 0 and p > 0 for p in parts)
    assert q2(Decimal("1.005")) == Decimal("1.01")
    assert masked("525512341234") == "52551234XXXX"
    write_csv_gz(tmp_path / "a.csv.gz", ["x"], [[1]])
    write_csv_gz(tmp_path / "b.csv.gz", ["x"], [[1]])
    assert (tmp_path / "a.csv.gz").read_bytes() == (tmp_path / "b.csv.gz").read_bytes()

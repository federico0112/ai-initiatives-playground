import pytest

from telco_platform.gate import NotApproved


def test_require_without_approval_refuses(gate):
    with pytest.raises(NotApproved, match="no analyst approval"):
        gate.require("CASE-0001", "portal_submit", {"amount": "1820.00"})


def test_approval_binds_payload(gate):
    payload = {"amount": "1820.00"}
    gate.approve("CASE-0001", "portal_submit", "analyst.a", "2026-10-06T10:00:00Z", payload)
    assert gate.require("CASE-0001", "portal_submit", payload)["analyst"] == "analyst.a"
    with pytest.raises(NotApproved, match="payload differs"):
        gate.require("CASE-0001", "portal_submit", {"amount": "2000.00"})
    with pytest.raises(NotApproved):
        gate.require("CASE-0001", "outbox_send", payload)


def test_latest_approval_wins(gate):
    gate.approve("CASE-0001", "portal_submit", "a", "t1", {"amount": "1"})
    gate.approve("CASE-0001", "portal_submit", "a", "t2", {"amount": "2"})
    gate.require("CASE-0001", "portal_submit", {"amount": "2"})

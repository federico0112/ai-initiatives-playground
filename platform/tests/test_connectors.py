import json

import pytest

from telco_platform.connectors import get_connector
from telco_platform.connectors.carrier_portal import portal_response_handler
from telco_platform.gate import NotApproved
from telco_platform.sim.overlay import DataRoot
from telco_platform.sim.runner import Runner

CASE = {"ticket_type": "invoice_discrepancy", "severity": "high", "status": "acknowledged", "sub_status": "opened",
        "creation_date": "2026-10-05T09:00:00Z", "correlation_id": "NBCS:NB-INV-2026-09-0412",
        "figures": {"variance": "2000.00"}}


@pytest.mark.parametrize("backend,external_file", [
    ("local", "cases/CASE-0001.json"),
    ("jira", "jira/issues/OPS-1.json"),
    ("salesforce", "salesforce/Case/00000001.json"),
])
def test_ticket_backends_share_one_interface(tmp_path, backend, external_file):
    t = get_connector("tickets", backend, root=tmp_path)
    rec = t.create(CASE)
    assert rec.cite_id == "sim://cases/case/CASE-0001"
    assert (tmp_path / external_file).exists()
    t.update("CASE-0001", {"status": "inProgress", "sub_status": "reconciling"}, "2026-10-05T09:05:00Z", "rules")
    t.add_note("CASE-0001", "analyst.a", "2026-10-05T10:00:00Z", "Checked trunk map.")
    got = t.get("CASE-0001").data
    assert got["status"] == "inProgress" and got["figures"] == {"variance": "2000.00"}
    assert got["notes"][0]["text"] == "Checked trunk map."
    assert [r.data["case_id"] for r in t.search(sub_status="reconciling")] == ["CASE-0001"]
    assert [h["event"] for h in t.history("CASE-0001")] == ["created", "updated", "note"]
    assert t.create(CASE).data["case_id"] == "CASE-0002"
    with pytest.raises(ValueError, match="TMF621"):
        t.update("CASE-0001", {"status": "open"}, "t", "x")


def test_jira_and_salesforce_payload_shapes(tmp_path):
    get_connector("tickets", "jira", root=tmp_path, project_key="INV").create(CASE)
    issue = json.loads((tmp_path / "jira/issues/INV-1.json").read_text())
    assert issue["fields"]["status"]["name"] == "Open" and issue["fields"]["priority"]["name"] == "High"
    get_connector("tickets", "salesforce", root=tmp_path, id_prefix="INC").create(CASE)
    sf = json.loads((tmp_path / "salesforce/Case/00000001.json").read_text())
    assert (sf["Status"], sf["Type"], sf["IsClosed"]) == ("New", "invoice_discrepancy", False)


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def test_inbox_folder_and_file_messages_respect_as_of(tmp_path):
    write_json(tmp_path / "base/invoices/inbox/INV-1/envelope.json", {"message_id": "m1", "received_at": "2026-10-05T09:00:00Z"})
    (tmp_path / "base/invoices/inbox/INV-1/summary.csv").write_text("x")
    write_json(tmp_path / "base/notices/N-1.json", {"notice_id": "N-1", "received_at": "2026-09-07T14:05:00Z"})
    write_json(tmp_path / "base/notices/N-2.json", {"notice_id": "N-2", "received_at": "2026-09-30T00:00:00Z"})
    root = DataRoot(tmp_path / "base")

    inv = get_connector("inbox", "local", data_root=root, folder="invoices/inbox", system="invoices", object_type="invoice")
    assert inv.list(as_of="2026-10-05T08:59:00Z") == []
    assert inv.get("INV-1", as_of="2026-10-05T09:00:00Z").cite_id == "sim://invoices/invoice/INV-1"
    assert inv.attachment("INV-1", "summary.csv").read_text() == "x"

    notices = get_connector("inbox", "local", data_root=root, folder="notices", system="notices", object_type="notice")
    assert [r.data["notice_id"] for r in notices.list(as_of="2026-10-01T00:00:00Z")] == ["N-1", "N-2"]
    assert [r.data["notice_id"] for r in notices.list(as_of="2026-10-01T00:00:00Z", since="2026-09-08T00:00:00Z")] == ["N-2"]
    with pytest.raises(KeyError):
        notices.get("N-2", as_of="2026-09-08T00:00:00Z")


def test_portal_requires_approval_and_gets_scripted_replies(tmp_path, gate):
    scenario = {"sim_start": "2026-10-06T10:00:00Z", "events": [
        {"on": "portal_submit", "after": "P2D", "action": "portal_response", "response": {"status": "acknowledged"}},
        {"on": "portal_submit", "after": "P6D", "action": "portal_response",
         "response": {"status": "resolved", "outcome": "customer_favour", "credit_amount": "1820.00"}}]}
    handlers = {}
    runner = Runner(scenario, {"portal_response": lambda e, r: handlers["respond"](e, r)})
    portal = get_connector("carrier_portal", "sim", root=tmp_path, gate=gate, on_submit=runner.emit)
    handlers["respond"] = portal_response_handler(portal)

    claim = {"reason_code": "RATE_BEFORE_EFFECTIVE", "disputed_amount": "1820.00"}
    with pytest.raises(NotApproved):
        portal.submit("CASE-0001", claim, "2026-10-06T10:00:00Z")
    gate.approve("CASE-0001", "portal_submit", "analyst.a", "2026-10-06T10:00:00Z", claim)
    sub = portal.submit("CASE-0001", claim, "2026-10-06T10:00:00Z")
    assert sub.data["claim_id"] == "CLM-00001"

    runner.wait()
    runner.wait()
    replies = portal.responses("CLM-00001")
    assert [r.data["status"] for r in replies] == ["acknowledged", "resolved"]
    assert replies[1].cite_id == "sim://carrier-portal/response/CLM-00001/2"
    assert [r.data["status"] for r in portal.responses("CLM-00001", as_of="2026-10-09T00:00:00Z")] == ["acknowledged"]


def test_outbox_is_gated(tmp_path, gate):
    out = get_connector("outbox", "local", root=tmp_path, gate=gate)
    msg = {"to": "routing-team", "subject": "Add TRK-NB-07 to trunk map", "body": "See case."}
    with pytest.raises(NotApproved):
        out.send("CASE-0001", msg, "t")
    gate.approve("CASE-0001", "outbox_send", "analyst.a", "t", msg)
    assert out.send("CASE-0001", msg, "t").cite_id == "sim://outbox/message/CASE-0001-MSG-001"
    assert len(out.list("CASE-0001")) == 1


def test_crm_contacts_by_role(tmp_path):
    write_json(tmp_path / "salesforce/Account/001A.json", {"Id": "001A", "Name": "Northbridge Messaging"})
    write_json(tmp_path / "salesforce/Contact/003A.json", {"Id": "003A", "AccountId": "001A", "Role__c": "NOC L1"})
    write_json(tmp_path / "salesforce/Contact/003B.json", {"Id": "003B", "AccountId": "001A", "Role__c": "Account Manager"})
    crm = get_connector("crm", "salesforce", data_root=DataRoot(tmp_path))
    assert crm.get_account("001A").cite_id == "sim://crm/account/001A"
    assert [c.data["Id"] for c in crm.contacts("001A", role="NOC L1")] == ["003A"]
    assert len(crm.contacts("001A")) == 2

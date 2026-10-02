"""Scenario definitions replay on the shared runner, and the shared connectors read the data as of sim time."""

from telco_platform.connectors.crm import SalesforceSimCRM
from telco_platform.connectors.inbox import LocalInbox
from telco_platform.sim.clock import iso
from telco_platform.sim.overlay import DataRoot
from telco_platform.sim.runner import Runner, load_scenario

ALERT = "2026-09-22T17:10:00Z"


def replay(sim, scn, submits):
    """Run a scenario with recording handlers; submits = [(event name, sim time)] the prototype would emit."""
    fired = []
    record = lambda e, r: fired.append((e["action"], iso(r.now), (e.get("response") or {}).get("status")))
    scenario = load_scenario(DataRoot(sim / scn).require("scenario.yaml"))
    runner = Runner(scenario, {"open_case_from_alarm": record, "supplier_response": record})
    runner.run_due()
    for name, at in submits:
        runner.wait(at)
        runner.emit(name, to="SUP-NB")
    runner.wait("2026-09-23T06:00:00Z")
    return fired


def test_base_supplier_replies(sim_data):
    fired = replay(sim_data, "base", [("escalation_submitted", "2026-09-22T17:25:00Z")])
    assert fired == [("open_case_from_alarm", ALERT, None),
                     ("supplier_response", "2026-09-22T17:40:00Z", "acknowledged"),
                     ("supplier_response", "2026-09-22T19:40:00Z", "resolved")]


def test_v2_needs_a_follow_up_for_the_real_answer(sim_data):
    no_follow_up = replay(sim_data, "v2", [("escalation_submitted", "2026-09-22T17:25:00Z")])
    assert [f[1] for f in no_follow_up[1:]] == ["2026-09-22T17:40:00Z", "2026-09-22T18:10:00Z"]
    with_follow_up = replay(sim_data, "v2", [("escalation_submitted", "2026-09-22T17:25:00Z"),
                                             ("follow_up_submitted", "2026-09-22T18:25:00Z")])
    assert with_follow_up[-1] == ("supplier_response", "2026-09-22T20:50:00Z", "resolved")


def test_no_escalation_means_no_reply(sim_data):
    assert replay(sim_data, "v3", []) == [("open_case_from_alarm", ALERT, None)]


def test_operator_notice_only_visible_in_v3_and_after_it_arrived(sim_data):
    def notices(scn, as_of):
        return [r.data["notice_id"] for r in LocalInbox(DataRoot(sim_data / scn), "notices", "notices", "notice").list(as_of)]
    assert notices("v3", ALERT) == ["MN-PR-2026-0910", "MN-NB-2026-0922", "MN-PR-2026-0922"]
    assert "MN-PR-2026-0922" not in notices("v3", "2026-09-22T15:00:00Z")
    assert "MN-PR-2026-0922" not in notices("base", ALERT)


def test_escalation_contacts_from_crm(sim_data):
    crm = SalesforceSimCRM(DataRoot(sim_data / "v2"))
    assert crm.get_account("SUP-NB").data["Agreement_Id__c"] == "AGR-NB-SMS-2025-031"
    [l2] = crm.contacts("SUP-NB", role="NOC L2")
    assert l2.data["Escalate_After__c"] == "PT2H"

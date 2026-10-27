"""Tests for the Assurance Resolution Agents prototype: endpoints, the ledger rules, the analyst
gate, and the base scenario scored against its answer key."""
import io
import json
import os
import subprocess
import sys
import tempfile

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


# ---- uploaded invoices (the product path: a file arrives, we check it against our records)

RAW = os.path.join(ROOT, "raw-examples", "out", "S5-carrier-invoice-inbox")
UBL = os.path.join(RAW, "04_NB-INV-2026-09-0412_ubl.xml")
CSV = os.path.join(RAW, "03_ORX_SMS_Traffic_Summary_Sep2026.csv")
needs_raw = pytest.mark.skipif(not os.path.exists(UBL), reason="run raw-examples/build_examples.py first")


@pytest.fixture(scope="session")
def native_invoice():
    path = os.path.join(DATASET, "sim-data", "base", "invoices", "inbox",
                        "NB-INV-2026-09-0412", "invoice.json")
    return json.load(open(path))


def post_invoice(client, name, data):
    return client.post("/upload", data={"invoice": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def test_upload_page_renders(client):
    assert client.get("/upload").status_code == 200


@needs_raw
@pytest.mark.parametrize("path", [UBL, CSV])
def test_upload_reproduces_the_seeded_findings(client, path):
    """Every accepted format must reach the same answer as the seeded scenario."""
    r = post_invoice(client, os.path.basename(path), open(path, "rb").read())
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "27,000.00" in body and "25,000.00" in body      # invoiced vs our rating run
    assert "1,820.00" in body and "partial dispute" in body  # the supported claim


@needs_raw
def test_upload_of_a_tampered_invoice_finds_the_extra_charge(client):
    """A changed rate must change the finding: this is a check, not a replay."""
    csv = open(CSV).read().replace('"211,950","250,000",0.021000,"5,250.00"',
                                   '"211,950","250,000",0.030000,"7,500.00"')
    csv = csv.replace('"1,604,000",,"27,000.00"', '"1,604,000",,"29,250.00"')
    body = post_invoice(client, "tampered.csv", csv.encode()).get_data(as_text=True)
    assert "29,250.00" in body          # the inflated total is read back
    assert "4,070.00" in body           # 1,820.00 rate error + 2,250.00 overcharge on Brazil


@needs_raw
def test_upload_within_tolerance_opens_no_case(client):
    csv = open(CSV).read().replace('"508,841","600,000",0.018500,"11,100.00"',
                                   '"508,841","600,000",0.015500,"9,300.00"')
    csv = csv.replace('"264,426","312,000",0.015000,"4,680.00"',
                      '"264,426","300,000",0.015000,"4,500.00"')
    csv = csv.replace('"1,604,000",,"27,000.00"', '"1,592,000",,"25,020.00"')
    body = post_invoice(client, "clean.csv", csv.encode()).get_data(as_text=True)
    assert "no case" in body and "No dispute is worth opening" in body


def test_upload_native_json_matches_the_scenario(client, native_invoice):
    body = post_invoice(client, "invoice.json", json.dumps(native_invoice).encode()).get_data(as_text=True)
    assert "1,820.00" in body and "partial dispute" in body


@pytest.mark.parametrize("name,data,expected", [
    ("junk.txt", b"not an invoice", "Unsupported file"),
    ("bad.json", b'{"invoice_id":"X","lines":[]}', "missing"),
    ("bad.csv", b"a,b\n1,2\n", "header row"),
    ("broken.xml", b"<Invoice><oops>", "Not valid XML"),
])
def test_unreadable_uploads_are_rejected_with_a_reason(client, name, data, expected):
    r = post_invoice(client, name, data)
    assert r.status_code == 400
    assert expected in r.get_data(as_text=True)


def test_upload_without_a_file_is_rejected(client):
    assert client.post("/upload", data={}, content_type="multipart/form-data").status_code == 400


@needs_raw
def test_ubl_party_ids_are_resolved_through_the_agreement(native_invoice):
    """A UBL EndpointID is a tax identifier; the rating run is keyed by our own party id."""
    from assurance import intake
    systems = Systems(DataRoot(os.path.join(DATASET, "sim-data", "base"), tempfile.mkdtemp()))
    inv, notes = intake.parse("ubl.xml", open(UBL, "rb").read(), systems)
    assert inv["seller_party"]["party_id"] == native_invoice["seller_party"]["party_id"]
    assert inv["buyer_party"]["party_id"] == native_invoice["buyer_party"]["party_id"]
    assert any("tax identifier" in n for n in notes)


@needs_raw
def test_csv_dates_come_from_the_contract_terms(native_invoice):
    """A traffic summary has no dates; the contract's own terms must reproduce the real ones."""
    from assurance import intake
    systems = Systems(DataRoot(os.path.join(DATASET, "sim-data", "base"), tempfile.mkdtemp()))
    inv, notes = intake.parse("summary.csv", open(CSV, "rb").read(), systems)
    assert inv["issue_date"] == native_invoice["issue_date"]
    assert inv["due_date"] == native_invoice["due_date"]
    assert len(notes) == 2


# ---- document viewer: a citation opens the record and its source, with the cited lines marked

def test_scenario_page_links_its_source_documents(client):
    body = client.get("/scenario/base").get_data(as_text=True)
    assert "Source documents" in body
    assert "NBCS-SMS-2025-0117" in body                     # the contract, by its document number
    assert "/doc?cite=sim://contracts/agreement/AGR-NB-2025-014" in body


@pytest.mark.parametrize("cite,expect", [
    ("sim://contracts/agreement/AGR-NB-2025-014", "document number"),
    ("sim://rates/deck/NB-SMS-2026-09-07/row/334990", "effective date"),
    ("sim://rates/notice/RN-NB-2026-0907", "notice id"),
    ("sim://usage/daily/2026-09-01/MX-ALT", "network group"),
    ("sim://rating/run/RUN-2026-09", "run id"),
])
def test_doc_shows_the_record_and_its_source(client, cite, expect):
    r = client.get("/doc", query_string={"cite": cite})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert expect in body
    assert "In the source document" in body


def test_doc_highlights_the_cited_clause_in_the_contract(client):
    body = client.get("/doc", query_string={
        "cite": "sim://contracts/agreement/AGR-NB-2025-014#clause-6.3"}).get_data(as_text=True)
    assert "contracts/documents/AGR-NB-2025-014.md" in body
    assert "Rate increase notice" in body
    assert 'class="ln hit"' in body          # the clause itself is marked, not just shown


def test_doc_highlights_the_cited_invoice_line_only(client):
    body = client.get("/doc", query_string={
        "cite": "sim://invoices/invoice/NB-INV-2026-09-0412/line/1"}).get_data(as_text=True)
    hits = body.count('class="ln hit"')
    assert 8 <= hits <= 14                   # the line-1 object, not the whole invoice


@pytest.mark.parametrize("cite", ["sim://nonsense/x", "not-a-cite", ""])
def test_unresolvable_citations_are_404(client, cite):
    assert client.get("/doc", query_string={"cite": cite}).status_code == 404


def test_every_calc_link_on_the_page_resolves(client):
    """Calc links carry the run that produced them; without that they 404."""
    import re as _re
    body = client.get("/scenario/base").get_data(as_text=True)
    links = sorted(set(_re.findall(r'href="(/doc\?cite=sim://ledger/calc/[^"]+)"', body)))
    assert links, "no calc links on the page"
    for href in links:
        assert client.get(href.replace("&amp;", "&")).status_code == 200, href
    assert client.get("/doc", query_string={"cite": "sim://ledger/calc/CALC-0001-011"}).status_code == 404


def test_a_calc_page_shows_the_sum_and_its_inputs(client):
    body = client.get("/scenario/base").get_data(as_text=True)
    import re as _re
    href = _re.search(r'href="(/doc\?cite=sim://ledger/calc/CALC-0001-011[^"]*)"', body).group(1)
    page = client.get(href.replace("&amp;", "&")).get_data(as_text=True)
    assert "280000 segments x (0.0185 - 0.0120)" in page     # the working, not a JSON blob
    assert "1,820.00" in page and "Built from" in page
    assert "invoice line 1" in page                          # inputs named readably
    assert "No source file on disk" not in page              # a calc has no file; saying so is noise


def test_clause_references_in_statements_are_linked(client):
    body = client.get("/scenario/base").get_data(as_text=True)
    assert "clause-6.3" in body and ">clause 6.3</a>" in body


# ---- CSV sources render as columns, with the deciding values marked

def test_csv_source_renders_as_a_table(client):
    body = client.get("/doc", query_string={
        "cite": "sim://usage/daily/2026-09-01/MX-ALT"}).get_data(as_text=True)
    assert 'class="src-table"' in body
    assert "billable segments" in body          # the header row, read as columns
    assert "As written in the file" in body     # raw text still available underneath


@pytest.mark.parametrize("cite,columns", [
    ("sim://rates/deck/NB-SMS-2026-09-07/row/334990", ["rate", "effective date", "change flag"]),
    ("sim://rates/deck_history/NB-SMS-2026-09-07", ["deck type", "received at", "load status"]),
    ("sim://usage/daily/2026-09-01/MX-ALT", ["messages", "billable segments"]),
])
def test_deciding_columns_are_called_out(client, cite, columns):
    body = client.get("/doc", query_string={"cite": cite}).get_data(as_text=True)
    assert "The finding turns on" in body
    for c in columns:
        assert f'<b class="keyname">{c}</b>' in body
    assert "class=key" in body or 'class="key"' in body


def test_csv_table_keeps_the_header_when_the_window_starts_past_it():
    """A row deep in a large file must still show what its columns mean."""
    from assurance.store import DataRoot, Systems
    from docview import source_view
    root = DataRoot(os.path.join(DATASET, "sim-data", "base"), tempfile.mkdtemp())
    systems = Systems(root)
    rejects = [k for k in (systems._build_index() or systems._index) if k.startswith("sim://usage/reject/")]
    view = source_view(root, systems.resolve(sorted(rejects)[-1]))
    assert view["lines"][0]["n"] > 1                     # the window skipped the header line
    assert view["table"]["header"][0] == "reject_id"     # but the header is still there


def test_quoted_csv_fields_stay_in_one_column():
    from docview import _csv_table
    text = ['a,b,c', 'x,"1,604,000",z']
    table = _csv_table("usage/x.csv", text, [{"n": 2, "text": text[1], "hit": True}])
    assert table["rows"][0]["cells"] == ["x", "1,604,000", "z"]


# ---- the summary states a zero remainder as accounted for, not as "0.00 unexplained"

def test_a_fully_explained_gap_is_not_reported_as_unexplained(client):
    body = client.get("/scenario/base").get_data(as_text=True)
    assert "Every cent of that gap is accounted for" in body
    # the misleading clause itself: a bolded zero presented as an outstanding amount
    assert "<b>0.00</b>" not in body
    assert "needs evidence before it can be claimed" not in body


# ---- the carrier's own deck files, reachable from the load record that names them

def test_deck_history_links_the_carrier_source_files(client):
    """The source_file column names what the carrier sent; it should open what was loaded."""
    body = client.get("/doc", query_string={
        "cite": "sim://rates/deck_history/NB-SMS-2026-09-07"}).get_data(as_text=True)
    for name, deck in [("NBCS_ORX_SMS_AZ_20260801.csv", "NB-SMS-2026-08-01"),
                       ("NBCS_ORX_SMS_PARTIAL_20260907.csv", "NB-SMS-2026-09-07")]:
        assert f'href="/doc?cite=sim://rates/deck/{deck}"' in body
        assert name in body


def test_whole_deck_shows_every_row(client):
    body = client.get("/doc", query_string={
        "cite": "sim://rates/deck/NB-SMS-2026-08-01"}).get_data(as_text=True)
    for network in ["Mexico - Altavia Movil", "Colombia - Andina Movil", "Guatemala - Quetzal Movil"]:
        assert network in body


@pytest.mark.parametrize("query,note", [
    ({"cite": "sim://rates/deck/NB-SMS-2026-09-07"}, "Marked: the rows this deck changed"),
    ({"cite": "sim://rates/deck/NB-SMS-2026-08-01"}, "It changed no rates"),
    ({"cite": "sim://rates/deck/NB-SMS-2026-08-01", "focus": "334990"}, "Marked: the row for 334990"),
])
def test_the_deck_note_says_what_was_actually_marked(client, query, note):
    assert note in client.get("/doc", query_string=query).get_data(as_text=True)


def test_focus_marks_the_asked_for_row(client):
    """The two decks side by side are the dispute: 0.0120 from 08-01, 0.0185 from 09-15."""
    body = client.get("/doc", query_string={
        "cite": "sim://rates/deck/NB-SMS-2026-08-01", "focus": "334990"}).get_data(as_text=True)
    marked = body[body.index("<tr class=hit"):]
    marked = marked[:marked.index("</tr>")]
    assert "334990" in marked and "0.0120" in marked and "2026-08-01" in marked
    assert "334991" not in marked            # only the focused network


def test_a_deck_row_links_to_its_whole_deck(client):
    body = client.get("/doc", query_string={
        "cite": "sim://rates/deck/NB-SMS-2026-09-07/row/334990"}).get_data(as_text=True)
    assert "sim://rates/deck/NB-SMS-2026-09-07&amp;focus=334990" in body


def test_unknown_deck_is_404(client):
    assert client.get("/doc", query_string={"cite": "sim://rates/deck/NOPE"}).status_code == 404


# ---- every CSV column explains itself

def test_every_csv_column_in_the_dataset_has_help_text():
    """A column with no definition would render a bare, unexplained header."""
    import glob
    import gzip
    from docview import COLUMN_HELP
    base = os.path.join(DATASET, "sim-data", "base")
    seen = set()
    for path in glob.glob(base + "/**/*.csv", recursive=True) + glob.glob(base + "/**/*.csv.gz", recursive=True):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            header = f.readline().strip().split(",")
        undefined = [h for h in header if h not in COLUMN_HELP]
        assert not undefined, f"{os.path.relpath(path, base)}: {undefined}"
        seen.update(header)
    assert not [k for k in COLUMN_HELP if k not in seen], "help text for columns that do not exist"


@pytest.mark.parametrize("cite,column,phrase", [
    ("sim://rates/deck/NB-SMS-2026-08-01", "mcc mnc", "Mobile Country Code"),
    ("sim://rates/deck/NB-SMS-2026-08-01", "change flag", "UNCHANGED, INCREASE"),
    ("sim://usage/daily/2026-09-01/MX-ALT", "no receipt", "bills on submit"),
    ("sim://rates/deck_history/NB-SMS-2026-09-07", "deck type", "complete A-Z"),
])
def test_column_headers_carry_their_explanation(client, cite, column, phrase):
    body = client.get("/doc", query_string={"cite": cite}).get_data(as_text=True)
    assert phrase in body
    assert f'data-tip="' in body and f'>{column}</th>' in body


def test_the_whole_header_cell_is_the_hover_target(client):
    """A wrapped label leaves a gap between its line boxes; the cell must still explain itself."""
    body = client.get("/doc", query_string={
        "cite": "sim://rates/deck/NB-SMS-2026-08-01"}).get_data(as_text=True)
    assert '<th class="term"' in body or '<th class="key term"' in body
    assert "<th><span class=\"term\"" not in body      # not an inner span with dead padding


# ---- a claim is a difference times a quantity; the steps must say so

def test_a_rate_claim_shows_the_per_segment_difference(client):
    """Written on one line, `280000 segments x (0.0185 - 0.0120)` reads as if money were being
    subtracted. The steps must show the subtraction is the overcharge on each segment."""
    import re as _re
    body = client.get("/scenario/base").get_data(as_text=True)
    href = _re.search(r'href="(/doc\?cite=sim://ledger/calc/CALC-0001-011[^"]*)"', body).group(1)
    page = client.get(href.replace("&amp;", "&")).get_data(as_text=True)
    assert "0.0185 − 0.0120 = 0.0065" in page          # the per-segment overcharge, on its own
    assert "280,000 × 0.0065 = 1,820.00 USD" in page   # then applied to the affected segments
    assert "overcharged on each one" in page
    assert "not claimed" in page                       # the rest of the line is not being claimed


@pytest.mark.parametrize("formula,result,expect", [
    ("280000 segments x (0.0185 - 0.0120)", "1820.00", "0.0185 − 0.0120 = 0.0065"),
    ("(312000 - 300000) x 0.0150", "180.00", "312,000 − 300,000 = 12,000 segments"),
    ("12000 segments x 0.0150", "180.00", "12,000 × 0.0150 = 180.00 USD"),
])
def test_formula_breakdowns(formula, result, expect):
    from explain import formula_steps
    steps = formula_steps(formula, result, "USD")
    assert steps and any(expect in expr for expr, _ in steps)


def test_a_plain_formula_is_left_alone():
    from explain import formula_steps
    assert formula_steps("invoiced - expected", "2000.00", "USD") is None
    assert formula_steps("invoice.total", "27000.00", "USD") is None


def test_every_calc_breakdown_sums_to_its_stated_result():
    """The steps are a retelling of the ledger's own arithmetic, so they must agree with it."""
    import shutil
    from decimal import Decimal
    from assurance.runner import run_scenario
    from explain import formula_steps
    r = run_scenario("base")
    shutil.rmtree(r["workspace"], ignore_errors=True)
    checked = 0
    for e in r["ledger"]:
        calc = e.get("calc")
        if not calc:
            continue
        steps = formula_steps(calc["formula"], calc["result"], calc["unit"])
        if not steps:
            continue
        final = steps[-1][0].rsplit("=", 1)[1].strip().split(" ")[0].replace(",", "")
        assert Decimal(final) == Decimal(calc["result"]), calc["formula"]
        checked += 1
    assert checked >= 3, "expected several broken-down calcs in the base case"

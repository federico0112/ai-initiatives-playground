"""Assurance Resolution Agents (Prototype 1): web front end for the invoice dispute flow.

Each scenario page replays the scenario with the analyst decisions taken so far (the run is
deterministic), so the app keeps no server-side case state.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections import OrderedDict
from functools import lru_cache

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from jinja2 import pass_context
from markupsafe import Markup, escape

from assurance import intake
from assurance.runner import DATASET, list_scenarios, load_scenario, run_invoice, run_scenario
from assurance.store import DataRoot, Systems
from docview import source_view
from explain import explain, formula_steps, verdict
from version import get_version_info

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # an invoice document, not a traffic dump

# The data an uploaded invoice is checked against: contracts, rate decks, our usage and rating run.
REFERENCE = "base"


@lru_cache(maxsize=1)
def reference_systems():
    """Read-only view of the reference data, for resolving contracts during intake."""
    return Systems(DataRoot(os.path.join(DATASET, "sim-data", REFERENCE), tempfile.mkdtemp(prefix="assurance-ref-")))


SCENARIO_NOTES = {
    "base": "The 8% invoice: early Mexico rate increase plus our own Colombia mediation rejects",
}

# Carrier-billing terms explained for a reader who does not work in telecom. One sentence each;
# rendered as hover/focus tooltips by the `term` template global.
GLOSSARY = {
    "invoiced": "What the carrier billed us for the month.",
    "expected": "What our own systems calculated we owe, priced from our record of the traffic we sent.",
    "variance": "Invoiced minus expected: the gap between the carrier's bill and our own number.",
    "segment": "The billing unit for SMS. A short text is 1 segment; a long one is split into several and each is charged.",
    "rate": "Price per segment for one destination network, taken from the carrier's rate deck.",
    "rate deck": "The carrier's price list: a rate per destination network, each with the date it takes effect.",
    "rating run": "Our monthly job that prices our own traffic records. Its total is the 'expected' figure.",
    "network": "The destination mobile operator. MCC-MNC is its global ID: country code + network code (334-990 = Mexico, Altavia Movil).",
    "supported": "The part of the gap we can prove is a billing error, with records behind it. This is what we claim back.",
    "carrier's favour": "Part of the gap caused by our own records being short, not by a carrier error, so we pay it.",
    "unresolved": "Gap that neither side's records explain yet. Above the threshold it triggers an evidence request.",
    "undisputed payable": "The part of the invoice we agree with and pay on time while the dispute is open.",
    "a2p sms": "Application-to-person SMS: texts sent by businesses (login codes, alerts), not person-to-person chat.",
    "reject": "A message our mediation system could not process, so it never reached our billing records, even though the carrier may have carried and billed it.",
    "mediation": "The system that collects raw traffic records and normalises them before billing.",
    "smpp bind": "A connection to the carrier for sending SMS. Traffic is tagged by the bind it went out on.",
    "delivery receipt": "Confirmation that the destination network delivered a message. Billing here is on submit, so undelivered messages are still payable.",
    "dispute deadline": "The last day the contract allows us to file a claim on this invoice.",
    "clause": "A numbered paragraph of the carrier agreement that a finding rests on.",
    "evidence ledger": "Append-only log of every statement and calculation in the case, each citing the records it rests on.",
    "reason code": "The carrier portal's standard label for why an amount is being disputed.",
    "case threshold": "Open a case only when the variance is a big enough share of the invoice to be worth working.",
}


@app.template_global()
def term(label, key=None):
    """Renders a term with its plain-language definition as a hover/focus tooltip."""
    tip = GLOSSARY.get((key or label).lower())
    if not tip:
        return Markup(escape(label))
    return Markup(f'<span class="term" tabindex="0" data-tip="{escape(tip)}">{escape(label)}</span>')


# Calculations live in a case ledger rather than the reference data, so a link to one has to name
# the run that produced it. Recent runs are kept here under a short token the links carry.
_RUNS = OrderedDict()
MAX_REMEMBERED_RUNS = 32


def remember_run(result, key=None):
    """Keeps a run reachable by token, so its calcs can be opened from the page that showed them."""
    token = hashlib.sha1((key or uuid.uuid4().hex).encode()).hexdigest()[:12]
    _RUNS[token] = result
    _RUNS.move_to_end(token)
    while len(_RUNS) > MAX_REMEMBERED_RUNS:
        _RUNS.popitem(last=False)
    return token


def cite_anchor(cite, label=None, run=None):
    """The link itself. Plain function, so other helpers can build links too."""
    if not cite or not str(cite).startswith("sim://"):
        return Markup(escape(label if label is not None else (cite or "")))
    url = url_for("doc", cite=cite, **({"run": run} if run else {}))
    kind, _ = describe_cite(cite)
    return Markup(f'<a class="cite" href="{escape(url)}" title="{escape(kind)}">'
                  f'{escape(label if label is not None else cite)}</a>')


@app.template_global()
@pass_context
def cite_link(ctx, cite, label=None, run=None):
    """Renders a citation as a link, carrying the page's run token so calc links resolve."""
    return cite_anchor(cite, label, run or ctx.get("run"))


SHORT_CITE = [
    (re.compile(r"rating/run/[^/]+/line/([\d-]+)/(\w+)$"), r"\1 · \2"),
    (re.compile(r"invoices/invoice/[^/]+/line/(\d+)$"), r"invoice line \1"),
    (re.compile(r"invoices/invoice/([^/]+)$"), r"invoice \1"),
    (re.compile(r"rates/deck/([^/]+)/row/(\w+)$"), r"\1 · \2"),
    (re.compile(r"contracts/agreement/[^#]+#clause-([\d.]+)$"), r"clause \1"),
    (re.compile(r"usage/daily/([\d-]+)/(\w+)$"), r"\1 · \2"),
    (re.compile(r"usage/reject/(\S+)$"), r"reject \1"),
]


@app.template_global()
def short_cite(cite):
    """A citation shortened to the part a reader needs, for lists of many of them."""
    tail = str(cite).replace("sim://", "")
    for pattern, repl in SHORT_CITE:
        if pattern.search(tail):
            return pattern.sub(repl, pattern.search(tail).group(0))
    return tail.rsplit("/", 1)[-1]


CLAUSE_REF = re.compile(r"\bclause (\d+(?:\.\d+)*)\b", re.I)


@app.template_global()
@pass_context
def link_clauses(ctx, text, agreement_id):
    """Turns 'clause 6.3' in a statement into a link to that clause in the agreement document."""
    if not text or not agreement_id:
        return Markup(escape(text or ""))
    run = ctx.get("run")
    out, at = [], 0
    for m in CLAUSE_REF.finditer(str(text)):
        out.append(escape(text[at:m.start()]))
        out.append(cite_anchor(f"sim://contracts/agreement/{agreement_id}#clause-{m.group(1)}", m.group(0), run))
        at = m.end()
    out.append(escape(text[at:]))
    return Markup("".join(str(x) for x in out))


def agreement_id_of(r):
    """The agreement a run's case rests on, for linking clause references in its statements."""
    for o in ((r or {}).get("case") or {}).get("related_objects") or []:
        if o["role"] == "agreement":
            return o["cite_id"].rsplit("/", 1)[1]
    return None


app.jinja_env.globals["agreement_id_of"] = agreement_id_of
app.jinja_env.globals["explain_cause"] = explain
app.jinja_env.globals["cause_verdict"] = verdict
app.jinja_env.globals["formula_steps"] = formula_steps


def _decisions(raw):
    try:
        d = json.loads(raw or "[]")
    except ValueError:
        abort(400, "decisions must be a JSON list")
    if not isinstance(d, list) or not all(isinstance(x, dict) and x.get("action") in ("approve", "edit", "return") for x in d):
        abort(400, "each decision needs action approve, edit or return")
    return d


@lru_cache(maxsize=64)
def _run(scenario_id, decisions_json):
    decisions = json.loads(decisions_json) if decisions_json is not None else None
    result = run_scenario(scenario_id, decisions=decisions)
    shutil.rmtree(result.pop("workspace"), ignore_errors=True)
    return result


def run_cached(scenario_id, decisions):
    if scenario_id not in list_scenarios():
        abort(404, f"unknown scenario {scenario_id}")
    return _run(scenario_id, None if decisions is None else json.dumps(decisions, sort_keys=True))


@app.route("/")
def index():
    scenarios = [{"id": s, "note": SCENARIO_NOTES.get(s, ""), "sim_start": str(load_scenario(s)["sim_start"])}
                 for s in list_scenarios()]
    return render_template("index.html", scenarios=scenarios, dataset=DATASET)


@app.route("/scenario/<scenario_id>")
def scenario(scenario_id):
    decisions = _decisions(request.args.get("decisions"))
    try:
        result = run_cached(scenario_id, decisions)
    except ValueError as e:
        abort(400, str(e))
    run = remember_run(result, key=f"scenario:{scenario_id}:{json.dumps(decisions, sort_keys=True)}")
    return render_template("scenario.html", r=result, note=SCENARIO_NOTES.get(scenario_id, ""),
                           decisions=decisions, decisions_json=json.dumps(decisions), run=run)


@app.route("/scenario/<scenario_id>/decide", methods=["POST"])
def decide(scenario_id):
    """Appends the analyst's decision and replays the scenario with it."""
    decisions = _decisions(request.form.get("decisions"))
    d = {"action": request.form.get("action"), "analyst": request.form.get("analyst") or "analyst",
         "reason": request.form.get("reason", "")}
    if d["action"] == "edit":
        d["amount"] = request.form.get("amount", "")
    decisions = _decisions(json.dumps(decisions + [d]))
    return redirect(url_for("scenario", scenario_id=scenario_id, decisions=json.dumps(decisions)))


@lru_cache(maxsize=1)
def _evaluation():
    from evaluate import evaluate, load_key
    rows = []
    for s in list_scenarios():
        result = run_scenario(s)
        checks = evaluate(result, load_key(s))
        shutil.rmtree(result["workspace"], ignore_errors=True)
        rows.append({"id": s, "checks": checks, "passed": sum(c[1] for c in checks)})
    return rows


@app.route("/evaluate")
def evaluate_page():
    return render_template("evaluate.html", rows=_evaluation())


@app.route("/api/scenarios")
def api_scenarios():
    return jsonify([{"id": s, "note": SCENARIO_NOTES.get(s, "")} for s in list_scenarios()])


@app.route("/api/run/<scenario_id>", methods=["POST"])
def api_run(scenario_id):
    body = request.get_json(silent=True) or {}
    decisions = body.get("decisions")
    if decisions is not None:
        decisions = _decisions(json.dumps(decisions))
    try:
        return jsonify(run_cached(scenario_id, decisions))
    except ValueError as e:
        abort(400, str(e))


# Sample carrier files to try the upload with, in the formats a carrier actually sends. Paths are
# relative to the prototype folder; each is only offered when it has been generated.
RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw-examples", "out", "S5-carrier-invoice-inbox")
DEMO_FILES = [
    ("04_NB-INV-2026-09-0412_ubl.xml", "UBL 2.1 e-invoice",
     "The EN 16931 XML carriers exchange. Carries the full invoice: header, parties, contract reference and lines."),
    ("03_ORX_SMS_Traffic_Summary_Sep2026.csv", "Traffic summary (CSV)",
     "Volumes and charges only. The contract, parties and invoice dates are resolved from the agreement on file."),
]


def demo_files():
    return [{"name": n, "label": label, "note": note} for n, label, note in DEMO_FILES
            if os.path.exists(os.path.join(RAW, n))]


@app.route("/upload", methods=["GET", "POST"])
def upload():
    """Checks an uploaded carrier invoice against the reference data and reports the discrepancies."""
    if request.method == "GET":
        return render_template("upload.html", files=demo_files(), reference=REFERENCE)

    f = request.files.get("invoice")
    if not f or not f.filename:
        return render_template("upload.html", files=demo_files(), reference=REFERENCE,
                               error="Choose an invoice file to check."), 400
    data = f.read()
    if not data:
        return render_template("upload.html", files=demo_files(), reference=REFERENCE,
                               error=f"{f.filename} is empty."), 400
    try:
        invoice, notes = intake.parse(f.filename, data, reference_systems())
        result = run_invoice(invoice, reference=REFERENCE)
    except intake.IntakeError as e:
        return render_template("upload.html", files=demo_files(), reference=REFERENCE, error=str(e)), 400
    except (KeyError, ValueError, TypeError) as e:
        return render_template("upload.html", files=demo_files(), reference=REFERENCE,
                               error=f"Could not check {f.filename}: {e}"), 400
    shutil.rmtree(result.pop("workspace"), ignore_errors=True)
    return render_template("review.html", r=result, notes=notes, filename=f.filename, reference=REFERENCE,
                           run=remember_run(result))


@app.route("/demo-files/<name>")
def demo_file(name):
    """Serves one of the sample carrier files so it can be downloaded and uploaded back."""
    if name not in {n for n, _, _ in DEMO_FILES}:
        abort(404)
    return send_from_directory(RAW, name, as_attachment=True)


DECK_CITE = re.compile(r"^sim://rates/deck/([A-Za-z0-9._-]+)$")
DECK_ROW_CITE = re.compile(r"^sim://rates/deck/([A-Za-z0-9._-]+)/row/([A-Za-z0-9|]+)$")

RECORD_KINDS = [
    ("sim://contracts/agreement/", "Agreement", "The signed contract. Its clauses decide which rate applies when."),
    ("sim://rates/deck_history/", "Rate deck load", "When a price list arrived and was loaded."),
    ("sim://rates/deck/", "Rate deck row", "The price for one destination network, and the date it takes effect."),
    ("sim://rates/notice/", "Rate notice", "The carrier's advance warning of a price change."),
    ("sim://invoices/invoice/", "Invoice", "The carrier's bill, as received."),
    ("sim://usage/daily/", "Our usage", "What we recorded sending, for one day and one network."),
    ("sim://usage/reject/", "Reject", "A message our mediation could not process."),
    ("sim://usage/connection_map/", "SMPP connection", "A sending connection and the dates it is mapped to a network."),
    ("sim://rating/run/", "Rating run", "Our own pricing of the period — the 'expected' total."),
    ("sim://rating/line/", "Rated line", "One line of our own pricing."),
    ("sim://ledger/calc/", "Calculation", "A figure the engine derived, with its formula and inputs."),
    ("sim://cases/case/", "Case", "The dispute case record."),
]


def describe_cite(cite):
    if DECK_CITE.match(cite):          # a whole deck, not one row of one
        return "Rate deck", "The price list as the carrier sent it."
    for prefix, label, note in RECORD_KINDS:
        if cite.startswith(prefix):
            return label, note
    return "Record", ""


def _calc_from_run(cite, run):
    """Finds a calculation in the ledger of the run that produced it."""
    result = _RUNS.get(run or "")
    if result is None:
        return None
    calc_id = cite.rsplit("/", 1)[1]
    for e in result.get("ledger") or []:
        if (e.get("calc") or {}).get("calc_id") == calc_id:
            return dict(e["calc"], cite_id=cite, text=e["text"], step=e["step"], at=e["at"],
                        actor=e["actor"], cites=e["cites"])
    return None


def _whole_deck(systems, deck_id, focus):
    """A price list as the carrier sent it, with the rows that matter marked.

    Marks the row for `focus` (an MCC-MNC) when given, otherwise every row the deck changed — on a
    partial deck that is the change itself. A full deck that moved nothing marks nothing, and says so.
    """
    history = systems.resolve(f"sim://rates/deck_history/{deck_id}")
    rows = systems.rates.get_deck_rows(deck_id) or []
    if not rows:
        return None, None, None
    focused = [r for r in rows if focus and focus in str(r.get("mcc_mnc", ""))]
    changed = [r for r in rows if str(r.get("change_flag", "")).upper() not in ("", "UNCHANGED")]
    wanted = focused or changed
    if focused:
        note = f"The price list as the carrier sent it. Marked: the row for {focus}, the network in question."
    elif changed:
        note = "The price list as the carrier sent it. Marked: the rows this deck changed."
    else:
        note = "The price list as the carrier sent it. It changed no rates — it is the baseline deck."
    stand_in = {"source": {"path": f"rates/decks/{deck_id}.csv", "line": None}}
    view = source_view(systems.root, stand_in,
                       highlight=[r["source"]["line"] for r in wanted if r["source"].get("line")])
    return (history or {"deck_id": deck_id, "rows": len(rows)}), view, note


@app.route("/doc")
def doc():
    """Shows the record behind a citation, and the file it came from with the cited lines marked."""
    cite = request.args.get("cite", "").strip()
    run = request.args.get("run")
    if not cite.startswith("sim://"):
        abort(404)
    label, note = describe_cite(cite)
    deck = DECK_CITE.match(cite)
    if deck:
        record, view, note = _whole_deck(reference_systems(), deck.group(1), request.args.get("focus"))
        if record is None:
            abort(404)
        return render_template("document.html", cite=cite, label="Rate deck", note=note, view=view,
                               record={k: v for k, v in record.items() if k not in ("cite_id", "source")},
                               run=run)
    related = []
    row = DECK_ROW_CITE.match(cite)
    if row:
        related.append({"href": url_for("doc", cite=f"sim://rates/deck/{row.group(1)}", focus=row.group(2)),
                        "text": "See this row in the whole deck, as the carrier sent it"})
    if cite.startswith("sim://ledger/calc/"):
        record, view = _calc_from_run(cite, run), None
    else:
        systems = reference_systems()
        record = systems.resolve(cite)
        view = source_view(systems.root, record) if record else None
    if record is None:
        return render_template("document.html", cite=cite, label=label, note=note, record=None,
                               view=None, run=run, related=related), 404
    return render_template("document.html", cite=cite, label=label, note=note,
                           record={k: v for k, v in record.items() if k not in ("cite_id", "source")},
                           view=view, run=run, related=related)


@app.route("/health")
def health():
    return {"status": "healthy"}


@app.route("/version")
def version():
    return get_version_info()


@app.template_filter("money")
def money_filter(v):
    from assurance.store import money
    return "" if v in (None, "") else money(v)


@app.template_filter("qty")
def qty_filter(v):
    """Counts with thousands separators, so 600000 segments reads as 600,000."""
    return "" if v in (None, "") else f"{int(v):,}"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

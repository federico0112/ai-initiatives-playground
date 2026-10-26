"""Assurance Resolution Agents (Prototype 1): web front end for the invoice dispute flow.

Each scenario page replays the scenario with the analyst decisions taken so far (the run is
deterministic), so the app keeps no server-side case state.
"""
import json
import os
import shutil
from functools import lru_cache

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from assurance.runner import DATASET, list_scenarios, load_scenario, run_scenario
from version import get_version_info

app = Flask(__name__)

SCENARIO_NOTES = {
    "base": "The 8% invoice: early Mexico rate increase plus our own Colombia mediation rejects",
}


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
    return render_template("scenario.html", r=result, note=SCENARIO_NOTES.get(scenario_id, ""),
                           decisions=decisions, decisions_json=json.dumps(decisions))


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

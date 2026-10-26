# Telecom prototypes: Assurance Resolution Agents (Prototype 1)

Planning material for Prototype 1, Assurance Resolution Agents: an exception-resolution
companion for wholesale carrier invoice disputes (invoice ~8% above expected). The simulated case is
a wholesale A2P SMS termination invoice: per-segment rates by destination network (MCC-MNC).

| Path | What it is |
|------|------------|
| `flow-diagram.html` | Case flow diagram (copy of https://claude.ai/artifact/XLPHPA893GkoasUvaCZiqi) |
| `industry-records-research.md` | Research notes on real-world record formats |
| `simulated-systems.md` | Spec for the simulated external systems (invoices, contracts, rates, usage, cases, ledger, carrier portal) |
| `dataset/` | Synthetic dataset generator and checker; the data and answer keys are generated locally, not committed (see `dataset/README.md`) |
| `raw-examples/build_examples.py` | Raw S5-S7 example files (invoice email/PDF/XML, Jira/Salesforce case, dispute form, letters) built from the dataset; writes `raw-examples/out/` |
| `test-case/build_test_case.py` | One full-size S1-S7 test case (raw files per system, industry-size message files) plus a zip of the large files; writes `test-case/out/` |

Generate and check the dataset (writes `dataset/sim-data/` and `dataset/answer-keys/`, both git-ignored):

```bash
cd dataset
python generate_dataset.py
python verify_dataset.py
cd ../raw-examples && python build_examples.py      # needs the dataset
cd ../test-case && python build_test_case.py        # needs the dataset and raw examples (~1 min, ~80 MB)
```

## The prototype

A Flask app that runs the seven-step invoice dispute flow over the simulated systems S1-S7, one
scenario at a time, and stops at the analyst gate.

| Path | What it is |
|------|------------|
| `assurance/store.py` | Adapters for S1-S7 with `sim://` citations and `resolve(cite_id)` |
| `assurance/ledger.py` | Append-only evidence ledger. Only `rules` calcs produce amounts; any other entry may quote an amount only if it cites the calc that produced it |
| `assurance/steps.py` | Steps 1-6: open case, validate, reconcile, investigate causes, size exposure, recommend; plus the claims and case packet for step 7 |
| `assurance/runner.py` | Scenario runner: sim clock, evidence requests and staged deliveries, analyst gate, S7 submission and scripted carrier responses |
| `evaluate.py` | Scores the test case against `dataset/answer-keys/base.json` (the only code that reads it) |
| `app.py`, `templates/` | Web UI: case view per step, analyst approve / edit amount / return, answer-key score |

Steps 4 and 6 (the agent steps) are a fixed policy for now, so every scenario reproduces to the
cent. A model can take over their wording later through the same ledger checks.

```bash
pip install -r requirements.txt
python dataset/generate_dataset.py   # once, about a minute
python evaluate.py                   # the test case against its answer key
python evaluate.py --packet base     # print the dispute packet for one scenario
python app.py                        # http://localhost:8080
pytest tests/test_app.py -v          # generates the data first if it is missing
```

There is one test case, `base` (the Northbridge September 2026 invoice): partial dispute 1,820.00
(Mexico rate applied before 15 Sep); Colombia 180.00 is our own rejects on SMPP-NB-07, paid.

The Docker image generates the simulated data at build time, since the data is not committed.

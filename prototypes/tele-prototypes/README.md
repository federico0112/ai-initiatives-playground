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
| `assurance/store.py` | Adapters for S1-S7 with the overlay rule, `sim://` citations and `resolve(cite_id)` |
| `assurance/ledger.py` | Append-only evidence ledger. Only `rules` calcs produce amounts; any other entry may quote an amount only if it cites the calc that produced it |
| `assurance/steps.py` | Steps 1-6: open case, validate, reconcile, investigate causes, size exposure, recommend; plus the claims and case packet for step 7 |
| `assurance/runner.py` | Scenario runner: sim clock, evidence requests and staged deliveries, analyst gate, S7 submission and scripted carrier responses |
| `evaluate.py` | Scores every scenario against `dataset/answer-keys/` (the only code that reads them) |
| `app.py`, `templates/` | Web UI: scenario list, case view per step, analyst approve / edit amount / return, answer-key scores |

Steps 4 and 6 (the agent steps) are a fixed policy for now, so every scenario reproduces to the
cent. A model can take over their wording later through the same ledger checks.

```bash
pip install -r requirements.txt
python dataset/generate_dataset.py   # once, about a minute
python evaluate.py                   # base and v1-v6 against the answer keys
python evaluate.py --packet base     # print the dispute packet for one scenario
python app.py                        # http://localhost:8080
pytest tests/test_app.py -v          # generates the data first if it is missing
```

| Scenario | Outcome |
|---|---|
| base | Partial dispute 1,820.00 (Mexico rate applied before 15 Sep); Colombia 180.00 is our own rejects on SMPP-NB-07, paid |
| v1 | Partial dispute 1,820.00; 180.00 unresolved but below the 1% evidence threshold |
| v2 | Request carrier message detail for Colombia; on arrival, partial dispute 2,240.00 (1,820.00 rate + 420.00 duplicate IDs) |
| v3 | Partial dispute 2,470.00 (notice sent late, increase only valid from 20 Sep) |
| v4 | Step 2 blocks: deck file missing, ask pricing for the deck and notice |
| v5 | Step 2 blocks: invoice lines do not add up, ask the carrier for a corrected invoice |
| v6 | No case |

The Docker image generates the simulated data at build time, since the data is not committed.

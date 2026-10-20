# Telecom prototypes: planning and simulated systems

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

This folder is not yet a deployable Flask prototype (no `app.py`, `Dockerfile`, etc.).

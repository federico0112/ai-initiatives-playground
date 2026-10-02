# Telecom prototypes: planning and simulated systems

Planning material for Prototype 1, Assurance Resolution Agents: an exception-resolution
companion for wholesale carrier invoice disputes (invoice ~8% above expected).

| Path | What it is |
|------|------------|
| `flow-diagram.html` | Case flow diagram (copy of https://claude.ai/artifact/XLPHPA893GkoasUvaCZiqi) |
| `industry-records-research.md` | Research notes on real-world record formats |
| `simulated-systems.md` | Spec for the simulated external systems (invoices, contracts, rates, usage, cases, ledger, carrier portal) |
| `dataset/` | Synthetic dataset generator and checker; the data and answer keys are generated locally, not committed (see `dataset/README.md`) |

Generate and check the dataset (writes `dataset/sim-data/` and `dataset/answer-keys/`, both git-ignored):

```bash
cd dataset
python generate_dataset.py
python verify_dataset.py
```

This folder is not yet a deployable Flask prototype (no `app.py`, `Dockerfile`, etc.).

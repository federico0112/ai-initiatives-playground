# Telecom prototypes: planning and simulated systems

Planning material for Prototype 1, Assurance Resolution Agents: an exception-resolution
companion for wholesale carrier invoice disputes (invoice ~8% above expected).

| Path | What it is |
|------|------------|
| `flow-diagram.html` | Case flow diagram (copy of https://claude.ai/artifact/XLPHPA893GkoasUvaCZiqi) |
| `industry-records-research.md` | Research notes on real-world record formats |
| `simulated-systems.md` | Spec for the simulated external systems (invoices, contracts, rates, usage, cases, ledger, carrier portal) |
| `dataset/` | Synthetic dataset: generator, checker, scenario data roots and answer keys (see `dataset/README.md`) |

Rebuild and check the dataset:

```bash
cd dataset
python generate_dataset.py
python verify_dataset.py
```

This folder is not yet a deployable Flask prototype (no `app.py`, `Dockerfile`, etc.).

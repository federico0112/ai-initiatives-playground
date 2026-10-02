# Operations Resolution Agents (prototype two)

Case: A2P SMS delivery to Mexico / Altavia Movil (a fictional network) falls from 94% to 68%.
The agents assemble the incident evidence, identify the probable owner, prepare a supplier
escalation for operations approval and verify recovery before closing.

This folder holds the planning material and the simulation that tests will run against. There is
no Flask app yet. Shared machinery (contract PDF parsing, Jira/Salesforce-shaped connectors, inbox,
portal, approval gate, ledger, overlays, scenario runner) comes from [`platform/`](../../platform).

| Path | What it is |
|------|------------|
| `flow-diagram.html` | Case flow diagram (copy of https://claude.ai/artifact/6hUKkCFc4gVKXdUWUQ8uB7) |
| `industry-records-research.md` | How the industry represents receipts, error codes, routes, SLAs, notices and tickets |
| `simulated-systems.md` | Spec for the simulated systems, the rules, the seeded case and the hidden cases |
| `contracts/terms.py` | SMS SLA terms read from supplier contract PDFs with the shared parser |
| `dataset/` | Generator and verifier for the simulated data and test cases (see `dataset/README.md`) |
| `tests/` | Generates every scenario at 10% volume, verifies it, parses the contracts, replays the scenarios |

Generated data is never committed.

```bash
pip install -e platform                                   # from the repo root
python prototypes/operations-resolution/dataset/generate_dataset.py
python prototypes/operations-resolution/dataset/verify_dataset.py
pytest prototypes/operations-resolution/tests -v
```

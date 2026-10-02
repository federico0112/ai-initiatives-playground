# telco-platform

Shared code for the telecom resolution prototypes (Assurance Resolution, Operations Resolution).
Both prototypes open a case, pull records from external systems through adapters, cite every
record, let rules compute and agents interpret, stop at an analyst gate, then send one packet
and evaluate the reply. This package holds that common machinery; each prototype keeps its own
domain logic (invoices and rate decks, or delivery receipts and routing) in its own folder.

Import name is `telco_platform` (a package named `platform` would shadow Python's standard library).

## Install

```bash
pip install -e platform            # from the repo root, for local development
pytest platform/tests -v
```

## What is in it

| Module | What it does |
|---|---|
| `citations` | `sim://<system>/<type>/<id>[#fragment]` cite IDs, `Record` (data + source file/line), `Resolver` |
| `ledger` | Append-only evidence ledger per case. Only `rules` actors write `calc` entries; an agent entry that states a number must cite the calc it came from |
| `gate` | Analyst approval records. `require(case, action, payload)` refuses without an approval of that exact payload |
| `connectors.tickets` | One `TicketSystem` interface (TMF621-shaped case) with `local`, `jira` and `salesforce` simulated backends that store real Jira issue / Salesforce Case payloads |
| `connectors.inbox` | Read-only mailbox for invoices, rate notices, maintenance notices, carrier emails; as-of aware |
| `connectors.carrier_portal` | Gated submit to the carrier (dispute claim or escalation ticket) with scenario-scripted replies |
| `connectors.outbox` | Gated internal notes (routing team, account manager) |
| `connectors.crm` | Salesforce-shaped Accounts and Contacts (who to escalate to) |
| `contracts` | PDF -> numbered clauses with cites (`pdf`), rule-based term extraction (`extract`), PDF vs agreement record comparison (`compare`), base agreement schema (`schema`) |
| `sim` | Overlay data roots (`overlay`), sim clock (`clock`), as-of visibility (`visibility`), scenario runner (`runner`), generator helpers (`datagen`), Markdown contract -> PDF (`render_pdf`) |
| `evals.harness` | Compare a run against a scenario's `expected` block, to the cent |

Backends are picked by config through the registry:

```python
from telco_platform.connectors import get_connector

tickets = get_connector("tickets", "jira", root=data_dir, project_key="OPS")
```

Real Jira and Salesforce clients are not included yet. A real backend replaces the file I/O in
the simulated one and keeps its `to_external` / `from_external` mapping, so prototypes and agents
do not change. Credentials would come from GCP Secret Manager via the prototype's `cloud-run.yaml`.

## Simulated data

Generated data is never committed. Each prototype's `dataset/generate_dataset.py` writes its
scenarios to a target folder using `sim.datagen`; tests generate what they need into a temp folder.
Contracts are generated as Markdown and rendered to PDF with `sim.render_pdf`, so the parser is
always tested against a real PDF.

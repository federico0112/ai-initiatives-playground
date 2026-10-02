# Simulated data and test cases: SMS delivery incident (prototype two)

Scripts only. The data is generated on demand and is not committed. Systems, schemas and scenarios are specified in [../simulated-systems.md](../simulated-systems.md).

## Generate and check

Python 3.11 and the shared platform package (`pip install -e platform` from the repo root).

```bash
python3 generate_dataset.py                # full size: about 350k messages in base, about 45 s, about 17 MB
python3 verify_dataset.py                  # re-derives every answer-key figure from the files, exits 1 on any mismatch
python3 generate_dataset.py --scale 0.1    # same scenarios, 10% of the volume, about 4 s
pytest ../tests -v                         # generates at 0.1 into a temp folder, verifies, parses contracts, replays scenarios
```

Options: `--out <dir>` (default `./sim-data`), `--scale <x>` (message volume multiplier), `--scenarios base,v1,...`. Output is byte-for-byte reproducible for the same options (fixed seed, gzip without timestamps, fixed PDF creation date).

## What gets written

```
sim-data/
  base/                    full data root for the seeded case
    alerts/alarms.jsonl              the monitoring alarm that opens the case (TMF642-shaped)
    messages/<day>.csv.gz            every MT message with its delivery receipt, 2026-09-15 to 09-23 06:00 UTC
    routing/route_history.csv        route weights per network and who changed them
    salesforce/Account, Contact      suppliers and customers, NOC and escalation contacts (Salesforce sObjects)
    suppliers/agreements/*.json      structured agreement and SLA terms (CLM side)
    suppliers/documents/*.md, *.pdf  the signed contract: Markdown source and the rendered PDF
    notices/*.json, *.eml.md         maintenance and partner notices
    tickets/customer/*.json          customer complaint (Salesforce Case fields)
    tests/active_tests.csv           hourly test SMS per route with handset confirmation
    reference/                       networks, error codes
    config/monitoring.json           alert, baseline and recovery rules
    cases/ ledger/ outbox/ supplier-portal/ tickets/internal/   empty stores the prototype writes into
    scenario.yaml                    runner events and analyst script
  v1 .. v4/                overlays: only the files that differ from base, plus overlay.json
  answer-keys/<id>.json    ground truth; keep it away from the agents
```

**Overlays.** To read a path in a variant, look in the variant folder first, then fall back to base unless the path is listed in `overlay.json` `deleted`. `telco_platform.sim.overlay.DataRoot` does this; the generator and verifier both use it.

**Time.** All records cover the whole horizon. Adapters must hide anything later than the sim clock (spec section 4), otherwise the agents can see the future.

## Scenarios

Each alarm reads 68.0% against a 94.0% baseline at 17:10 UTC on 2026-09-22, so the alert alone never decides.

| ID | What happened | Owner | Right action | Recovery verified |
|---|---|---|---|---|
| base | Northbridge's link to Altavia fails after its maintenance (62% on that route) | Supplier SUP-NB | Escalate P1 to Northbridge; analyst drops the credit claim from the packet | 23:05 |
| v1 | Mercado Sol sends a burst to a bad number list; routes healthy | Customer CUST-003 | No supplier escalation, note to account manager (misleading alert) | 21:05 |
| v2 | Same as base; Northbridge first answers "no fault found" and closes | Supplier SUP-NB | Reject closure, follow up with samples, level-2 escalation (incomplete carrier status) | 00:05 next day |
| v3 | Operator maintenance queues messages; receipts arrive late | Operator (Altavia) | Wait and recheck after 18:00 (wait for more evidence) | 21:05 |
| v4 | Our route change sends 70% to a filtered best-effort supplier | Our routing team | Recommend reverting the change (a person does it) | 21:05 |

## Making new test cases

1. Add an entry to `SCENARIOS` in `generate_dataset.py`. The modifiers are `degrade` (a supplier/network gets a lower delivery ratio and a failure mix inside a window), `burst` (extra traffic from one customer with its own outcome), `queue` (receipts held until a release time), `route_changes` and `stream_p` (a supplier with a fixed ratio), plus `notices_extra`. Set `fix_or_end` to when recovery checking may start.
2. Add its `TRUTH` entry (cause, owner, hypotheses, expected actions) and, if the supplier replies, its events in `scenario_yaml()`.
3. Add any planted pattern you want guaranteed to the design checks in `verify_dataset.py` section 6.
4. Run the generator, the verifier and `pytest ../tests`. All figures in the answer key are computed from the generated files, so only the design checks need hand-picked numbers.

## Checks

At full scale and at `--scale 0.1`, `verify_dataset.py` passes 360 checks: message integrity (unique IDs, a route with weight at every submit time, receipts after submission, EXPIRED at submit + validity, known error codes), the alarm against the messages, every answer-key figure, SLA figures against both the agreement record and the clauses parsed from its PDF, and the planted pattern of each scenario. It catches a tampered alarm value and a tampered route weight.

The tests in `../tests` add: SLA terms extracted from each supplier's PDF match the agreement record (and a changed record shows up as a mismatch), each scenario's supplier replies fire on the shared runner only after the matching submission, and notices and CRM contacts read correctly through the shared connectors as of sim time.

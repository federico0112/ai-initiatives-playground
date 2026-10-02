# Synthetic dataset: the 8% carrier invoice case

Thread 4 of the prototype plan. Simulated records for every mock system in
[simulated-systems.md](../simulated-systems.md), with the seeded case from its section 6 and
variants V1 to V6. All parties, trunks and numbers are fictional.

Thresholds (confirmed by Federico, 2026-10-02): open a case when `|invoiced − expected| / invoiced > 2%`;
request evidence when `unresolved / invoiced > 1%`.

## Generating the data and test cases

The data and answer keys are **not committed**. Generate them locally (Python 3.11, stdlib only,
fixed seed, about 25 s):

```bash
cd prototypes/tele-prototypes/dataset
python3 generate_dataset.py   # writes sim-data/ and answer-keys/
python3 verify_dataset.py     # re-derives every figure; exits 1 on any mismatch
```

The output is deterministic: every run produces byte-identical files. Both output folders are
listed in `.gitignore`. Each scenario (base, v1 to v6) is a test case: its data root under
`sim-data/<id>/` is what the agents see, and `answer-keys/<id>.json` is the expected outcome to
score them against. To add or change a scenario, edit `generate_dataset.py` and regenerate; do not
hand-edit the generated files.

## Layout

```
dataset/
  generate_dataset.py      rebuilds everything (stdlib, fixed seed)
  verify_dataset.py        re-derives every figure from the files only; exits 1 on any mismatch
  sim-data/base/           (generated) full data root, spec section 9 layout
  sim-data/v1 .. v6/       (generated) overlays on base (see below)
  answer-keys/<id>.json    (generated) ground truth per scenario; keep it away from the agents
```

`sim-data/base` holds 530,597 CDRs in 30 gzipped daily files (about 18 MB), the daily summary,
4,085 rejects, the trunk map, both rate decks with load history and the rate notice, the agreement
JSON plus its clause-anchored document, the rating run (150 daily lines) and the invoice inbox
folder. `cases/`, `ledger/` and `carrier-portal/` are empty stores for the prototype to write into.

**Overlays.** A variant folder only contains the files that differ from base, plus `overlay.json`:
`{"inherits": "sim-data/base", "deleted": [paths]}`. To resolve a path, look in the variant, then fall
back to base unless the path is listed in `deleted`. `verify_dataset.py` has a 15-line `path()`
helper that does this. Files the scenario runner delivers later sit under `staged/` and are named in
`scenario.yaml` with `from:` and `path:`.

## Scenarios

| ID | Edit to base | Invoiced | Expected | Decision | Supported | Unresolved |
|---|---|---:|---:|---|---:|---:|
| base | none | 27,000.00 | 25,000.00 | partial dispute | 1,820.00 | 0.00 (180.00 explained by our rejects) |
| v1 | CO-MOB rejects removed | 27,000.00 | 25,000.00 | partial dispute | 1,820.00 | 180.00 (0.67%) |
| v2 | v1 + carrier bills 340,000 CO-MOB min | 27,420.00 | 25,000.00 | request evidence, then partial dispute | 1,820.00, then 2,240.00 | 600.00 (2.19%), then 0.00 |
| v3 | notice sent 2026-09-12 instead of 09-07 | 27,000.00 | 24,350.00 | partial dispute | 2,470.00 | 0.00 |
| v4 | partial deck file deleted | 27,000.00 | 25,000.00 | step 2 blocks, request deck from pricing | none | none |
| v5 | invoice line 4 = 5,520.00, subtotal unchanged | 27,000.00 | 25,000.00 | step 2 blocks, request corrected invoice | none | none |
| v6 | correct invoice | 25,000.00 | 25,000.00 | no case | none | none |

## Base case in brief

- **Mexico Mobile, rate before effective date (dispute).** Notice sent 2026-09-07T14:00Z, deck says
  0.0185 from 2026-09-15, clause 6.3 agrees. Invoice line 1 bills all 600,000 minutes at 0.0185.
  280,000 minutes fall on 09-01 to 09-14 (exactly 20,000 a day), so 280,000 × 0.0065 = 1,820.00.
- **Colombia Mobile, +12,000 minutes (our fault, pay it).** Mediation rejected 4,000 calls / 12,000
  minutes on trunk TRK-NB-07 from 09-21 to 09-30 as `UNMAPPED_TRUNK`; `trunk_map.csv` only adds that
  trunk from 2026-10-01. 12,000 × 0.0150 = 180.00 is legitimate.
- **Distractors.** 25 `DUPLICATE` and 60 `ZERO_DURATION` rejects that mediation dropped correctly,
  and two Brazil dial codes under one destination group.
- Dispute deadline 2026-10-19, payment due 2026-11-04, undisputed payable 25,180.00.

**V2 after evidence.** The spec leaves this open, so this dataset decides it: the carrier's CDR
detail (113,327 rows) shows 12,000 minutes on our trunk TRK-NB-07 (180.00, carrier's favour) and
28,000 minutes of duplicated carrier records with the same start, number and duration (420.00,
disputable as `DUPLICATE`).

**V3.** The internal rating run applies clause 6.3 (increase from 09-20), so step 2's effective-date
check passes and the deck's stated 09-15 is the carrier's error. Supported covers 09-01 to 09-19:
380,000 × 0.0065 = 2,470.00.

## Checks

`python3 verify_dataset.py` passes 27 checks on freshly generated files: daily summary equals the CDR
roll-up exactly, every rated line follows the `contracted_rate` rule, invoice lines sum to the
header, and every figure in the table above recomputes to the cent.

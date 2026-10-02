# Simulated external systems: spec for the Assurance Resolution prototype

Thread 3 of the prototype plan. Owner of this file: the "Simulated external systems design" thread. Other threads should send changes through that thread rather than edit here.

Inputs: [industry-records-research.md](industry-records-research.md) (record shapes, standards) and the flow diagram artifact (seven steps, evidence ledger, analyst gate).

**Thresholds.** Confirmed by Federico on 2026-10-02 as defaults. Both stay configurable per scenario (`thresholds` in `scenario.yaml`):
- Open a case when `|invoiced − expected| / invoiced > 2%`.
- Request evidence when the unresolved amount `> 1%` of the invoiced total.

All companies, people, trunks and numbers below are fictional.

---

## 1. Approach in one paragraph

Every external system is a folder of plain files (JSON, CSV, Markdown) plus a thin read-only adapter with the same function signatures a real connector would later have. The agents never read files directly; they call the adapter, and every object the adapter returns carries a stable citation ID (`sim://…`). A scenario runner owns a simulated clock and drops new files into the folders at set times (an invoice arriving, a carrier answering an evidence request), so the case can be replayed end to end without any real integration. Swapping a mock for a real system later means re-implementing one adapter, not touching the agents.

```
             scenario runner (sim clock, scenario.yaml)
                       │ drops files at sim times
   ┌──────────┬────────┴──┬─────────────┬─────────────┬──────────────┐
   ▼          ▼           ▼             ▼             ▼              ▼
contracts/  rates/     usage/        rating/      invoices/      carrier-portal/
 (CLM)    (rate mgmt) (mediation)   (rating eng)  (AP inbox)     (carrier side)
   └──────────┴───────────┴──────┬──────┴─────────────┴──────────────┘
                          read-only adapters  ──►  agents / rules steps
                                                     │ write
                                         cases/ (ticket store) + ledger/ (evidence)
```

## 2. The mock systems

| # | Mock system | Stands in for (real world) | Shape modelled on | Read/write |
|---|---|---|---|---|
| S1 | Contract repository | CLM / settlement "agreement" object + signed PDF | TMF651 Agreement | read |
| S2 | Rate deck store | Rate management tool (deck versions, load history) + carrier rate notices by email | Carrier CSV deck; 46labs load history; TMF620 `validFor` | read |
| S3 | Usage store | Mediation output: CDRs, daily summaries, reject log | FCS UK CDR v3 + wholesale switch fields; TMF635 | read |
| S4 | Rated charges | Internal rating engine output | CGRateS rated CDR; TMF635 `ratedProductUsage` | read |
| S5 | Carrier invoice inbox | Email/FTP/iXLink invoice delivery to AP | UBL 2.1 / Peppol BIS 3 header + destination summary CSV | read |
| S6 | Case store | Settlement dispute module, Salesforce Case, Jira | TMF621 Trouble Ticket | read/write |
| S7 | Carrier dispute portal | Carrier's claims desk (e.g. Brightspeed process) | Brightspeed dispute fields | write (submit), read (responses) |
| L  | Evidence ledger | Part of our product, not external; specified here because every mock cites into it | append-only JSONL | append |

Answer to the flow diagram's open question on sending the packet: S7 simulates the carrier side, but **only the analyst can trigger a submission**, and the stub replies with a scripted acknowledgement. Nothing is ever auto-sent.

## 3. Citation IDs

Every record an adapter returns has a `cite_id`. The ledger and every agent statement refer to records only by these IDs.

```
sim://<system>/<object-type>/<id>[#<fragment>]

sim://contracts/agreement/AGR-NB-2025-014
sim://contracts/agreement/AGR-NB-2025-014#clause-6.3
sim://rates/deck/NB-VOICE-2026-09-07/row/521
sim://rates/notice/RN-NB-2026-0907
sim://usage/cdr/CDR-20260903-000123
sim://usage/daily/2026-09-03/MX-MOB
sim://usage/reject/REJ-20260921-0007
sim://usage/trunk_map/TRK-NB-07
sim://rating/run/RUN-2026-09/line/2026-09-03/MX-MOB
sim://invoices/invoice/NB-INV-2026-09-0412/line/3
sim://cases/case/CASE-0001
sim://ledger/calc/CALC-0001-005
```

Fragments: `#clause-<n>` for contract clauses, `/row/<dial_code>` for deck rows, `/line/<n>` for invoice lines. A cite_id must resolve through the adapter's `resolve(cite_id)` call, which returns the record plus its source file path and line number.

---

## 4. Field-level schemas

Types: `string`, `int`, `decimal(p)` (fixed-point, `p` decimals, serialized as a string in JSON to avoid float drift), `date` (YYYY-MM-DD), `datetime` (ISO 8601 with `Z`, always UTC), `enum`. `req` marks required fields; step 2 validation fails a record that lacks one.

### S1 Contract repository

Files: `contracts/agreements/<agreement_id>.json` plus `contracts/documents/<agreement_id>.md` (the "signed PDF" as Markdown with numbered clause anchors, so citations can point at a clause).

`agreement` (TMF651-shaped, flattened where TMF nests):

| Field | Type | Req | Notes |
|---|---|---|---|
| agreement_id | string | req | `AGR-NB-2025-014` |
| document_number | string | req | Carrier's own reference |
| name | string | req | |
| agreement_type | enum | req | `wholesale_voice`, `a2p_sms` |
| status | enum | req | `active`, `expired`, `terminated` |
| version | int | req | Increments on amendment |
| period_start / period_end | date | req / — | `period_end` null = evergreen |
| buyer_party | object | req | `{party_id, name, role:"buyer"}` |
| seller_party | object | req | `{party_id, name, role:"seller"}` |
| currency | string | req | ISO 4217 |
| billing_cycle | enum | req | `monthly`, `weekly`, `daily` |
| billing_timezone | string | req | IANA, e.g. `UTC`. Defines the period cut-off |
| billing_increment | string | req | `60/60`, `30/6`, `1/1` (first increment / subsequent, seconds) |
| rounding | object | req | `{charge_decimals:2, rounding_mode:"half_up", level:"invoice_line"}` |
| rate_schedule_ref | string | req | Deck family this agreement prices from, e.g. `NB-VOICE` |
| rate_notice_days_increase | int | req | e.g. 7 |
| rate_notice_days_decrease | int | req | e.g. 0 |
| invoice_due_days_after_period | int | req | Invoice must be issued within N days of period end |
| payment_terms_days | int | req | From invoice date |
| dispute_window_days | int | req | From invoice date |
| dispute_min_amount | decimal(2) | — | Contractual threshold below which disputes are not accepted |
| pay_undisputed_required | bool | req | |
| dispute_evidence_required | string[] | req | e.g. `["billed_rate","correct_rate","period","account_id","cdr_detail"]` |
| escalation_days | int | — | Unresolved disputes escalate after N days |
| clauses | object[] | req | `{clause_id, title, summary}`; `clause_id` matches an anchor in the `.md` document |
| amendments | string[] | — | Agreement IDs of amendments (`associatedAgreement`) |

### S2 Rate deck store

Files:
- `rates/decks/<deck_id>.csv` one file per uploaded deck version
- `rates/deck_history.csv` the load-history log
- `rates/notices/<notice_id>.json` + `rates/notices/<notice_id>.eml.md` the carrier's rate-change notification as received

`deck row` (CSV columns, carrier de facto layout):

| Column | Type | Req | Notes |
|---|---|---|---|
| dial_code | string | req | E.164 prefix without `+` |
| destination | string | req | e.g. `Mexico Mobile` |
| destination_group | string | req | Our normalized key, e.g. `MX-MOB`; reconciliation groups on this |
| service | enum | req | `voice`, `sms` |
| mcc_mnc | string | — | SMS decks only |
| rate | decimal(4) | req | Per minute (voice) or per message (sms) |
| currency | string | req | |
| effective_date | date | req | Row applies from 00:00 in the agreement's `billing_timezone` |
| end_date | date | — | Exclusive; null = open |
| change_flag | enum | req | `NEW`, `INCREASE`, `DECREASE`, `UNCHANGED`, `CLOSED` |
| billing_increment | string | req | Usually mirrors the agreement |
| connection_fee | decimal(4) | — | |

`deck_history` (one row per deck load):

| Column | Type | Req | Notes |
|---|---|---|---|
| deck_id | string | req | `NB-VOICE-2026-09-07` |
| rate_schedule_ref | string | req | Ties to agreement |
| deck_type | enum | req | `full` (A-Z) or `partial` |
| source_file | string | req | Filename as received |
| received_at | datetime | req | |
| loaded_at | datetime | req | |
| loaded_by | string | req | |
| load_status | enum | req | `loaded`, `rejected`, `superseded` |
| total_rows | int | req | |
| min_effective_date / max_effective_date | date | req | |
| notice_id | string | — | Links to the notice that carried this deck |

`rate notice`:

| Field | Type | Req | Notes |
|---|---|---|---|
| notice_id | string | req | `RN-NB-2026-0907` |
| from_party | string | req | |
| sent_at | datetime | req | Notice period counts from here |
| received_at | datetime | req | |
| channel | enum | req | `email`, `portal`, `ixlink_like` |
| subject | string | req | |
| deck_id | string | req | Deck attached to the notice |
| summary_changes | object[] | req | `{destination_group, old_rate, new_rate, effective_date, change_flag}` |

**Resolving the contracted rate.** For a call at UTC time `t`, convert `t` to the agreement's `billing_timezone`, then take the deck row with the longest matching `dial_code` among loaded decks where `effective_date ≤ local_date(t) < end_date`. If two loaded decks give different rows for the same code and date, the later `received_at` wins, but an `INCREASE` row is only contractually effective from the first full day (in `billing_timezone`) that starts at least `rate_notice_days_increase` days after the notice's `sent_at`; before that, the previous row still applies. Step 3 calls this rule `contracted_rate(code, t)` and records the deck row it used.

### S3 Usage store (mediation)

Files:
- `usage/cdrs/<YYYY-MM-DD>.csv.gz` one file per UTC day
- `usage/daily_summary.csv` mediation's own daily roll-up
- `usage/rejects.csv` records mediation dropped, with reason
- `usage/trunk_map.csv` trunk → carrier mapping

`cdr` (FCS v3 subset plus wholesale switch fields):

| Column | Type | Req | Notes |
|---|---|---|---|
| cdr_id | string | req | `CDR-<YYYYMMDD>-<seq>` unique |
| call_id | string | req | SIP Call-ID; duplicates detected on this |
| start_time_utc | datetime | req | Connect time |
| end_time_utc | datetime | req | |
| duration_sec | int | req | Actual seconds |
| a_number | string | req | E.164, masked to last 4 digits in prototype (`5255XXXX1234`) |
| b_number | string | req | E.164 dialled, unmasked prefix needed for code match; mask last 4 |
| ingress_trunk | string | req | Customer side |
| egress_trunk | string | req | Carrier side; maps to carrier via `trunk_map` |
| egress_carrier_id | string | req | From `trunk_map` |
| release_cause | int | req | SIP/Q.850 cause; 16 = normal |
| service | enum | req | `voice` |
| mediation_batch_id | string | req | |

`daily_summary`: `date_utc, egress_carrier_id, destination_group, calls, duration_sec, billable_minutes, source_batch_ids`. `billable_minutes` applies the agreement's increment per call (60/60 → `ceil(duration_sec/60)` for `duration_sec > 0`). Must equal the sum over CDRs exactly.

`reject`: `reject_id, cdr_raw_ref, start_time_utc, duration_sec, b_number, egress_trunk, reject_reason (enum: UNMAPPED_TRUNK, MALFORMED, DUPLICATE, ZERO_DURATION), rejected_at, reprocessed (bool)`.

`trunk_map`: `trunk_id, carrier_id, direction, valid_from, valid_to`.

### S4 Rated charges (rating engine)

Files: `rating/runs/<run_id>.json` (run header) and `rating/runs/<run_id>/lines.csv`.

`rating run`: `run_id, period_start, period_end, carrier_id, agreement_id, agreement_version, decks_used (deck_id[]), run_at, engine_version, status (complete|partial)`.

`rated line` (daily per destination group per rate; CDR-level rating is out of scope for the prototype because daily totals are exact under per-call increments):

| Column | Type | Req | Notes |
|---|---|---|---|
| run_id | string | req | |
| date_local | date | req | In agreement `billing_timezone` |
| destination_group | string | req | |
| deck_id | string | req | Deck version used |
| dial_code_rule | string | req | Deck row key used, e.g. `521` |
| rate | decimal(4) | req | |
| calls | int | req | |
| billable_minutes | int | req | |
| amount | decimal(2) | req | `billable_minutes × rate`, half-up to 2 dp |
| currency | string | req | |

Expected charge for the period = sum of `amount` over the run. This is what step 1 compares against.

### S5 Carrier invoice inbox

Files per invoice:
- `invoices/inbox/<invoice_id>/envelope.json` delivery metadata (as if an email)
- `invoices/inbox/<invoice_id>/invoice.json` UBL-lite header + lines
- `invoices/inbox/<invoice_id>/summary.csv` the carrier's destination summary (what carriers actually send)
- `invoices/inbox/<invoice_id>/invoice.md` human-readable render of the PDF
- `invoices/inbox/<invoice_id>/cdr_detail.csv.gz` optional, only if the carrier supplied detail

`envelope`: `message_id, from, to, received_at, subject, attachments[]`.

`invoice` header:

| Field | Type | Req | Notes |
|---|---|---|---|
| invoice_id | string | req | Carrier's invoice number |
| issue_date | date | req | Dispute window and payment terms count from here |
| due_date | date | req | |
| seller_party / buyer_party | object | req | `{party_id, name, tax_id}` |
| account_id | string | req | Our account at the carrier |
| agreement_ref | string | req | Carrier's `document_number` |
| period_start / period_end | date | req | |
| currency | string | req | |
| line_count | int | req | |
| subtotal | decimal(2) | req | Sum of line amounts |
| tax_total | decimal(2) | req | 0.00 for wholesale international voice in the seed |
| total | decimal(2) | req | |

`invoice line` (also the shape of `summary.csv`):

| Field | Type | Req | Notes |
|---|---|---|---|
| line_no | int | req | |
| destination | string | req | Carrier's own label |
| destination_group | string | — | Carrier invoices do **not** carry our key; mapping happens in step 3 via dial code or a label map |
| dial_codes | string | — | Pipe-separated, if carrier lists them |
| service | enum | req | |
| period_start / period_end | date | req | A line can cover a sub-period |
| calls | int | req | |
| minutes | int | req | Billable minutes as carrier computed |
| rate | decimal(4) | req | |
| amount | decimal(2) | req | |

`carrier cdr_detail` (when supplied): `carrier_cdr_id, start_time_local, duration_sec, billed_minutes, b_number_masked, destination, rate, amount, our_trunk_id`.

### S6 Case store (TMF621-shaped)

Files: `cases/<case_id>.json`, rewritten on every change; `cases/<case_id>.history.jsonl` append-only status and note history.

| Field | Type | Req | Notes |
|---|---|---|---|
| case_id | string | req | `CASE-0001` |
| correlation_id | string | req | Our invoice key `carrier_id:invoice_id` |
| carrier_claim_id | string | — | Filled from S7 acknowledgement |
| ticket_type | enum | req | `invoice_discrepancy` |
| severity | enum | req | `low` `medium` `high`, from variance size |
| status | enum | req | TMF621 v4: `acknowledged`, `inProgress`, `pending`, `held`, `resolved`, `closed`, `cancelled`, `rejected` |
| sub_status | enum | req | Flow step: `opened`, `validating`, `awaiting_evidence`, `reconciling`, `investigating`, `sizing`, `recommended`, `analyst_review`, `returned`, `approved`, `submitted`, `carrier_responded` |
| status_change_reason | string | — | |
| creation_date | datetime | req | Sim clock |
| dispute_deadline | date | req | `issue_date + dispute_window_days` |
| target_resolution_date | date | — | |
| resolution_date | datetime | — | |
| related_objects | object[] | req | `{role, cite_id}`: roles `invoice`, `agreement`, `rating_run`, `usage_extract`, `deck` |
| related_parties | object[] | req | `{role, party_id, name}`: `carrier`, `analyst`, `owner` |
| figures | object | — | `{invoiced, expected, variance, variance_pct_of_invoice, supported, unresolved}`, decimals as strings, each with a `calc_id` |
| recommendation | object | — | `{decision, rationale, cites[], created_at, agent}`; `decision` ∈ `approve`, `partial_dispute`, `full_dispute`, `request_evidence` |
| analyst_decision | object | — | `{decision, approved_amount, edits, reason, analyst, decided_at}` |
| evidence_requests | object[] | — | `{request_id, to (carrier|internal_team), items[], sent_at, due_at, fulfilled_at, fulfilled_by_cite_ids[]}` |
| notes | object[] | — | `{author, at, text}` |
| attachments | object[] | — | `{name, path, cite_id}` e.g. the packet |

### S7 Carrier dispute portal

Files: `carrier-portal/submissions/<claim_id>.json`, `carrier-portal/responses/<claim_id>.json`. Fields follow the carrier dispute processes in the research (one product and one bill period per dispute).

`submission`: `claim_id (assigned by stub), our_reference (case_id), account_id, invoice_id, product, bill_period, disputed_amount, reason_code (RATE_BEFORE_EFFECTIVE|RATE_MISMATCH|VOLUME|DUPLICATE|OTHER), billed_rate, correct_rate, from_date, through_date, supporting_files[], submitted_by, submitted_at`.

`response`: `claim_id, status (acknowledged|in_review|resolved), outcome (customer_favour|carrier_favour|partial|null), credit_amount, credit_from, credit_through, message, responded_at, attachments[]`.

Behaviour: on submit the stub writes an `acknowledged` response at `submitted_at + 2 business days` (sim clock). The resolution, if any, is scripted per scenario (section 6).

### L Evidence ledger

File: `ledger/<case_id>.jsonl`, append-only, one entry per line. Entries are never edited; a correction is a new entry with `supersedes`.

| Field | Type | Req | Notes |
|---|---|---|---|
| entry_id | string | req | `LED-0001-0042` |
| case_id | string | req | |
| at | datetime | req | Sim clock |
| step | int | req | 1–7 |
| actor | object | req | `{kind: rules|agent|analyst|system, name, version}` |
| kind | enum | req | `record_ref`, `check`, `calc`, `statement`, `decision`, `request`, `approval` |
| claim_class | enum | — | For `statement` only: `fact`, `calculation`, `interpretation`, `missing_evidence` |
| text | string | req | One sentence a reviewer can read |
| cites | string[] | req | `sim://` IDs; empty only for `missing_evidence` |
| calc | object | — | For `calc`: `{calc_id, formula, inputs:{name: cite_id or value}, result, unit}` |
| supersedes | string | — | entry_id |

Rule enforced by the ledger writer: an amount may only appear in `kind=calc` entries written by a `rules` actor. Agent statements may reference a `calc_id` but not introduce a new number.

---

## 5. What step 2 checks, by field

| Check | Records | Blocking if |
|---|---|---|
| Period alignment | invoice header vs rating run vs usage dates | invoice period not fully covered by a complete rating run |
| Currency | invoice, agreement, deck rows, rated lines | any mismatch with agreement currency |
| Units | invoice minutes vs agreement increment | carrier minutes non-integer under 60/60 |
| Totals | invoice lines → subtotal → total; rated lines → run total; CDRs → daily summary | any sum off by more than 0.01 |
| Required fields | all `req` fields above | any missing in invoice header or agreement |
| Duplicates | `call_id` in CDRs; `line_no` in invoice | duplicates in invoice lines (CDR duplicates are flagged, not blocking) |
| Effective dates | deck rows used by rating vs `contracted_rate` rule | a rated line used a row not effective on its date |
| Rejects | `usage/rejects.csv` for the period | never blocking; unreprocessed rejects to this carrier are flagged for step 4 |

## 6. Seeded scenario: the 8% invoice

Parties: buyer **Orion Voice Exchange** (our customer's self-managed wholesale team, party `ORX`), seller **Northbridge Carrier Services** (party `NBCS`). Agreement `AGR-NB-2025-014`, voice termination, USD, monthly, `billing_timezone` UTC, 60/60, increase notice 7 days, dispute window 14 days, payment 30 days, `dispute_min_amount` 50.00. Key clauses: **6.3** rate change notice, **8.2** dispute minimum amount.

Billing period 2026-09-01 to 2026-09-30. Sim clock starts 2026-10-05T09:00Z when invoice `NB-INV-2026-09-0412` (issue date 2026-10-05) lands in the inbox. Dispute deadline 2026-10-19.

**Rate decks.**
- `NB-VOICE-2026-08-01` (full, loaded 2026-07-24): all five groups at the "old" rates below.
- `NB-VOICE-2026-09-07` (partial, notice `RN-NB-2026-0907` sent 2026-09-07T14:00Z): Mexico Mobile `INCREASE` 0.0120 → 0.0185, `effective_date` **2026-09-15**. Seven full days of notice end 2026-09-14T14:00Z, so the increase is valid from 2026-09-15.

| destination_group | Destination | dial_code(s) | Rate | 
|---|---|---|---|
| MX-MOB | Mexico Mobile | 521 | 0.0120 until 09-14, 0.0185 from 09-15 |
| MX-FIX | Mexico Fixed | 52 | 0.0060 |
| CO-MOB | Colombia Mobile | 573 | 0.0150 |
| BR-MOB | Brazil Mobile | 55119, 55219 | 0.0210 |
| GT-MOB | Guatemala Mobile | 5025 | 0.0850 |

**Internal records (our side, correct).**

| Group | Billable minutes | Rate | Expected (USD) |
|---|---|---|---|
| MX-MOB 09-01…09-14 | 280,000 | 0.0120 | 3,360.00 |
| MX-MOB 09-15…09-30 | 320,000 | 0.0185 | 5,920.00 |
| MX-FIX | 400,000 | 0.0060 | 2,400.00 |
| CO-MOB | 300,000 | 0.0150 | 4,500.00 |
| BR-MOB | 250,000 | 0.0210 | 5,250.00 |
| GT-MOB | 42,000 | 0.0850 | 3,570.00 |
| **Total (rating run `RUN-2026-09`)** | 1,592,000 | | **25,000.00** |

**Carrier invoice `NB-INV-2026-09-0412`.**

| Line | Destination | Minutes | Rate | Amount |
|---|---|---|---|---|
| 1 | Mexico Mobile (09-01…09-30) | 600,000 | 0.0185 | 11,100.00 |
| 2 | Mexico Fixed | 400,000 | 0.0060 | 2,400.00 |
| 3 | Colombia Mobile | 312,000 | 0.0150 | 4,680.00 |
| 4 | Brazil Mobile | 250,000 | 0.0210 | 5,250.00 |
| 5 | Guatemala Mobile | 42,000 | 0.0850 | 3,570.00 |
| | **Total** | 1,604,000 | | **27,000.00** |

**Variance:** 2,000.00, which is 8.00% of expected and 7.41% of invoiced, above the 2% case threshold, so step 1 opens `CASE-0001`.

**Two planted causes.**
1. **Rate applied before its effective date (supported).** Line 1 bills all 600,000 Mexico Mobile minutes at 0.0185, but 280,000 of them fall on 09-01…09-14, before the increase took effect. Supported dispute = 280,000 × (0.0185 − 0.0120) = **1,820.00**. Evidence: deck row `NB-VOICE-2026-09-07/row/521`, notice `RN-NB-2026-0907`, contract clause 6.3 (`#clause-6.3`), daily usage for MX-MOB, invoice line 1.
2. **Volume gap on Colombia Mobile (explained by our own records).** The carrier bills 12,000 more minutes (180.00). Mediation rejected 4,000 calls / 12,000 billable minutes to CO-MOB between 09-21 and 09-30 with `UNMAPPED_TRUNK` on new egress trunk `TRK-NB-07`, which `trunk_map` only gained on 2026-10-01. So the carrier's charge is legitimate and our expected total is understated. This tests that the system admits an own-side fault instead of disputing it.

**Expected outcome (base scenario).** Step 2 flags the unreprocessed rejects (non-blocking). Step 3 finds a rate exception on MX-MOB (1,820.00) and a volume exception on CO-MOB (180.00). Step 4 classifies the MX-MOB cause as fact + calculation, and the CO-MOB gap as a fact explained by `usage/rejects.csv`. Step 5: supported 1,820.00, explained in carrier's favour 180.00, unresolved 0.00. Step 6 recommends **partial dispute of 1,820.00** and paying the undisputed 25,180.00 by 2026-11-04, plus an internal note to fix the trunk map. Analyst approves; S7 submission with `reason_code RATE_BEFORE_EFFECTIVE`, `from_date 2026-09-01`, `through_date 2026-09-14`. Scripted carrier response at sim +6 days: `customer_favour`, credit 1,820.00.

**Variants.** Each variant is an overlay on base (see section 9). Ground truth per scenario is in `dataset/answer-keys/<id>.json`, which the agents must not read.

| ID | Edit to base | Exercises | Expected decision |
|---|---|---|---|
| V1 | Remove the CO-MOB rows from `rejects.csv` | Unexplained but immaterial volume gap: 180.00 = 0.67% of invoice < the 1% evidence threshold | Partial dispute 1,820.00; 180.00 noted as unresolved, below threshold |
| V2 | V1, and carrier bills 40,000 extra CO-MOB minutes (340,000 total; 600.00; invoice 27,420.00) | Unresolved 600.00 = 2.19% > 1% | Request evidence: carrier CDR detail for CO-MOB. On arrival (`cdr_detail.csv.gz` at sim +3 days) the case re-enters step 2. The detail shows 12,000 min on our trunk TRK-NB-07 (180.00, carrier's favour) and 28,000 min of duplicated carrier records with the same start, number and duration (420.00, reason `DUPLICATE`). After evidence: **partial dispute 2,240.00**, unresolved 0.00 |
| V3 | Notice `RN-NB-2026-0907` sent 2026-09-12 instead of 09-07, deck still says effective 09-15 | Notice period breach: increase only effective from 09-20 under clause 6.3. Internal rating applies 6.3, so expected is 24,350.00 and the deck's 09-15 is the carrier's error | **Partial dispute 2,470.00** (380,000 min on 09-01…09-19 × 0.0065) |
| V4 | Delete deck `NB-VOICE-2026-09-07` from the store | Missing record | Step 2 blocks; request evidence (rate notice and deck) from internal pricing team |
| V5 | Invoice line 4 amount 5,520.00 but subtotal still sums to 27,000.00 | Totals don't sum | Step 2 blocks; request corrected invoice from carrier |
| V6 | Invoice equals 25,000.00 exactly (CO-MOB billed at 300,000 min) and the CO-MOB rejects are removed so internal records stay consistent | Below threshold | No case opens |

## 7. Adapter interface

Plain functions over the folders (Python or TypeScript), returning records with `cite_id` and `source: {path, line}`. The same signatures are what a real connector would implement later.

```
contracts.get_agreement(agreement_id) -> Agreement
contracts.get_clause(agreement_id, clause_id) -> Clause
contracts.find_by_carrier(carrier_id, on_date) -> Agreement

rates.list_decks(rate_schedule_ref) -> [DeckHistoryRow]
rates.get_deck_rows(deck_id, destination_group=None) -> [DeckRow]
rates.contracted_rate(rate_schedule_ref, dial_code, at_utc) -> DeckRow   # implements the rule in S2
rates.list_notices(carrier_id, since) -> [Notice]

usage.daily_summary(carrier_id, start, end) -> [DailyRow]
usage.cdrs(carrier_id, start, end, destination_group=None) -> iterator[CDR]
usage.rejects(carrier_id, start, end) -> [Reject]

rating.get_run(carrier_id, period) -> RatingRun
rating.lines(run_id) -> [RatedLine]

invoices.poll_inbox(since) -> [Envelope]
invoices.get_invoice(invoice_id) -> Invoice  # header + lines
invoices.get_carrier_detail(invoice_id) -> iterator[CarrierCDR] | None

cases.create(case) / cases.update(case_id, patch) / cases.get(case_id) / cases.add_note(...)
ledger.append(entry) / ledger.read(case_id)

portal.submit(submission) -> claim_id     # analyst-triggered only
portal.responses(claim_id) -> [Response]

resolve(cite_id) -> record + source
```

## 8. Scenario runner

`scenario.yaml` per scenario:

```yaml
id: base
sim_start: 2026-10-05T09:00:00Z
data_root: sim-data/base           # relative to assurance-prototype/dataset/
thresholds:
  open_case_variance_pct_of_invoice: 2.0     # confirmed default
  request_evidence_unresolved_pct_of_invoice: 1.0   # confirmed default
events:
  - at: 2026-10-05T09:00:00Z
    action: deliver_invoice
    invoice_id: NB-INV-2026-09-0412
  - on: evidence_request            # V2 only
    match: {to: carrier, item: cdr_detail, destination_group: CO-MOB}
    after: P3D
    action: deliver_file
    from: staged/invoices/inbox/NB-INV-2026-09-0412/cdr_detail.csv.gz   # inside the variant folder
    path: invoices/inbox/NB-INV-2026-09-0412/cdr_detail.csv.gz          # where it appears in the data root
  - on: portal_submit
    after: P2D
    action: portal_response
    response: {status: acknowledged}
  - on: portal_submit
    after: P6D
    action: portal_response
    response: {status: resolved, outcome: customer_favour, credit_amount: "1820.00"}
expected:
  case_opened: true
  supported: "1820.00"
  unresolved: "0.00"
  decision: partial_dispute
```

`deliver_file` copies `from` (a file under the variant's `staged/` folder, invisible to adapters until delivered) to `path` in the data root. The example above mixes base and V2 events for illustration; each scenario's real file is in its folder.

The runner advances the clock only when the case is waiting (evidence or carrier response), so a demo runs in seconds. `expected` makes each scenario a regression test: the deterministic steps must hit these figures to the cent.

## 9. Folder layout of the generated data

Generated by the dataset thread; see `dataset/README.md` for how to rebuild and verify it.

```
assurance-prototype/dataset/
  generate_dataset.py  verify_dataset.py
  answer-keys/<scenario_id>.json          ground truth, keep away from agents
  sim-data/base/                          full data root
    contracts/agreements/  contracts/documents/
    rates/decks/  rates/deck_history.csv  rates/notices/
    usage/cdrs/  usage/daily_summary.csv  usage/rejects.csv  usage/trunk_map.csv
    rating/runs/
    invoices/inbox/<invoice_id>/
    cases/  ledger/  carrier-portal/      empty stores the prototype writes into
    scenario.yaml
  sim-data/v1 … v6/                       overlays on base
    overlay.json                          {"inherits": "sim-data/base", "deleted": [paths]}
    <only the files that differ from base>
    staged/                               files the runner delivers later
    scenario.yaml
```

**Resolving a path in a variant.** Look in the variant folder first; if absent, fall back to base, unless the path is listed in `deleted`, in which case it does not exist. Adapters implement this once (`verify_dataset.py` has a short `path()` helper), so agents never see the overlay mechanics.

Base holds about 530,600 CDRs in 30 gzipped daily files (about 18 MB).

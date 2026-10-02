# Simulated external systems: spec for the Operations Resolution prototype

Prototype two: an A2P SMS delivery incident. Delivery to Mexico / Altavia Movil falls from 94% to 68%; the system assembles the evidence, finds the probable owner, prepares a supplier escalation for human approval and verifies recovery before closing.

Inputs: [industry-records-research.md](industry-records-research.md), the flow diagram, prototype one's spec (same approach), and the shared platform in [`platform/`](../../platform/README.md). Data scripts: [dataset/](dataset/README.md).

All companies, networks, people and numbers are fictional. MCC-MNC 334-990 and 334-991 are not assigned to real Mexican operators here.

---

## 1. Approach in one paragraph

Same as prototype one. Every external system is a folder of plain files plus a thin read-only adapter with the signatures a real connector would have; every returned record carries a `sim://` citation ID. A scenario runner owns a simulated clock and delivers supplier replies when the case waits. One thing is new: an incident unfolds in time, so **every record has a visibility time** and adapters only return what existed at the current sim time (section 4). That is how the agents can be wrong in a realistic way (receipts not in yet, a supplier reply that has not arrived) without any live system.

```
           scenario runner (sim clock, scenario.yaml, analyst script)
                     │ delivers supplier replies, advances clock while the case waits
 ┌────────┬─────────┬┴─────────┬──────────┬──────────┬──────────┬──────────┐
 ▼        ▼         ▼          ▼          ▼          ▼          ▼          ▼
alerts/ messages/ routing/  suppliers/  notices/  tickets/   tests/   supplier-portal/
(NOC)  (platform) (routing) (CLM+CRM)  (NOC mail) (CRM/ITSM) (probes)  (supplier NOC)
 └────────┴─────────┴──────┬───┴──────────┴──────────┴──────────┴──────────┘
              read-only adapters, as_of(sim now)  ──►  rules + agents
                                                        │ write
                                       cases/ + ledger/ + outbox/ (+ supplier-portal/submissions)
```

## 2. The mock systems

| # | Mock system | Stands in for | Shape modelled on | Read/write | Shared platform? |
|---|---|---|---|---|---|
| O1 | Alert feed | NOC monitoring tool | TMF642 Alarm | read | specific |
| O2 | Message store | A2P platform message log: submit + delivery receipt | SMPP 3.4 `submit_sm_resp` + DLR | read | specific |
| O3 | Route history | Routing / LCR tool change log | Route plan + change ticket | read | specific |
| O4 | Supplier records | CLM agreement + signed PDF, CRM supplier account, NOC contacts | TMF651 Agreement; Salesforce Account/Contact | read | **shared** contract parser, base schema and CRM connector; SMS SLA terms in `contracts/terms.py` |
| O5 | Notice inbox | NOC mailbox: maintenance and partner notices | Messaging NOC notice (window, MT/MO/DR impact) | read | **shared** inbox; notice fields here |
| O6 | Customer tickets | Salesforce Case from the enterprise sender | Salesforce Case / TMF621 | read | **shared** Salesforce-shaped backend |
| O7 | Active tests | Test-SMS service (handset confirmation) | Vendor probe results | read | specific |
| O8 | Supplier portal | Supplier NOC ticket desk / email | TMF621 Trouble Ticket | write (submit), read (responses) | **shared** submit/response mechanics and approval gate; payload here |
| O9 | Case store | Our incident record (Jira SM / ServiceNow / Salesforce) | TMF656 Service Problem + TMF621 | read/write | **shared** ticket connector; `delivery_incident` fields here |
| L | Evidence ledger | Part of our product | append-only JSONL | append | **shared**, with `hypothesis_id` and `contradicts` |
| — | Outbox | Internal notes (account manager, routing team) | email-like JSON | write | **shared** |

Rule shared with prototype one: **nothing leaves without an analyst approval record.** The system never changes routes; containment (shift weight, revert a change) is a recommendation that a person carries out.

## 3. Citation IDs

```
sim://alerts/alarm/ALM-2026-0922-0042
sim://messages/msg/ORX-20260922-0012345
sim://messages/window/MX-ALT/2026-09-22T16:05Z/2026-09-22T17:05Z?supplier=SUP-NB&as_of=2026-09-22T17:10Z
sim://routing/change/RC-2026-0922-01
sim://suppliers/agreement/AGR-NB-SMS-2025-031#clause-5.1
sim://crm/contact/SUP-NB-C2
sim://notices/notice/MN-NB-2026-0922
sim://tickets/customer/SF-00012871
sim://tests/test/TST-001234
sim://supplier-portal/response/NB-T-551203/2
sim://cases/case/INC-0001
sim://ledger/calc/CALC-0001-004
```

A `window` cite stands for an aggregate the rules computed; it always carries `as_of`, so re-running the same query later returns the same numbers.

## 4. Visibility rule (as_of)

Data files hold the whole horizon (2026-09-15 to 2026-09-23 06:00 UTC). Adapters take `as_of = sim now` and hide the future:

| Record | Visible when |
|---|---|
| Message | `submitted_at ≤ as_of`; its receipt fields (`dlr_stat`, `dlr_err`, `dlr_received_at`) only if `dlr_received_at ≤ as_of`, otherwise blank (pending) |
| Alarm | `alarmRaisedTime ≤ as_of` |
| Route change | `effective_at ≤ as_of` |
| Notice | `received_at ≤ as_of` |
| Customer ticket | `CreatedDate ≤ as_of` |
| Active test | `sent_at ≤ as_of`; receipt and handset fields only once their own times pass |
| Supplier response | written by the runner at its delivery time |

## 5. Field-level schemas

Types as in prototype one: `datetime` is ISO 8601 UTC with `Z`; decimals are strings.

### O1 Alert feed: `alerts/alarms.jsonl`

TMF642-shaped, one alarm per line: `externalAlarmId`, `sourceSystemId`, `alarmType` (`QualityOfServiceAlarm`), `perceivedSeverity` (`CRITICAL` if observed < baseline − 25 pp, else `MAJOR`), `probableCause` (`thresholdCrossed`), `specificProblem`, `alarmedObject {id, mcc_mnc, name}`, `alarmRaisedTime`, `alarmReportingTime`, `serviceAffecting`, `state`, `crossedThresholdInformation {direction, indicatorName, indicatorUnit, observedValue, baselineValue, thresholdValue, window_start, window_end, submitted}`, `comment`.

The monitoring rule is described in `config/monitoring.json` (observed = DELIVRD receipts received by evaluation time / messages submitted in the previous hour, lagged 5 min; baseline = same window on the previous 7 days, pooled; alert below baseline − 15 pp). The monitor itself is not simulated: the prototype starts from an existing alert, as the wedge says. The alarm's `comment` names the largest route ("Suspected supplier degradation"), which is the misleading lead in v1 and v3.

### O2 Message store: `messages/<YYYY-MM-DD>.csv.gz`

One row per MT message, by UTC submit day, receipt merged in.

| Column | Type | Notes |
|---|---|---|
| msg_id | string | Ours: `ORX-<yyyymmdd>-<seq>` |
| submitted_at | datetime (ms) | Accepted by supplier |
| customer_id | string | Enterprise sender, see `reference/customers.csv` |
| traffic_type | enum | `otp`, `transactional`, `marketing` |
| sender_type / sender_id | string | `alphanumeric` / `shortcode` |
| network_group / mcc_mnc | string | `MX-ALT` / `334990`; `MX-VRD` / `334991` |
| dest_masked / dest_range | string | `52551234XXXX` / `52551234` |
| supplier_id / route_id | string | `SUP-NB` / `RT-ALT-NB` |
| submit_status | string | SMPP `command_status`; always `0x00000000` in this dataset |
| supplier_msg_id | string | ID from `submit_sm_resp`; the only ID the supplier can search |
| segments | int | Message parts (billing unit) |
| dlr_stat | enum | `DELIVRD`, `UNDELIV`, `EXPIRED`, blank = no receipt (yet) |
| dlr_err | string | 3-digit network error, see `reference/error_codes.csv` |
| dlr_received_at | datetime (ms) | When the receipt reached us; EXPIRED arrives at submit + 4 h validity |

Receipt text parsing (`id:… stat:… err:…`) is assumed done upstream; the structured fields are what the platform stores.

`reference/error_codes.csv`: `err, name, permanent, sla_excluded, meaning` (codes 000, 001, 005, 006, 009, 011, 013, 015, 020, 022).

### O3 Route history: `routing/route_history.csv`

`change_id, effective_at, network_group, weights (SUP-NB:80|SUP-PR:20), previous_weights, changed_by, actor_kind (person|auto_rule), reason, change_ticket`. The weights in force at time t are the last row for that network with `effective_at ≤ t`. Every message's supplier must have weight > 0 at its submit time (checked by `verify_dataset.py`).

### O4 Supplier records

- `salesforce/Account/<id>.json` and `salesforce/Contact/<id>.json`: Salesforce sObjects read by `telco_platform.connectors.crm`. Supplier accounts carry `Connection__c, SMPP_System_Id__c, NOC_Hours__c, Agreement_Id__c`; contacts carry `Role__c` (`NOC L1`, `NOC L2`, `Account Manager`), `Email`, `Escalation_Level__c`, `Escalate_After__c`. Customer accounts carry `Traffic_Type__c, Sender_Id__c, Account_Manager__c`.
- `suppliers/agreements/<id>.json`: TMF651-style header as in prototype one (`agreement_id, document_number, agreement_type: a2p_sms, status, version, period, buyer_party, seller_party, currency, billing_basis, prices[{network_group, mcc_mnc, price}]`) plus `sla`:

| sla field | Example (Northbridge) |
|---|---|
| measurement_day | UTC calendar day |
| delivery_target_pct / delivery_measure | 95.0 / DELIVRD ÷ (accepted − excluded), per network per day |
| excluded_errors | UNDELIV 001, 009, 011, 013; EXPIRED 006 |
| dlr_coverage_target_pct, dlr_latency | 97.0; 95% within 30 s |
| priorities[] | P1: raw delivery < 80% for ≥ 30 consecutive min, respond PT30M, restore PT4H; P2 … |
| escalation_evidence | ≥ 10 supplier message IDs, MCC-MNC and ranges, UTC start, error breakdown |
| maintenance_notice / maintenance_exclusion | P5D business days / only the window and only the stated impact |
| service_credit | 10% of the day's charges for the network; claim via account manager within 30 days |

- `suppliers/documents/<id>.md` and `<id>.pdf`: the signed contract. The generator writes clause-anchored Markdown and renders it to PDF with `telco_platform.sim.render_pdf`. Prototype two reads the PDF with the shared parser, extracts SLA terms with `contracts/terms.py`, and checks them against the JSON record with `telco_platform.contracts.compare`; a mismatch is ledger evidence. The JSON carries `signed_pdf` and the `clauses` list. Pacifica's agreement has a 92% target and no credits; Cobalt's is best effort with **no SLA**.

### O5 Notice inbox: `notices/<notice_id>.json` + `.eml.md`

`notice_id, from_party, notice_type (planned_maintenance|emergency_maintenance|partner_notice), sent_at, received_at, channel, subject, window_start, window_end, scope, networks[], impact {MT, MO, DR}, reference`. The `.eml.md` is the human-readable email.

### O6 Customer tickets: `tickets/customer/<CaseNumber>.json`

Salesforce Case field names: `Id, CaseNumber, AccountId, Account.Name, ContactEmail, Subject, Description, Priority, Status, Origin, Type, CreatedDate, OwnerId`.

### O7 Active tests: `tests/active_tests.csv`

One probe per active route per hour at :30. `test_id, sent_at, supplier_id, network_group, mcc_mnc, test_number_masked, dlr_stat, dlr_err, dlr_received_at, handset_received_at` (blank = the handset never got it). This is the independent check that a receipt matches reality.

### O8 Supplier portal: `supplier-portal/submissions/`, `supplier-portal/responses/`

Submitted through `telco_platform.connectors.carrier_portal` (system `supplier-portal`), which refuses a payload without a matching approval and emits the runner event (`escalation_submitted`, `follow_up_submitted`).

`submission` (written by the prototype after approval): `submission_id, kind (escalation|follow_up|level_2), our_reference (case_id), supplier_id, to, priority, level, mcc_mnc, network_name, destination_ranges[], start_time, ongoing, impact {baseline_pct, observed_pct, window, submitted}, error_breakdown {}, samples [{msg_id, supplier_msg_id, submitted_at, dlr_stat, dlr_err}] (≥ 10), comparison (other supplier on same network, same supplier on other network), actions_taken[], request, sla_refs[] (cite_ids), approval_id, submitted_at`.

`response` (written by the runner): `ticket_id, seq, status (acknowledged|investigating|resolved), root_cause, fix_time, message, responded_at, requested_info[]`.

### O9 Case store: `cases/<case_id>.json` + `.history.jsonl`

TMF656/TMF621-shaped; shared ticket fields as in prototype one (`case_id, correlation_id, status, sub_status, creation_date, related_objects, related_parties, notes, attachments`) plus:

| Field | Notes |
|---|---|
| ticket_type | `delivery_incident` |
| priority | P1/P2 per the owner's agreement, or internal P1 for own faults |
| sub_status | `opened, collecting, timeline, diagnosing, ops_review, returned, approved, escalated, awaiting_supplier, waiting_evidence, verifying_recovery, closed` |
| affected | `{network_group, mcc_mnc, start_time, end_time, suppliers[], customers[]}` |
| figures | `{baseline_pct, observed_pct, per_supplier{}, control_network_pct, receipt_coverage_pct, excluding_customer{}}`, each with a `calc_id` |
| timeline | `[{at, event, cites[]}]` sorted, with `gaps[]` and `contradictions[]` |
| hypotheses | `[{hypothesis_id, cause, owner, status (open|supported|refuted), for[], against[]}]` (cite_ids) |
| diagnosis | `{probable_cause, owner, owner_kind (supplier|operator|customer|internal), confidence (high|medium|low), alternatives[]}` |
| recommendation | `{action (escalate_supplier|internal_containment|notify_customer|wait_and_recheck|close_no_action), detail, recheck_at, cites[]}` |
| analyst_decision | `{decision (approve|revise|reject), edits, reason, analyst, decided_at}` |
| escalations | `[{submission_id, kind, level, submitted_at, responses[]}]` |
| recovery | `{rule, hours[], verified, verified_at}` |
| sla_impact | `{agreement, priority_condition_met_at, response_met, restoration_met, day_sla_pct, breach, credit_amount, credit_claim (separate commercial step)}` |
| closure | `{closed_at, cause, owner, summary, cites[]}` |

### L Evidence ledger

As prototype one (`entry_id, case_id, at, step, actor, kind, claim_class, text, cites, calc, supersedes`), plus `hypothesis_id` and `contradicts` (cite_ids of records that disagree). Same rule: a number only appears in a `calc` written by a `rules` actor.

## 6. Deterministic rules (step 2 to 4 and 7 compute these; agents only interpret them)

| Rule | Definition |
|---|---|
| R1 delivery rate | DELIVRD receipts received by `as_of` ÷ messages submitted in `[t0, t1)`, filtered by network / supplier / customer |
| R2 receipt coverage | receipts of any final state received by `as_of` ÷ submitted. Low coverage with normal error counts means "not known yet", not "failed" |
| R3 baseline | R1 on the same clock window on each of the previous 7 days, each read at its own `t1 + lag`, pooled |
| R4 error breakdown | count of `stat err` among visible receipts; subscriber errors = UNDELIV 001/009/011/013, EXPIRED 006 |
| R5 isolation | R1 for: each supplier on the network; the same supplier on the control network (MX-VRD); the network excluding each customer. The owner pattern is which of these moved |
| R6 recovery | 3 consecutive full hours, starting at or after the fix (or end of window), with network rate ≥ hourly baseline − 2.0 pp and volume ≥ 50% of baseline; each hour read at hour end + 5 min |
| R7 SLA impact | P1/P2 condition on raw delivery (six consecutive 5-minute buckets below 80% for P1); response and restoration time from our escalation; day SLA rate on final receipts; credit = 10% of day charges if below target |

## 7. Seeded scenario (base): supplier connection fault

Parties: **Orion Messaging Exchange** (ORX, our customer's messaging operations team). Suppliers to Mexico: **Northbridge Messaging** (SUP-NB, direct to the operators), **Pacifica Route** (SUP-PR, aggregator), **Cobalt SMS** (SUP-CB, cheap best-effort route, only used in v4). Enterprise senders: Banco Litoral (OTP, 50%), Rapido Envios (transactional, 35%), Mercado Sol (marketing, 15%).

Routing: Altavia 80% Northbridge / 20% Pacifica; Verdemovil 70/30 until an automatic rebalance to 60/40 on 09-18 (a harmless distractor). Normal final delivery on Altavia: Northbridge 94.5%, Pacifica 92.0%, blended **94.0%**. Volumes follow Mexico City daytime with lower weekends; about 350,000 messages over the horizon at full scale.

Timeline (2026-09-22, UTC; Mexico City is UTC−6):

| Time | Event | Record |
|---|---|---|
| 09-15 16:00 | Northbridge notifies maintenance 14:00–16:00 on 09-22, "no impact expected" on MT/MO/DR | `notices/MN-NB-2026-0922` |
| 14:00–16:00 | Maintenance window, delivery normal | messages |
| 16:05 | Northbridge→Altavia drops to 62% (UNDELIV 022, EXPIRED 000). Pacifica→Altavia and Northbridge→Verdemovil normal | messages |
| 16:30 | Active test via Northbridge to Altavia fails; via Pacifica arrives | `tests/` |
| 16:35 | Clause 5.1 P1 condition met (raw < 80% for 30 min) | R7 |
| 16:40 | Banco Litoral opens a case: OTPs not arriving on Altavia | `tickets/customer/SF-00012871` |
| **17:10** | **Alarm: 68.0% vs baseline 94.0%, CRITICAL** (sim start) | `alerts/` |
| 17:25 | Ops review; analyst removes the service credit from the escalation (credits go via the account manager, clause 7.2); escalation submitted | runner |
| 17:40 | Northbridge acknowledges, ticket NB-T-551203 | runner |
| 19:30 | Bind re-provisioned (data recovers) | messages |
| 19:40 | Northbridge: root cause and fix time | runner |
| 23:05 | Recovery verified (20:00, 21:00, 22:00 at baseline); close | R6 |

At the alert (window 16:05–17:05, read 17:10): network 68.0% of 2,250 messages, receipt coverage 91.1%; Northbridge 62.0% of 1,800, Pacifica 92.0% of 450; Verdemovil 94.1%; failures dominated by UNDELIV 022 (453). Northbridge's day SLA on Altavia: 91.2% vs 95.0% target, breach, credit 49.97 USD (recorded in the case, not in the escalation). Contradiction to surface: the maintenance notice said no impact, yet the drop began 5 minutes after the window.

## 8. Hidden cases (variants)

| ID | What differs | Trap | Right outcome |
|---|---|---|---|
| v1 | No supplier fault. Mercado Sol sends a burst to a bad list 16:00–17:30 (+49% volume, UNDELIV 001/011). | **Misleading alert**: same 68.0% and the alarm blames the Northbridge route | Both suppliers drop alike, excluding Mercado Sol the rate is 93.8%, tests pass, errors are subscriber errors (SLA-excluded). No supplier escalation; internal note to the account manager; close |
| v2 | Base fault lasts until 20:40. Northbridge first replies at 18:10 "no fault found, closing". | **Incomplete carrier status**: a resolved ticket without root cause while data still shows 62% | Reject the closure (clause 5.4), send a follow-up with fresh samples, escalate to level 2 at 19:25 (clause 5.2), accept the 20:50 reply with root cause, verify recovery at 00:05 |
| v3 | No supplier fault. Altavia emergency maintenance 16:00–18:00: messages queue and deliver 18:00–18:25. Only Pacifica forwarded the operator notice (15:30). | **Wait for more evidence**: same 68.0%, but receipt coverage is 71.6% and errors are normal | Recommend wait and recheck after 18:00; queued messages deliver; close with owner = operator, no escalation |
| v4 | Our routing team moves 70% of Altavia to Cobalt at 16:00 (NETOPS-2231); Cobalt is filtered (UNDELIV 011/009, 56.6%); reverted at 17:45. | Own-side fault; tempting to escalate to a supplier | Owner = our routing change; recommend the revert (a person does it); no SLA claim (Cobalt has none); verify recovery at 21:05 |

Every variant's alarm reads 68.0% vs 94.0% so the alert alone never decides. The P1 raw-delivery condition is also met in v1 and v3, on purpose: it is a contract trigger, not a diagnosis.

Ground truth per scenario is in `answer-keys/<id>.json` (generated, keep away from agents): alert-window figures by supplier, customer and control network; hourly rates and baselines; recovery time; SLA figures; and `truth` with the cause, owner, expected hypothesis verdicts, contradictions, expected actions with sim times and what the escalation must and must not include.

## 9. Adapter interface

```
alerts.list(as_of, since=None) -> [Alarm]
messages.window_stats(t0, t1, as_of, network=None, supplier=None, customer=None, exclude_customer=None) -> Stats   # R1, R2, R4
messages.baseline(t0, t1, lag, **filters) -> Stats                                                                # R3
messages.sample(t0, t1, as_of, network, supplier, stat=None, err=None, n=10) -> [Message]                          # escalation samples
messages.get(msg_id, as_of) -> Message
routing.history(network, as_of) -> [RouteChange]
routing.weights_at(network, t) -> {supplier_id: weight}
suppliers.get(supplier_id) -> SupplierRecord
suppliers.agreement(agreement_id) -> Agreement           # shared contract schema + SMS SLA terms
suppliers.clause(agreement_id, clause_id) -> Clause
notices.list(as_of, network=None, since=None) -> [Notice]
tickets.customer(as_of, account_id=None) -> [Case]       # Salesforce-shaped, shared connector
tests.list(as_of, network=None, supplier=None, since=None) -> [ActiveTest]
portal.submit(submission) -> submission_id               # requires approval_id
portal.responses(ticket_or_submission_id, as_of) -> [Response]
cases.* / ledger.* / outbox.send(note)                   # shared, as prototype one
resolve(cite_id) -> record + source
```

## 10. Scenario runner

`scenario.yaml` per scenario (written by the generator into each data root), replayed by `telco_platform.sim.runner`:

```yaml
id: base
sim_start: 2026-09-22T17:10:00Z
clock: advances only while the case waits (supplier, recheck, recovery window)
analyst:
  latency: PT15M
events:
  - at: 2026-09-22T17:10:00Z
    action: open_case_from_alarm
    alarm_id: ALM-2026-0922-0042
  - "on": escalation_submitted   # quoted: unquoted YAML reads `on` as true
    to: SUP-NB
    after: PT15M
    action: supplier_response
    response: {status: acknowledged, ticket_id: NB-T-551203}
  - "on": escalation_submitted
    to: SUP-NB
    at: 2026-09-22T19:40:00Z     # delivered at this time, or on submission if later
    action: supplier_response
    response: {status: resolved, root_cause: "...", fix_time: 2026-09-22T19:30:00Z}
analyst_script:
  - at_step: 5
    decision: revise
    edits: {remove_section: service_credit}
```

- `on:` events fire only if the prototype did the triggering action (an escalation never sent gets no reply).
- `analyst_script` drives the human gate in automated runs; the demo UI replaces it with a real person. Each decision takes `analyst.latency` of sim time.
- `wait_and_recheck` and the recovery check ask the runner to advance the clock to a requested time.

**Known limit.** The messages are pre-generated, so the supplier's fix (19:30 in base, 20:40 in v2) and the revert in v4 (17:45) happen at fixed times. A run that escalates much later than the expected path will see recovery before its escalation. The runner flags that as "off the expected path" rather than pretending causality.

## 11. What to score (from the brief's success measures)

Per scenario, the eval harness compares the case to the answer key:
1. Probable cause and owner match `truth.owner_kind` / `truth.owner`.
2. Hypothesis verdicts match; alternatives are listed when records conflict (`truth.contradictions` surfaced).
3. Action matches (`escalate_supplier`, `notify_customer`, `wait_and_recheck`, `internal_containment`), at or before the expected sim time.
4. Escalation packet contains every `escalation_must_include` item, nothing in `must_not_include`, and every number traces to a ledger calc.
5. The analyst revision is honoured (base) and the packet regenerates consistently.
6. Recovery verified at `recovery.verified_at`, never closed earlier.
7. Brief quality (rubric, human or LLM judge) and wall-clock time to prepare the brief. Not MTTR: the brief is explicit that a simulation cannot show real-world MTTR.

## 12. Open questions for user interviews

- Are delivery targets usually measured with subscriber errors excluded, and over a day or shorter?
- Who owns the decision to shift traffic during an incident: the NOC on duty, or a routing desk?
- Do suppliers accept escalations by email with sample IDs, or only through a portal?
- How often do operator maintenance notices reach the buyer only through one supplier (v3)?

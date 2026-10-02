# Industry records and case formats: research for the Operations Resolution prototype

Prototype two: an A2P SMS delivery incident (delivery to one country and mobile network falls from 94% to 68%). Goal: know what the real records look like so we can simulate them faithfully without integrating any real system. Same approach and source key as prototype one's research notes.

Source key: **[S]** backed by a cited source below. **[K]** general industry knowledge, not verified against a source in this pass; treat it as a strong default to confirm with a practitioner (ideally one of the target users we still need to interview).

---

## 1. The short answer

A2P messaging has one real wire standard, **SMPP 3.4/5.0**, and almost everything an operations team looks at during a delivery incident is derived from it: submit responses (`command_status`), delivery receipts (DLRs) with a final state and a network error code, and per-route traffic counts. Everything around it (route plans, supplier SLAs, maintenance notices, NOC tickets) is bilateral and lives in email, supplier portals, CRM and ITSM tools. TM Forum gives neutral shapes for the alert (TMF642), the service problem (TMF656) and the ticket (TMF621). [S: smpp.org, ServiceNow TMF642, TM Forum]

| Record | What is common in industry | Closest open shape to model it on |
|---|---|---|
| Message record (submit + DLR) | Platform log of each MT: submit time, route/supplier, `command_status`, receipted DLR with `stat` and `err` | SMPP 3.4 `submit_sm_resp` + `deliver_sm` receipt text and TLVs |
| Error codes | DLR `err` carries a GSM MAP / network error (1 unknown subscriber, 6 absent, 13 barred, 22 system failure); submit errors are SMPP `command_status` (0x58 throttled, 0x45 submit fail) | 3GPP TS 29.002 MAP errors; SMPP 3.4 §5.1.3 |
| Traffic and delivery KPIs | Per country / network (MCC-MNC) / supplier / hour: submitted, delivered, failed, DLR coverage, latency | Platform dashboards; no standard [K] |
| Monitoring alert | Threshold crossing on delivery rate or DLR ratio, raised by NOC tooling | TMF642 Alarm (`perceivedSeverity`, `probableCause`, `crossedThresholdInformation`) |
| Route plan and change history | Route table per destination network: supplier, priority/weight, cost; change log with who/when/why | No standard; LCR tables in SMS hub products [K] |
| Supplier SLA | Agreement schedule: delivery target, DLR target, latency, NOC response/restore times, escalation matrix, maintenance notice, credits | TMF651 Agreement (same as prototype one) |
| Maintenance notice | NOC email or status page post: type (planned / emergency / partner incident), window, affected MO/MT/DR, impact text | No standard; vendor NOC formats [S: Sinch NOC FAQ] |
| Carrier exchange / escalation | Email to supplier NOC or their portal ticket; supplier assigns its own ticket ID | TMF621 Trouble Ticket |
| Incident case | NOC/ops ticket (Jira Service Management, ServiceNow, Salesforce Case) linked to the alert | TMF656 Service Problem + TMF621 |
| Active test results | Test SMS sent to real handsets/test numbers to confirm true delivery (catches fake DLRs) | Vendor formats (TelQ and similar) [S: TelQ] |

---

## 2. Message records, delivery receipts and error codes

**The DLR.** An SMSC returns a delivery receipt as a `deliver_sm` (or `data_sm`) whose `short_message` holds a de facto text format: `id:<msg_id> sub:001 dlvrd:001 submit date:YYMMDDhhmm done date:YYMMDDhhmm stat:DELIVRD err:000 text:...`. Final states are DELIVRD, EXPIRED, DELETED, UNDELIV, ACCEPTD, UNKNOWN, REJECTD; ENROUTE is intermediate. The TLVs `receipted_message_id`, `message_state` and `network_error_code` carry the same information in structured form. [S: smpp.org delivery receipt]
- The `id` in the receipt is the **supplier's** message ID returned in `submit_sm_resp`, not ours. Correlating our message ID to the supplier's is the first thing any escalation needs, because the supplier can only search by their ID. [S: smpp.org; K]
- `submit date`/`done date` have minute resolution and are in the SMSC's local time unless stated [K]. The simulation stores UTC ISO timestamps and keeps the raw receipt text as a separate field so the parser path is still exercised.

**Error codes.** `err` is platform-specific but in practice usually a GSM MAP error from the destination network [S: smpp.org, tyntec]:

| err | Name | Typical meaning | Permanent? |
|---|---|---|---|
| 1 | Unknown subscriber | Number not assigned | yes |
| 5 | Unidentified subscriber | HLR/MSC mismatch | no |
| 6 | Absent subscriber SM | Handset off / out of coverage | no |
| 9 | Illegal subscriber | Authentication failed; can indicate filtering | yes |
| 11 | Teleservice not provisioned | SMS disabled for the receiver, or blocked by the network | yes |
| 13 | Call barred | Barred, or deactivated by the operator | no |
| 15 | Facility not supported | Can indicate network filtering | no |
| 20 | SM delivery failure | Network-side delivery failure | no |
| 22 | System failure | Generic error in the destination network | no |

[S: tyntec GSM error codes]. Two practical consequences for the prototype:
- **Subscriber errors (1, 9, 11 and often 13) say something about the number list, not the route.** A drop dominated by err 1 points at the sender's data (a bad campaign list), not at the supplier. SLAs commonly exclude these from the delivery target [K].
- **Network errors (20, 22) and EXPIRED concentrated on one supplier** point at that supplier's connection to the operator; the same errors on every supplier point at the operator.

**Submit-side errors** are SMPP `command_status` values in `submit_sm_resp`: 0x00 OK, 0x08 system error, 0x0B invalid destination, 0x14 queue full, 0x45 submit failed, 0x58 throttled. [S: smpp.org error codes] These fail before the message reaches the supplier's SMSC, so they show up as submit rejects rather than DLR failures.

**How delivery is measured.** Vendors measure a success ratio (delivered / sent) and separately a **DLR ratio** (share of messages that got any final status); "a healthy route provides statuses for over 97% of messages", and variation over time matters more than absolute figures because some networks return DLRs sporadically. [S: Vonage] Latency variation is monitored the same way. This matters for our hidden cases: a low delivery rate with a low DLR ratio may just mean receipts are late (wait), while a low delivery rate with a normal DLR ratio and hard errors is a real failure.

**Fake DLRs.** Some routes return DELIVRD receipts for messages that never reached a handset; filtering often only affects bulk traffic so a single test passes. The countermeasure is active testing: send to real test numbers inside a batch and compare. [S: TelQ, CM.com] Not part of the 94→68 case, but the simulation includes a small active-test feed so the agent can use "test SMS arrived on handset" as independent evidence.

## 3. Traffic, baselines and alerts

- Operations teams watch delivery rate per destination network (MCC-MNC), per supplier route and per hour, against a baseline such as the same hour over the previous 7 days [K]. Volumes vary strongly by hour and by sender (OTP vs marketing), so a drop with a volume spike from one sender is a classic false lead [K].
- **Alert shape.** TMF642 Alarm has `externalAlarmId`, `alarmType` (e.g. QualityOfServiceAlarm), `perceivedSeverity` (CRITICAL, MAJOR, MINOR, WARNING, CLEAR), `probableCause` (e.g. "Threshold crossed"), `alarmedObject`, `alarmRaisedTime`, `alarmReportingTime`, `serviceAffecting`, and `crossedThresholdInformation` {direction, indicatorName, observedValue, thresholdId}. [S: ServiceNow TMF642 API] This is a good, neutral shape for the monitoring alert that opens the case.
- An alert is a claim about a metric, not a diagnosis. Its title often names a suspected cause ("supplier degradation") that the evidence must confirm or reject [K].

## 4. Routing and route history

- A2P hubs pick a supplier per destination network using routing rules over price, quality, sender and content, with load balancing and failover. [S: Openmind Networks; Horisen] Least-cost routing (LCR) is the standard term. [S: Wikipedia LCR]
- A route plan entry is typically: destination (country / MCC-MNC), supplier connection, priority or weight, price, traffic filter (sender, customer, message type), valid from [K]. Changes are made by a routing team, often under a change ticket, and kept as a history (who, when, old and new weights, reason) [K].
- A route change made just before a drop is the first internal suspect, so the change log is core evidence for ownership. Teams cannot always tell whether a change was made by a person or an automatic quality rule; the log should record which [K].

## 5. Supplier SLAs and agreements

- A2P supplier agreements are bilateral and rarely public. SLAs are "measurable and verifiable terms"; typical subjects are uptime, delivery performance and support availability. [S: Messaggio glossary] Concrete targets below are **[K]** and should be validated with target users:
  - Delivery rate target per destination network, measured on DLRs, excluding subscriber-caused errors (unknown, barred, absent past validity).
  - DLR coverage target (around 97% as above) and DLR latency (e.g. 95% within 30 s).
  - NOC incident priorities with response and restoration times (e.g. P1 when delivery to a network falls below a floor for 30 minutes: respond in 30 min, restore in 4 h).
  - Escalation matrix (NOC → duty manager → account manager) with contacts and time triggers.
  - Planned maintenance notice period, and whether maintenance windows are excluded from SLA measurement.
  - Service credits as a share of that day's charges for the affected network when the target is missed.
  - What evidence an escalation must carry: supplier message IDs, timestamps, destination ranges, error codes.
- The agreement is the same "contract PDF + structured terms" pattern as prototype one, so it should go through the shared contract parser with an SMS SLA term schema.

## 6. Maintenance notices and carrier communications

- Messaging NOCs send notifications that describe the activity (scheduled maintenance, an SMSC destination connection potentially unavailable, partner incident) and the impact per direction: **MO, MT and DR** (delivery receipts), with impacts such as "No impact expected" or "will queue at carrier (or operator) end", meaning messages are held and delivered after the issue clears. Activities can happen at any time inside the stated window. [S: Sinch NOC FAQ] Public status pages post the same as incidents ("SMS carrier partner maintenance"). [S: Twilio status, Medallia status]
- Two simulation hooks come straight from this: a notice that says "no impact expected" while the data shows impact (a contradiction to flag), and an operator-side window where MT and DR queue (the right action is to wait).
- Carrier exchanges are emails or portal tickets. The supplier opens its own ticket ID, often answers first with a generic "no issue found on our side" and asks for message IDs [K]. That incomplete first answer is our second hidden case.

## 7. Incident cases and tickets

- **TMF656 Service Problem** is the TM Forum shape for "something is wrong with a service, cause unknown": affected service, impact, priority, status, root cause, related alarms, related trouble tickets, tracking records [S: TM Forum TMF656 directory; field names K].
- **TMF621 Trouble Ticket** (already used in prototype one) is the shape for the customer ticket and for the ticket we open with the supplier. Salesforce maps it onto the Case object. [S: Salesforce TMF621 mapping]
- In practice the internal incident sits in Jira Service Management or ServiceNow, customer complaints arrive as Salesforce Cases, and the supplier ticket is an email thread or portal entry [K]. The shared platform plan already proposes Jira- and Salesforce-shaped simulated backends; prototype two only needs the payloads.

## 8. Market reference (from Federico's brief, not re-researched here)

Netcracker (cross-domain intelligent operations) and Nokia (AI network troubleshooting and root-cause guidance) cover broad AI operations. The wedge is narrower: one A2P delivery incident for one destination network, from an existing alert to an approved, evidence-backed supplier escalation and verified recovery. Whether current vendor tools already do this handoff is the open question for user interviews.

## 9. What this means for simulation

Each external system becomes a folder of files plus a read-only adapter, as in prototype one. The concrete design is in [simulated-systems.md](simulated-systems.md). The records to fake, in order of importance: message records with DLRs and error codes; the hourly traffic roll-up; the alert; route history; supplier SLAs (contract documents); maintenance notices; the supplier NOC exchange; customer tickets; active test results.

---

## Sources

- SMPP delivery receipt format: https://smpp.org/smpp-delivery-receipt.html
- SMPP command_status error codes: https://smpp.org/smpp-error-codes.html
- tyntec GSM error codes: https://www.tyntec.com/docs/docs-center-sms-api-gsm-error-codes
- 8x8 SMPP delivery receipts: https://developer.8x8.com/connect/docs/smpp-delivery-receipts
- Vonage, how SMS delivery success is measured: https://api.support.vonage.com/hc/en-us/articles/203779156-How-is-SMS-delivery-success-measured
- TelQ, SMS fake delivery receipts: https://telqtele.com/blog/sms-fake-delivery-receipts
- CM.com, how fake DLRs devalue the SMS market: https://cm.com/blog/how-fake-dlr-devaluate-a-well-functioning-sms-market
- ServiceNow Alarm Management Open API (TMF642): https://www.servicenow.com/docs/r/api-reference/rest-apis/alarm-open-api.html
- TM Forum TMF656 Service Problem Management: https://www.tmforum.org/oda/open-apis/directory/TMF656
- Salesforce TMF621 mapping: https://developer.salesforce.com/docs/industries/communications/guide/tmf621_resource_mapping.html
- Openmind Networks, message routing: https://www.openmindnetworks.com/blog/message-routing-how-messages-get-delivered-efficiently/
- Horisen, SMS platform routing: https://www.horisen.com/understanding-the-backbone-of-horisen-sms-platform-routing
- Least-cost routing: https://en.wikipedia.org/wiki/Least-cost_routing
- Messaggio glossary, SLA: https://messaggio.com/glossary/sla/
- Sinch Enterprise Messaging NOC notifications FAQ: https://community.sinch.com/sybase/attachments/sybase/EnterpriseTKB/193/3/Enterprise%20Messaging%20NOC%20Notifications%20-%20FAQ%20v2.pdf
- Twilio status (carrier maintenance example): https://status.twilio.com/incidents/twvwkgxy89nc
- LINK Mobility, how planned maintenance affects business messaging: https://www.linkmobility.com/blog/how-planned-maintenance-affects-business-messaging

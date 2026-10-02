# Industry records and case formats: research for the Assurance Resolution prototype

Thread 1 of the prototype plan. Research only, no design. Goal: know what the real records look like so we can simulate them faithfully without integrating any real system.

Source key: **[S]** means the point is backed by a cited source below. **[K]** means it comes from general industry knowledge and was not verified against a source in this pass; treat it as a strong default to confirm with a practitioner.

---

## 1. The short answer

There is no single universal standard for wholesale voice or A2P messaging settlement. Mobile roaming is the exception (GSMA TAP/RAP and its successor BCE). Everything else is bilateral: each carrier sends its own CSV/Excel rate deck, its own invoice layout, and disputes over email or a partner portal. Reconciliation vendors exist mainly to normalize that mess. [S: Commsrisk, Telarix, Subex]

That is good news for the prototype. A realistic simulation only needs to reproduce a handful of de facto shapes:

| Record | What is common in industry | Closest open standard to model it on |
|---|---|---|
| Contract / interconnect agreement | PDF master agreement plus schedules (rates, billing, disputes); terms live in a CLM or a billing system's "agreement" object | TM Forum TMF651 Agreement |
| Rate sheet (rate deck) | CSV/Excel per carrier, one row per dial code, with effective date and change flag | TMF620 price with `validFor`; CGRateS tariff plan model |
| Usage (CDRs / EDRs) | Switch/SBC CSV exports, or ASN.1 from mobile core | FCS UK Standard CDR format; TMF635 Usage |
| Rated charges | Internal billing/rating engine output, CDR plus cost and rate applied | CGRateS rated CDR; TMF635 `ratedProductUsage` |
| Carrier invoice | PDF plus summary by destination (CSV/Excel), detail CDRs on request | UBL 2.1 / Peppol BIS 3.0 (EN 16931); GSMA EID for roaming |
| Discrepancy / dispute case | Ticket in a settlement tool, CRM, or ITSM; carrier assigns its own claim number | TMF621 Trouble Ticket |

---

## 2. Contracts between carriers and wholesalers

**How they are written.** A master interconnect or wholesale services agreement with schedules. The schedules that matter for this case are charges, billing, and dispute procedure. Example: the SingTel interconnection agreement's billing schedule says invoices go out within 14 days of the billing period, payment is due 30 days from invoice date, a dispute must be raised in writing within 14 days of the invoice, the undisputed portion must be paid on normal terms, and unresolved disputes escalate to management after 30 days. [S: IMDA/SingTel Schedule 4]

**Terms the prototype must capture** (all appear in the sources or are standard [K]):
- Parties, agreement ID, start/end, currency, billing cycle (monthly, weekly or daily for prepaid voice) [K]
- Rate schedule reference and the **rate change notice period** (commonly 7 days for increases, immediate for decreases in international voice) [K]
- Billing increments (60/60, 30/6, 1/1) and rounding rules [K]
- Invoice timing, payment terms, dispute window, dispute threshold, obligation to pay undisputed amounts [S: IMDA]
- Dispute evidence expected: billed vs correct rate, period, account, supporting detail [S: Brightspeed]

**Where they are stored.**
- The signed PDF sits in a contract lifecycle tool or document store; the machine-usable terms are re-keyed into the billing or settlement system as an "agreement" object. [K]
- Settlement products model agreements explicitly: Enghouse advertises rating, volume commitment modelling and revenue-share schemas per partner; Subex's partner settlement and Telarix's iXTools cover agreement, rate and billing management. [S: Enghouse, Subex, Telarix]
- TMF651 Agreement gives a neutral schema: `name`, `documentNumber`, `agreementType`, `status`, `agreementPeriod`, `engagedParty` (the two carriers), `agreementItem` (what is covered), `agreementSpecification` (template), `associatedAgreement` (amendments). [S: Salesforce TMF651 mapping]
- Roaming is the one area with a standard commercial-terms exchange: GSMA RAEX lets operators exchange roaming agreement data and commercial rates electronically. [S: GSMA IDS]

## 3. Rate sheets (rate decks) and effective dating

**Common shape [K, partly S]:** one row per dial code (E.164 prefix), with destination name, rate per minute (or per message for SMS, keyed by MCC/MNC), currency, effective date, change indicator (New / Increase / Decrease / Unchanged / Closed), billing increments, and sometimes connection fee. Carriers send either a full A-Z deck or a partial update.

**How tools store history.** Rate management tools keep every uploaded deck as a version: e.g. a rate-deck history screen listing ratesheet name, source CSV file name, load status, total rates, rounding, effective date(s), who modified it, and when. [S: 46labs PeerEdge docs]

**How rating engines model effective dates.** CGRateS (open-source telecom rating engine) splits a tariff into Destinations (prefix groups), Rates (connect fee, rate, rate unit, rate increment, interval start), DestinationRates, Timings, RatingPlans, and RatingProfiles bound to an **activation time**. [S: CGRateS docs] TM Forum APIs generally use a `validFor` start/end period for the same purpose [K].

**Why this is the right failure to simulate.** Unannounced or mis-dated rate changes are a known pain point: carriers change decks without clear notice, and teams that do not check imported decks only notice when billing or margin is hit. [S: Neosmart forum] The classic version of our 8% case is a carrier billing a new, higher rate from a date earlier than the contract's notice period allows, or an internal system still rating at the old rate [K].

## 4. Usage records and rated charges

**Voice CDRs.** Exported from the switch or SBC, usually CSV [K]. The UK FCS Standard CDR format (v3, 42 fields) is a public, well-documented example: call type, CLI, dialled number (E.164), call date (DD/MM/YYYY), call time, duration in whole seconds, destination description, charge code, time band, sale price to 4 decimals, carrier, network, and more. Files follow a naming convention with provider, frequency, account, date, sequence number, record count and version. [S: FCS UK Standard CDR v3] Fields typical in wholesale switch CDRs but not in that format: ingress/egress carrier or trunk, connect and disconnect timestamps in UTC, SIP Call-ID, release cause [K].

**Mobile.** Core network CDRs use 3GPP ASN.1 formats; roaming usage between operators uses TAP files, with RAP for rejections and NRTRDE for near-real-time fraud data. [S: GSMA IDS] GSMA BCE replaces per-record TAP with aggregated reports (Usage Data Report, Billing Statement Report, Detailed Data Records on request), supports flexible settlement periods, and adds a standard reconcile, reject and dispute process (GSMA TD.201 to TD.206). [S: GSMA BCE, Telecoms.com]

**A2P SMS.** Billing can be on submission, on attempt, or on delivery (DLR received). Rates vary by country, network (MCC/MNC) and route, and rate sheets are CSV uploads. [S: REVE] Billing basis mismatch (they bill submitted, we count delivered) is a standard messaging discrepancy [K].

**Rated charges.** The internal billing engine's output is the CDR plus rate applied, rate version, rounded duration and cost [K]. TMF635 Usage Management is the neutral schema for usage with rated usage attached. [S: TM Forum TMF635]

## 5. Carrier invoices

- Usually a PDF invoice plus a summary by destination (calls, minutes, rate, amount), with CDR detail supplied on request or in a portal [K]. Formats are inconsistent across partners, which is why reconciliation teams spend effort loading, translating, mapping and aggregating before they can compare. [S: Commsrisk]
- Invoices are expected to carry enough "billing verification information" for the buyer to check the charges. [S: IMDA]
- Roaming has GSMA EID (Electronic Invoice Data), RTDR (traffic report for invoicing) and PNR (payment notification for matching payments). [S: GSMA IDS]
- Some national schemes standardize interconnect exchange: Codifi (Spain), PISA (Mexico), DETRAF (Brazil), with limited adoption. [S: Commsrisk]
- For a generic, well-specified invoice schema, UBL 2.1 / Peppol BIS Billing 3.0 (EN 16931) gives header, invoice period, parties, and invoice lines. [S: Maventa, eConnect]

## 6. How discrepancy and dispute cases are opened and tracked

**Inside settlement / assurance tools.** Subex's reconciliation and dispute management loads invoices from email/FTP, compares summary and detail against internal records, auto-raises disputes above predefined thresholds, splits each invoice into a non-disputed and a disputed version, and auto-resolves after re-rating. [S: Subex] Telarix's iXLink is a document exchange network (over 4,000 members) used to exchange rate decks and invoices between carriers. [S: Telarix press release, July 2024] Mobileum, Enghouse, Syniverse and Nexign sell comparable wholesale/roaming settlement and reconciliation products. [S: vendor pages]

**On the carrier's side.** Wholesale carriers publish dispute processes. Brightspeed's: one product and one bill period per dispute, account ID, reason, disputed amount, supporting detail including billed vs correct rates; acknowledgement within 2 business days; resolution target 28 days; outcome is customer favour, carrier favour or partial, with credit "from and through" dates; a unique dispute ID that can echo the customer's own ID; written escalation. [S: Brightspeed]

**In CRM / ITSM.** Many teams track disputes as cases in Salesforce, ServiceNow or Jira [K]. Salesforce maps TMF621 Trouble Ticket onto its Case object (subject, description, severity, type, priority, status, created, closed, attachments, notes, related party and entity, parent case). [S: Salesforce TMF621 mapping] Salesforce also markets an Agentforce billing-resolution agent for retail billing disputes. [S: Salesforce]

**Neutral case schema.** TMF621 Trouble Ticket: `id`, `correlationId`, `creationDate`, `targetResolutionDate`, `resolutionDate`, `ticketType`, `severity`, `status`, `subStatus`, `statusChangeReason`, `relatedObject`, `relatedParty`, `note`. [S: TM Forum data model] Status values in v4 are acknowledged, rejected, pending, held, inProgress, cancelled, resolved, closed [K, from the spec; confirm against the OpenAPI file].

**Common root causes to seed in scenarios [K]:** rate applied before its effective date or after notice expiry; wrong billing increment or rounding; time-zone cut-off at period boundaries; duplicate CDRs; dial-code reassignment between destinations; short or zero-duration calls billed; currency conversion date; SMS billed on submit instead of delivery.

## 7. What this means for simulation (options, not a design)

Federico asked how to avoid real integrations. Based on the above, each external system can be stood in for by files or a stub that copies a public shape:

- **Contract repository:** a folder of agreement records shaped like TMF651, each pointing to a rate schedule version and holding notice period, increments, dispute window and threshold. Optionally a PDF of the "signed" agreement so citations can point at a clause.
- **Rate deck store:** versioned CSV decks in the de facto carrier layout (code, destination, rate, currency, effective date, change flag, increments), with a load-history log like the 46labs example. CGRateS's model is a reference if a real rating step is wanted.
- **Usage store:** CDR CSVs modelled on the FCS v3 field list plus carrier/trunk and UTC timestamps; for messaging, EDRs with MCC/MNC and DLR status.
- **Internal rating output:** rated CDRs with the rate version applied, so the reconciliation can cite which deck row was used.
- **Carrier invoice feed:** a PDF-like summary plus a CSV by destination, optionally a UBL XML twin; delivered into an "inbox" folder to mimic email/FTP/iXLink delivery.
- **Case tool:** a TMF621-shaped ticket store with status history and notes, plus a carrier-side claim number field.

Threads 3 and 4 would decide which of these to build.

---

## Sources

- IMDA / SingTel interconnection agreement, Schedule 4 Billing: https://www.imda.gov.sg/assets/13f9b7f9-220f-44d6-a525-6b2dbeb03920.pdf
- Brightspeed wholesale bill dispute process: https://www.brightspeed.com/ew/wholesale/clecs/billdisputeprocess
- 46labs PeerEdge, Rate Deck History: https://46labs.atlassian.net/wiki/spaces/peeredge/pages/98795534/Rate+Deck+History
- Neosmart forum, carrier rate deck changes without warning: https://neosmart.net/forums/threads/what-happens-when-a-carrier-changes-its-rate-deck-without-warning.35034/
- CGRateS rating docs: https://cgrates.readthedocs.io/en/v0.10/rals.html
- FCS UK Standard CDR Format v3: https://cdn.document360.io/334a2c15-3e03-4702-a06b-0946461deedc/Images/Documentation/UK%20Standard%20CDR%20Format%20v3.pdf
- GSMA IDS standardised B2B interfaces (TAP, RAP, NRTRDE, RAEX, RTDR, EID, PNR): https://www.gsma.com/get-involved/working-groups/interoperability-data-specifications-and-settlement-group/standardised-b2b-interfaces-specified-by-ids/
- GSMA Billing and Charging Evolution: https://www.gsma.com/get-involved/working-groups/interoperability-data-specifications-and-settlement-group/billing-and-charging-evolution
- Telecoms.com, Why billing will be better with BCE: https://www.telecoms.com/oss-bss-cx/why-billing-will-be-better-with-bce
- Commsrisk, interconnect invoice reconciliation: https://commsrisk.com/?p=7957
- REVE Systems, how SMS billing works: https://www.revesoft.com/blog/sms-platform/how-does-sms-billing-work/
- Subex reconciliation and dispute management: https://www.subex.com/solutions/ecosystem-management/wholesale-billing-and-routing/reconciliation-dispute-management/
- Telarix relaunch (iXLink, iXTools), July 2024: https://prnewswire.co.uk/news-releases/telarix-reborn-to-lead-the-next-era-of-wholesale-302189631.html
- Enghouse interconnect billing: https://www.enghousenetworks.com/products/customer-revenue-management/wholesale-revenue-management/interconnect-billing/
- TM Forum TMF621 Trouble Ticket: https://tmforum.org/oda/open-apis/directory/trouble-ticket-management-api-TMF621/v5.0
- TM Forum Ticket data model: https://datamodel.tmforum.org/en/latest/Common/Ticket/
- Salesforce TMF621 mapping: https://developer.salesforce.com/docs/industries/communications/guide/tmf621_resource_mapping.html
- Salesforce TMF651 mapping: https://developer.salesforce.com/docs/industries/communications/guide/tmf651_resource_mapping.html
- TM Forum TMF635 Usage: https://www.tmforum.org/oda/open-apis/directory/TMF635
- Salesforce Agentforce billing resolution: https://www.salesforce.com/agentforce/use-cases/communications/billing-resolution/
- UBL invoice structure (eConnect): https://econnect.eu/en/docs/knowledge/document-formats/basics/ubl-invoice-structure
- Peppol BIS 3.0 (Maventa): https://documentation.maventa.com/integration-guide/invoicing-formats/peppolbis30/

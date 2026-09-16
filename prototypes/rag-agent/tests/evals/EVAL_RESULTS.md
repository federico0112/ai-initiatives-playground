# RAG Chatbot Eval Results — NVIDIA Earnings Multi-Turn Suite

**Suite:** `tests/evals/test_rag_chatbot_e2e.py`
**Dataset:** `tests/evals/dataset_nvidia.json` (30 synthesizer-generated multi-turn conversational goldens)
**Judge model:** `gemini-2.5-flash` (all metrics)
**Last run:** round 2b (`iterating-on-nvidia-chatbot-round-2b`), 2026-09-15
**Status:** 2 of 5 planned iteration rounds complete

---

## Scores

### Run-over-run

| Round | Pass rate | Passed | Failed | Change made before this round |
|---|---|---|---|---|
| 1 | 63.3% | 19/30 | 11 | baseline |
| 2b | **76.7%** | **23/30** | **7** | capped `TurnFaithfulnessMetric.window_size=3`; set `chatbot_role` on test cases |

Round 2b: 3456s runtime, $0.38 judge cost.

### Per-metric (round 2b)

| Metric | Pass | Rate | Round 1 | Notes |
|---|---|---|---|---|
| Turn Relevancy | 30/30 | 100% | 100% | Stable. No action needed. |
| Role Adherence | 29/30 | 97% | *never ran* | Was silently skipped in round 1 (no `chatbot_role`). Now active. |
| Turn Faithfulness | 28/30 | 93% | 80% | Both remaining failures are **infra errors, not quality failures**. |
| CrossTurnConsistency (GEval) | 25/30 | 83% | 80% | **All 5 failures are genuine quality defects.** Primary target. |

### Failure classification (round 2b, 7 failing cases)

| Kind | Count | Cases |
|---|---|---|
| Genuine quality failure (CrossTurnConsistency) | 5 | 9, 11, 20, 23, 29 |
| Genuine quality failure (Role Adherence) | 1 | 11 |
| Infra/transient (judge timeout, upstream 503) | 2 | 14, 18 |

Case 11 fails two metrics. Infra failures are *not* app defects — see A4.

---

## Root-cause findings

### F1 — Answers truncate mid-sentence (highest confidence, highest impact)

**Evidence:** 14 of 112 assistant turns (12%) end without terminal punctuation, cut mid-word or mid-citation:

```
"...potentially disadvantaging the company (2026AnnualReport"
"...Subsequent to FY20"
"...The program is flexible and"
```

Truncated turns are only 1152–3391 chars (~300–900 visible tokens) — far below the configured `max_output_tokens: 2048` in `rag/pipeline.py:184`.

**Hypothesis:** `gemini-2.5-flash` is a thinking model, and reasoning tokens count against `max_output_tokens`. The budget is being consumed by thinking before the visible answer completes. This is the likely mechanical cause of most truncation.

**Why it matters beyond cosmetics:** the judge explicitly penalized truncation in cases 11, 23, and 9 ("ended abruptly, lacking completeness", "cut off mid-sentence"). It also *causes* F2 — a turn that dies mid-answer leaves the model's own history containing a half-stated fact it then contradicts.

### F2 — Self-contradiction about what the documents contain

The dominant CrossTurnConsistency failure pattern: the assistant asserts data is absent in one turn, then supplies it in another (or vice versa).

- **Case 9 (score 0.1, worst):** repeatedly failed to extract cash flow data, then explicitly claimed the documents "do not contain the Consolidated Statements of Cash Flows" — despite those statements being present in *prior turns'* retrieval context.
- **Case 29 (0.6):** turn 2 said Data Center growth percentages were unavailable, though turn 1's context stated "Data Center revenue for fiscal year 2026 was up 68% from a year ago."
- **Case 11 (0.6):** turns 1–2 said the auditor's opinion wasn't in the documents; turn 3 said it was.

**Mechanism:** retrieval is re-run per turn with only the *current* user message as the query (`test_rag_chatbot_e2e.py:59-77`, mirroring `run_rag_query`). Context retrieved in turn 1 is gone by turn 3. The model sees its own prior *answers* in history but not the prior *evidence*, so a weak retrieval on one turn reads as "this data doesn't exist."

### F3 — Fiscal-year and figure mix-ups across documents

Three annual reports (FY2024/2025/2026) are in the same index and get conflated.

- **Case 23 (0.5):** cited a "Fiscal 2024 report" while using Fiscal 2026 segment context; also reported FY2024 total revenue as `$60.9 billion` in turn 1 and `$60,955 million` in turn 2 (same number, inconsistent presentation flagged by the judge).
- **Case 29 (0.6):** stated 65% net income per diluted share growth where context said 67%.
- **Case 20 (0.5):** contradicted itself on FY2025 goal rigor; leaked FY2026 information into a FY2025 answer.

### F4 — Minor role drift (case 11, Role Adherence 0.67)

Response introduced "e.g., unqualified opinion" as an illustrative example not grounded in the retrieved documents. The system prompt (`rag/pipeline.py:60`) does not forbid outside-knowledge elaboration.

---

## Action items

Ordered by expected value. **A1 is the prerequisite for a clean read on everything else** — do it first and re-run before attempting A2/A3, since truncation contaminates the other metrics.

### A1 — Fix truncation (start here)

**Files:** `rag/pipeline.py:184`

1. Raise `max_output_tokens` (try 4096) **and** explicitly configure the thinking budget so reasoning tokens don't starve the visible answer. Check the `google-genai` / Haystack `GoogleGenAIChatGenerator` surface for a `thinking_config` / `thinking_budget` passthrough in `generation_kwargs`; if the Haystack wrapper doesn't expose it, that's the blocker to solve first.
2. Add a finish-reason check in `_generate_chat_response` (`rag/pipeline.py:169`): the Gemini response carries a finish reason (e.g. `MAX_TOKENS`); log a warning when a generation is cut short instead of silently returning a partial answer.

**Verify:** re-run the suite; the "no terminal punctuation" rate should drop well below 12%. Measure with:

```python
# over .deepeval/.latest_test_run.json assistant turns
content[-1] not in ".!?\"')]}:"
```

### A2 — Carry retrieval context across turns (addresses F2)

**Files:** `rag/pipeline.py:202` (`run_rag_query`), `rag/session.py:14` (`HISTORY_WINDOW`)

The fix is to stop treating each turn's retrieval as independent. Options, cheapest first:

1. **Query rewriting** — condense `chat_history` + current message into a standalone retrieval query before embedding. This is the standard fix for follow-ups like "what about vs last year?" and likely resolves most of F2 on its own.
2. **Sticky context** — carry forward the previous turn's retrieved chunks alongside the current turn's, so evidence from turn 1 is still visible at turn 3.

Also strengthen the system prompt (`rag/pipeline.py:60`): instruct the model that absence from the *current* retrieval does not mean absence from the corpus, and that it must not contradict facts it previously stated.

**Verify:** CrossTurnConsistency pass rate should rise above 83%; cases 9, 11, 29 specifically should improve.

### A3 — Disambiguate fiscal years (addresses F3)

**Files:** `rag/pipeline.py:65` (`RAG_TEMPLATE`)

The template already passes `doc.meta.filename` and pages. Make the fiscal year impossible to miss — state it explicitly per document block and instruct the model to name the fiscal year and source document in every figure it reports, and never to blend figures across reports without labeling them.

**Verify:** cases 20, 23, 29 should stop mixing FY2024/2025/2026.

### A4 — Stabilize the harness (not app defects, but they cost 2 cases/run)

1. **Judge timeout (case 14):** one `TurnFaithfulnessMetric` call still exceeded 88.5s. Either lower `window_size` from 3 to 2 (`tests/evals/metrics.py:70`) or raise `DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE`.
2. **Upstream 503 (case 18):** transient Gemini unavailability. Retry-on-503 around the judge, or simply re-run.
3. **Collection-time fragility:** `simulator.simulate()` runs at module import (`test_rag_chatbot_e2e.py:98`), so a *single* transient API error aborts the entire run with zero results — this already cost one full round (2a). Consider caching simulated conversations to disk and reusing them across scoring runs, which also makes A1–A3 comparisons apples-to-apples (same conversations, different app behavior) and cuts the ~58min runtime substantially.

### A5 — Follow-ups (lower priority)

- Tighten the system prompt against un-grounded elaboration (F4, case 11).
- `tests/evals/metrics.py` thresholds are all `0.7`; unchanged and not yet tuned.
- Consider adding retrieval-quality span metrics once tracing is sent somewhere inspectable — tracing is instrumented (`@observe` on `_run_retrieval`, `_generate_chat_response`, `run_rag_query`) but currently no-ops without a Confident AI key.

---

## How to run

```bash
cd prototypes/rag-agent

# One-time: load the NVIDIA PDFs into the ISOLATED eval database
python tests/evals/load_nvidia_documents.py

# Run the suite
export GOOGLE_API_KEY="$GEMINI_API_KEY"   # deepeval's Gemini judge reads GOOGLE_API_KEY
deepeval test run tests/evals/test_rag_chatbot_e2e.py \
  --identifier "iterating-on-nvidia-chatbot-round-3" \
  --ignore-errors --skip-on-missing-params

# Regenerate goldens (only if the source PDFs change)
python tests/evals/generate_nvidia_dataset.py
```

Results land in `.deepeval/.latest_test_run.json` — parse `testRunData.conversationalTestCases[].metricsData[]` for per-metric scores and reasons.

⚠️ **Database isolation:** eval data lives in the `rag_agent_eval` MongoDB database, **not** production `rag_agent`. This is set by `MONGODB_DATABASE` in `.env`. Do not point eval scripts at the production database — `load_nvidia_documents.py --clear` deletes by filename and will destroy production data if misconfigured. (This already happened once during setup.)

## Notes for whoever picks this up

- Runtime is ~58 min/round and each round costs ~$0.35 in judge calls. A4.3 (caching simulations) is worth doing before many more iterations.
- Don't reuse single-turn metrics (`FaithfulnessMetric`, `AnswerRelevancyMetric`) here — this suite uses `ConversationalTestCase`, which requires multi-turn metrics.
- The existing single-turn suites (`test_rag_hallucination.py`, `test_rag_e2e.py`, `.dataset.json`) are unrelated to this work and were deliberately left untouched.
- All 36 tests in `tests/test_app.py` pass with the current changes.

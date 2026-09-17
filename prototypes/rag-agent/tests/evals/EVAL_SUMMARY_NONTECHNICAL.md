# What We Learned Testing the NVIDIA Document Assistant

*A plain-language summary. No technical background needed.*

---

## What we built and why

We have an AI assistant that answers questions about documents — in this test, three NVIDIA annual reports. You ask it something like *"How did revenue break down by business segment?"* and it finds the relevant pages and answers from them.

The problem: **how do you know if it's actually any good?** Spot-checking a few questions by hand doesn't scale, and it tends to flatter the product — you unconsciously ask the questions you know it handles well.

So we built an automated report card. It works in three steps:

1. **Generate realistic questions.** We fed the three annual reports to an AI that wrote 30 realistic investor conversations — not single questions, but multi-turn exchanges with natural follow-ups, the way a real analyst talks ("...and how does that compare to last year?").
2. **Run them against the real assistant.** Each conversation is played out against the actual product, start to finish.
3. **Grade the transcripts.** A separate AI acts as an examiner, scoring each conversation on four criteria.

The key thing we added this round: **we test whole conversations, not isolated questions.** The product remembers what you said earlier — that memory had never been tested before, and as you'll see, that's exactly where the problems live.

---

## The four things we grade

Think of these as four different examiners, each checking something different:

| What we call it | The question it asks |
|---|---|
| **Turn Relevancy** | Did it actually answer what was asked, rather than something adjacent? |
| **Role Adherence** | Did it stay in character as a document researcher — sticking to the documents rather than volunteering outside knowledge? |
| **Turn Faithfulness** | Is every claim actually supported by the documents, or is it making things up? |
| **Cross-Turn Consistency** | Does it contradict itself as the conversation goes on? |

That last one is custom-built for this product, and it's the one that matters most here. The first three examine each answer on its own. Only Cross-Turn Consistency reads the whole conversation and asks: *did this assistant stay coherent with itself?*

---

## The results

**77% → 83% of conversations now pass.** Across four rounds of fixes:

| Round | Score |
|---|---|
| 1 (baseline) | 63% |
| 2 | 77% |
| 3 | 73% |
| **4 (current)** | **83%** |

Broken down by what each examiner checks:

| Criterion | Score | Read this as |
|---|---|---|
| Turn Relevancy | 100% | Perfect. Always answers what was asked. |
| Turn Faithfulness | 100% | **Fixed.** No longer makes up facts. |
| Cross-Turn Consistency | 93% | **Much improved** (was 83%). |
| Role Adherence | 83% | The one that got *worse* — explained below. |

**What we fixed:**

- **Cut-off answers: 12% → 1%.** Answers no longer stop mid-sentence.
- **Losing track between questions.** The assistant now remembers what it *read*
  earlier, not just what it *said* — so it stops denying information it showed you
  a moment ago.
- **Refusing to do analysis.** It used to decline questions like *"which risk is
  most significant?"* or *"calculate the growth rate"* by saying the documents
  didn't cover it. It now does the work, showing the figures it used.

---

## One score went down, and it's worth understanding why

Role Adherence — "did it stay in its lane?" — fell from 97% to 83%. Two causes,
and only one is a real problem:

**1. A real bug (now fixed).** The assistant was inventing citations — plausible-looking
page numbers that didn't exist. Cause: when we gave it memory of earlier material, we
passed that text along *without* labelling where it came from. Told to always cite its
sources, it filled the gap by guessing. We now attach the source label to every excerpt.
Spot-checks show zero invented citations, but this needs one more full run to confirm.

**2. A measurement problem, not a product problem.** We told the examiner the assistant
should be grounded "*only* in retrieved document content." The examiner takes that
literally and marks the assistant down for doing arithmetic or ranking risks — which is
exactly the analytical behaviour we deliberately enabled, and what users actually want.

So the grader and the product now disagree about what "good" means. **That's a decision
to make, not a bug to fix:** either we widen the definition so the grader rewards useful
analysis, or we keep it strict and accept a lower ceiling. Worth deciding before chasing
the number further — otherwise we'd be optimising toward behaviour nobody wants.

## What happens next

One round remains of the five we planned. Two things to do, in order:

1. **Confirm the citation fix worked.** It's spot-checked but not yet proven across a
   full run. Cheap, and it should recover most of the Role Adherence drop.
2. **Decide what we're grading.** The grader currently penalises the assistant for
   doing analysis we deliberately enabled. Settle that definition before optimising
   further — chasing the score while the target is wrong just bakes in the wrong
   behaviour.

Separately, one workflow improvement is worth the investment: each round takes ~50
minutes, and because the test conversations are regenerated every time, small
score changes are partly random noise rather than real movement. Saving and reusing
the conversations would make rounds directly comparable and cut the wait
substantially.

---

## Three things worth knowing

**A bug we found that had nothing to do with the tests — and mattered more.** The
system had error-recovery code meant to retry when Google's AI service has a
momentary outage. Investigating a test failure revealed **it had never once worked**:
it was written to catch a different type of error than the one the service actually
produces. Every transient outage was reaching users as a raw failure, in production,
with no retry. Its unit tests passed throughout, because they tested the component in
isolation rather than as actually wired up. Now fixed and verified.

**One round went badly wrong, and that's normal.** Round 3 initially scored 7% — far
worse than the 77% baseline. Two of our own fixes had backfired. We caught it, traced
both causes, and fixed them. This is what the measurement system is *for*: without it,
that regression would have shipped silently.

**During setup, we damaged some data.** A configuration mistake caused the test scripts
to write to and delete from the live database instead of a separate test one. It was
caught and reported, the stray test data cleaned up, and testing now runs against a
fully isolated database. However, some pre-existing data in the live database was
deleted before the mistake was noticed and could not be recovered. Safeguards and
warnings are now documented so this can't recur.

---

## The bottom line

The assistant went from **63% to 83%**, and the two hardest-to-fix problems are gone:
it no longer fabricates facts (100%) and no longer cuts off mid-sentence (12% → 1%).
It now remembers what it read earlier, and will do real analysis on request instead of
deflecting.

What's left is mostly a question about our own yardstick rather than the product. And
the more valuable outcome may be the process itself: it caught a silent production bug,
and it caught our own bad fix before it shipped.

---

*Technical companion document: `EVAL_RESULTS.md` (same folder) — exact scores, root-cause analysis, file references, and reproduction steps.*

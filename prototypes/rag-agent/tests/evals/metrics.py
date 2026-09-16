"""DeepEval metrics for RAG evaluation.

Metrics focused on hallucination detection and answer quality.
Uses Gemini as the evaluation model.
"""

import os

from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualRelevancyMetric,
    ConversationalGEval,
    FaithfulnessMetric,
    RoleAdherenceMetric,
    TurnFaithfulnessMetric,
    TurnRelevancyMetric,
)
from deepeval.models import GeminiModel
from deepeval.test_case import MultiTurnParams

# Configure Gemini as the evaluation model
gemini_model = GeminiModel(
    model="gemini-2.5-flash",
    api_key=os.environ.get("GEMINI_API_KEY"),
)

# Primary RAG metrics for hallucination detection
RAG_METRICS = [
    # Key metric for hallucination: is the output grounded in the context?
    FaithfulnessMetric(
        threshold=0.7,
        model=gemini_model,
        include_reason=True,
    ),
    # Does the output actually answer the question?
    AnswerRelevancyMetric(
        threshold=0.7,
        model=gemini_model,
        include_reason=True,
    ),
    # Is the retrieved context relevant to the question?
    ContextualRelevancyMetric(
        threshold=0.7,
        model=gemini_model,
        include_reason=True,
    ),
]

# Faithfulness-only for focused hallucination testing
FAITHFULNESS_METRICS = [
    FaithfulnessMetric(
        threshold=0.7,
        model=gemini_model,
        include_reason=True,
    ),
]

# Multi-turn metrics for the NVIDIA-earnings chatbot conversation eval
# (ConversationalTestCase only - do not use single-turn LLMTestCase metrics here)
MULTI_TURN_NVIDIA_METRICS = [
    # Are individual assistant turns grounded in what was actually retrieved?
    # window_size is capped (default 10) because evaluating the full
    # conversation's accumulated retrieval context on every turn produced
    # ~24K-token judge calls that timed out in round 1 - only recent turns'
    # context matters for judging a given turn's faithfulness.
    TurnFaithfulnessMetric(
        threshold=0.7,
        model=gemini_model,
        include_reason=True,
        window_size=3,
    ),
    # Are assistant turns relevant to the user's turns?
    TurnRelevancyMetric(
        threshold=0.7,
        model=gemini_model,
        include_reason=True,
    ),
    # Does the assistant stay in its financial-research-assistant role?
    RoleAdherenceMetric(
        threshold=0.7,
        model=gemini_model,
        include_reason=True,
    ),
    # Domain-specific: does the assistant avoid contradicting or fabricating
    # figures across turns (e.g. restating a different revenue number for the
    # same fiscal year later in the conversation)?
    ConversationalGEval(
        name="CrossTurnConsistency",
        criteria=(
            "Determine whether the assistant's turns are internally consistent "
            "and grounded: later turns must not contradict figures, dates, or "
            "facts stated in earlier turns, and must not state numbers or "
            "claims that are absent from the retrieval context for that turn."
        ),
        evaluation_params=[
            MultiTurnParams.CONTENT,
            MultiTurnParams.RETRIEVAL_CONTEXT,
        ],
        model=gemini_model,
        threshold=0.7,
    ),
]

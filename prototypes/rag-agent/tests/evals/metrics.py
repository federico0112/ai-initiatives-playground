"""DeepEval metrics for RAG evaluation.

Metrics focused on hallucination detection and answer quality.
Uses Gemini as the evaluation model.
"""

import os

from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
)
from deepeval.models import GeminiModel

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

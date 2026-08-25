"""End-to-end RAG evaluation with real MongoDB retrieval.

Tests the complete RAG pipeline including:
- Query embedding
- Vector search in MongoDB
- Document retrieval
- Prompt building
- Response generation
- Hallucination evaluation
"""

import os
import sys
from pathlib import Path

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Register embedders and storages
from embedders import gemini  # noqa: F401
from storages import mongodb  # noqa: F401

from deepeval import assert_test
from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualRelevancyMetric,
    FaithfulnessMetric,
)
from deepeval.models import GeminiModel
from deepeval.test_case import LLMTestCase


# Skip all tests if environment not configured
pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY") or not os.environ.get("MONGODB_URI"),
    reason="GEMINI_API_KEY and MONGODB_URI required for E2E tests",
)

# Configure evaluation model
gemini_eval = GeminiModel(
    model="gemini-2.5-flash",
    api_key=os.environ.get("GEMINI_API_KEY"),
)

# E2E metrics - evaluates both retrieval and generation quality
E2E_METRICS = [
    FaithfulnessMetric(
        threshold=0.7,
        model=gemini_eval,
        include_reason=True,
    ),
    AnswerRelevancyMetric(
        threshold=0.7,
        model=gemini_eval,
        include_reason=True,
    ),
    ContextualRelevancyMetric(
        threshold=0.5,  # Lower threshold - retrieval may not be perfect
        model=gemini_eval,
        include_reason=True,
    ),
]


def run_full_rag_pipeline(query: str) -> tuple[str, list[str]]:
    """Run the complete RAG pipeline with real MongoDB.

    Args:
        query: The user question.

    Returns:
        Tuple of (generated_response, list of retrieved contexts).
    """
    from rag.pipeline import run_rag_query

    chunks = []
    retrieved_contexts = []

    for event in run_rag_query(query, model="gemini-2.5-flash", top_k=5):
        if event["type"] == "sources":
            # Extract source documents - we'll get the actual content separately
            pass
        elif event["type"] == "chunk":
            chunks.append(event["content"])
        elif event["type"] == "done":
            break

    response = "".join(chunks)

    # Get retrieved contexts by running retrieval separately
    from rag.pipeline import build_retrieval_pipeline

    pipeline = build_retrieval_pipeline(top_k=5)
    result = pipeline.run(
        {
            "query_embedder": {"query": query},
            "prompt_builder": {"query": query},
        },
        include_outputs_from={"retriever"},
    )

    documents = result.get("retriever", {}).get("documents", [])
    retrieved_contexts = [doc.content for doc in documents]

    return response, retrieved_contexts


# Test queries matching the llmware dataset (invoices, Microsoft, Biden)
# Run `python tests/evals/load_test_documents.py` first to load these docs
E2E_TEST_QUERIES = [
    "What is the total amount of the invoice?",
    "What was Microsoft's cloud revenue?",
    "When was Biden inaugurated?",
    "What are the payment terms?",
    "Who is the CEO of Microsoft?",
]


@pytest.mark.parametrize("query", E2E_TEST_QUERIES)
def test_rag_e2e_faithfulness(query: str):
    """Test E2E RAG pipeline for hallucination."""
    response, contexts = run_full_rag_pipeline(query)

    # Skip if no documents retrieved
    if not contexts:
        pytest.skip("No documents retrieved from MongoDB - upload documents first")

    test_case = LLMTestCase(
        input=query,
        actual_output=response,
        retrieval_context=contexts,
    )

    # Only test faithfulness for E2E - is the response grounded in retrieved docs?
    assert_test(
        test_case,
        [
            FaithfulnessMetric(
                threshold=0.7,
                model=gemini_eval,
                include_reason=True,
            )
        ],
    )


@pytest.mark.parametrize("query", E2E_TEST_QUERIES)
def test_rag_e2e_full(query: str):
    """Full E2E RAG evaluation with all metrics."""
    response, contexts = run_full_rag_pipeline(query)

    if not contexts:
        pytest.skip("No documents retrieved from MongoDB - upload documents first")

    test_case = LLMTestCase(
        input=query,
        actual_output=response,
        retrieval_context=contexts,
    )

    assert_test(test_case, E2E_METRICS)


class TestWithCustomQueries:
    """Test with custom queries matching your MongoDB content."""

    def test_custom_query(self):
        """Template for testing specific queries.

        Customize the query based on documents you've uploaded to MongoDB.
        """
        # Example: If you've uploaded invoices
        query = "What is the total amount?"

        response, contexts = run_full_rag_pipeline(query)

        if not contexts:
            pytest.skip("No documents retrieved")

        print(f"\nQuery: {query}")
        print(f"Retrieved {len(contexts)} documents")
        print(f"Response: {response[:200]}...")

        test_case = LLMTestCase(
            input=query,
            actual_output=response,
            retrieval_context=contexts,
        )

        assert_test(
            test_case,
            [
                FaithfulnessMetric(
                    threshold=0.7,
                    model=gemini_eval,
                    include_reason=True,
                )
            ],
        )

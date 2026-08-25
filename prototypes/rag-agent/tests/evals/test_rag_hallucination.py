"""DeepEval tests for RAG hallucination detection.

Tests the actual RAG pipeline code with mocked retrieval.
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from deepeval import assert_test
from deepeval.dataset import EvaluationDataset
from deepeval.test_case import LLMTestCase
from haystack import Document

from tests.evals.metrics import FAITHFULNESS_METRICS, RAG_METRICS

# Load dataset
dataset = EvaluationDataset()
dataset.add_goldens_from_json_file(
    file_path=str(Path(__file__).parent / ".dataset.json")
)


def run_rag_pipeline_with_context(query: str, context: str) -> tuple[str, list[str]]:
    """Run the actual RAG pipeline with mocked retrieval.

    Args:
        query: The user question.
        context: The context to inject as retrieved documents.

    Returns:
        Tuple of (generated_response, retrieval_contexts).
    """
    from rag.pipeline import run_rag_query

    # Create mock document from the golden's context
    mock_doc = Document(
        content=context,
        meta={"filename": "test_doc.pdf", "pages": [1], "score": 0.95},
    )

    # Mock the storage to return our context
    mock_storage = MagicMock()
    mock_storage.vector_search.return_value = [
        {
            "text": context,
            "document_id": "test-doc-001",
            "filename": "test_doc.pdf",
            "chunk_index": 0,
            "pages": [1],
            "score": 0.95,
            "model": "gemini",
        }
    ]

    # Mock the embedder
    mock_embedder = MagicMock()
    mock_embedder.embed_query.return_value = [0.1] * 768

    with patch("rag.components.get_storage", return_value=mock_storage), \
         patch("rag.components.get_embedder", return_value=mock_embedder):

        # Collect streaming response
        chunks = []
        retrieval_contexts = []

        for event in run_rag_query(query, model="gemini-2.5-flash"):
            if event["type"] == "sources":
                # The sources contain retrieved doc info
                pass
            elif event["type"] == "chunk":
                chunks.append(event["content"])
            elif event["type"] == "done":
                break

        response = "".join(chunks)
        retrieval_contexts = [context]  # The context we injected

        return response, retrieval_contexts


@pytest.mark.parametrize(
    "golden",
    dataset.goldens[:10],  # Start with 10 for faster iteration
    ids=lambda g: g.input[:50],
)
def test_rag_pipeline_hallucination(golden):
    """Test that the actual RAG pipeline doesn't hallucinate."""
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")

    context = golden.context[0] if golden.context else ""

    # Run the ACTUAL RAG pipeline with mocked retrieval
    actual_output, retrieval_contexts = run_rag_pipeline_with_context(
        golden.input, context
    )

    # Create test case
    test_case = LLMTestCase(
        input=golden.input,
        actual_output=actual_output,
        expected_output=golden.expected_output,
        retrieval_context=retrieval_contexts,
    )

    assert_test(test_case, FAITHFULNESS_METRICS)


@pytest.mark.parametrize(
    "golden",
    dataset.goldens[:10],
    ids=lambda g: g.input[:50],
)
def test_rag_pipeline_full_evaluation(golden):
    """Full RAG evaluation with all metrics."""
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")

    context = golden.context[0] if golden.context else ""

    actual_output, retrieval_contexts = run_rag_pipeline_with_context(
        golden.input, context
    )

    test_case = LLMTestCase(
        input=golden.input,
        actual_output=actual_output,
        expected_output=golden.expected_output,
        retrieval_context=retrieval_contexts,
    )

    assert_test(test_case, RAG_METRICS)

"""Multi-turn E2E eval for the RAG chatbot over NVIDIA earnings documents.

Unlike test_rag_hallucination.py/test_rag_e2e.py (single-turn, no chat
history), this suite simulates realistic multi-turn conversations from
dataset_nvidia.json against the real chat pipeline (rag.pipeline.run_rag_query
with chat_history), scored with multi-turn conversational metrics only.

Run `python tests/evals/load_nvidia_documents.py` first to load the NVIDIA
PDFs into MongoDB. This reads from the same dedicated MONGODB_DATABASE
(default "rag_agent_eval") that script writes to, not production - see its
module docstring.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MONGODB_DATABASE", "rag_agent_eval")

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Register embedders and storages
from embedders import gemini  # noqa: F401
from storages import mongodb  # noqa: F401

from deepeval import assert_test
from deepeval.dataset import EvaluationDataset
from deepeval.simulator import ConversationSimulator
from deepeval.test_case import Turn

from tests.evals.load_nvidia_documents import NVIDIA_FILENAMES
from tests.evals.metrics import MULTI_TURN_NVIDIA_METRICS

MAX_TURNS = 6

# Skip all tests if environment not configured
pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY") or not os.environ.get("MONGODB_URI"),
    reason="GEMINI_API_KEY and MONGODB_URI required for the NVIDIA chatbot E2E eval",
)


async def rag_chatbot_callback(input: str, turns: list[Turn]) -> Turn:
    """Run the real chat pipeline for one simulated turn, with full history.

    Mirrors the /api/v1/chat endpoint's use of run_rag_query with
    chat_history, but rebuilds history from the simulator's turns each call
    instead of app.py's session store.
    """
    from rag.pipeline import build_retrieval_pipeline, run_rag_query

    chat_history = [{"role": t.role, "content": t.content} for t in turns]

    chunks: list[str] = []
    for event in run_rag_query(
        input,
        chat_history=chat_history,
        top_k=5,
        filenames=NVIDIA_FILENAMES,
    ):
        if event["type"] == "chunk":
            chunks.append(event["content"])
        elif event["type"] == "done":
            break

    # Fetch retrieved chunk text separately (run_rag_query only yields
    # filename/page metadata via the "sources" event, not raw content),
    # matching the pattern used in test_rag_e2e.py's run_full_rag_pipeline.
    retrieval_pipeline = build_retrieval_pipeline(top_k=5, filenames=NVIDIA_FILENAMES)
    retrieval_result = retrieval_pipeline.run(
        {"query_embedder": {"query": input}, "prompt_builder": {"query": input}},
        include_outputs_from={"retriever"},
    )
    documents = retrieval_result.get("retriever", {}).get("documents", [])

    return Turn(
        role="assistant",
        content="".join(chunks),
        retrieval_context=[doc.content for doc in documents],
    )


CHATBOT_ROLE = (
    "A financial research assistant that answers questions about NVIDIA's "
    "annual report documents, grounded only in retrieved document content"
)

simulator = ConversationSimulator(model_callback=rag_chatbot_callback)
dataset = EvaluationDataset()
dataset.add_goldens_from_json_file(
    file_path=str(Path(__file__).parent / "dataset_nvidia.json")
)

simulated_test_cases = simulator.simulate(
    conversational_goldens=dataset.goldens,
    max_user_simulations=MAX_TURNS,
)
for _test_case in simulated_test_cases:
    # RoleAdherenceMetric reads chatbot_role off the test case; nothing sets
    # it by default, so it silently never ran in round 1.
    _test_case.chatbot_role = CHATBOT_ROLE


@pytest.mark.parametrize(
    "test_case",
    simulated_test_cases,
)
def test_rag_chatbot_conversation(test_case):
    """Simulated multi-turn conversation over the NVIDIA earnings chatbot."""
    assert_test(test_case=test_case, metrics=MULTI_TURN_NVIDIA_METRICS)

"""Generate multi-turn NVIDIA-earnings goldens with DeepEval's synthesizer.

Equivalent to:
    deepeval generate --method docs --variation multi-turn --documents tests/data ...

but calls the Synthesizer Python API directly because this deepeval version's
`generate` CLI only supports OpenAI/Azure/Ollama/local embedders for
--method docs context construction, and this project has no OPENAI_API_KEY.
This wires in the project's existing Gemini embedder (embedders.gemini) as a
DeepEvalBaseEmbeddingModel instead, so the whole generation path stays on
Gemini, matching the judge model already configured in metrics.py.

Usage:
    python tests/evals/generate_nvidia_dataset.py

Requires:
    GEMINI_API_KEY environment variable.
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import os

from deepeval.models import GeminiModel
from deepeval.models.base_model import DeepEvalBaseEmbeddingModel
from deepeval.synthesizer import Synthesizer
from deepeval.synthesizer.config import (
    ContextConstructionConfig,
    ConversationalStylingConfig,
)

from embedders import gemini  # noqa: F401 - registers the embedder
from embedders.base import get_embedder

DATA_DIR = project_root / "tests" / "data"


class GeminiEmbeddingModel(DeepEvalBaseEmbeddingModel):
    """Adapts the project's Gemini embedder to DeepEval's embedding interface."""

    def load_model(self):
        return get_embedder("gemini-embedding-2")

    def embed_text(self, text: str) -> list[float]:
        return self.model.embed_query(text)

    async def a_embed_text(self, text: str) -> list[float]:
        return self.embed_text(text)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.model.embed_batched(texts)

    async def a_embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self.embed_texts(texts)

    def get_model_name(self) -> str:
        return "gemini-embedding-2"


def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY not set")
        sys.exit(1)

    document_paths = [str(DATA_DIR / f) for f in os.listdir(DATA_DIR) if f.endswith(".pdf")]
    if not document_paths:
        print(f"Error: no PDFs found in {DATA_DIR}")
        sys.exit(1)

    print(f"Generating multi-turn goldens from: {document_paths}")

    gemini_model = GeminiModel(
        model="gemini-2.5-flash", api_key=os.environ["GEMINI_API_KEY"]
    )

    synthesizer = Synthesizer(
        model=gemini_model,
        conversational_styling_config=ConversationalStylingConfig(
            scenario_context=(
                "Investors and financial analysts researching NVIDIA's "
                "reported earnings, financial highlights, business segments, "
                "and risk factors from its annual reports"
            ),
            conversational_task=(
                "Help the user understand NVIDIA's reported financials and "
                "business performance, correctly carrying context across "
                "natural follow-up questions (e.g. year-over-year "
                "comparisons, drill-downs into a specific segment or metric "
                "mentioned earlier)"
            ),
            participant_roles="User (an investor/analyst) and AI financial research assistant",
            scenario_format=(
                "A realistic 2-4 turn conversation starting with a general "
                "question about NVIDIA's reported financial performance, "
                "followed by 1-3 natural follow-ups that depend on the "
                "assistant's prior answer"
            ),
            expected_outcome_format=(
                "The assistant gives accurate, source-grounded answers and "
                "stays consistent with figures/facts it stated earlier in "
                "the conversation"
            ),
        ),
    )

    context_config = ContextConstructionConfig(
        embedder=GeminiEmbeddingModel(),
        critic_model=gemini_model,
        max_contexts_per_document=5,
        chunk_size=1024,
    )

    goldens = synthesizer.generate_conversational_goldens_from_docs(
        document_paths=document_paths,
        include_expected_outcome=True,
        max_goldens_per_context=2,
        context_construction_config=context_config,
    )

    print(f"Generated {len(goldens)} conversational goldens")

    output_path = synthesizer.save_as(
        file_type="json",
        directory=str(Path(__file__).parent),
        file_name="dataset_nvidia",
    )
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()

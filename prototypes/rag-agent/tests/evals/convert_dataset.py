"""Convert llmware RAG dataset to DeepEval Golden format."""

import json
from pathlib import Path


def convert_to_deepeval_format(input_path: str, output_path: str, limit: int = 50):
    """Convert llmware JSONL to DeepEval JSON format.

    Args:
        input_path: Path to input JSONL file.
        output_path: Path to output JSON file.
        limit: Max number of goldens to include.
    """
    goldens = []

    with open(input_path) as f:
        for i, line in enumerate(f):
            if i >= limit:
                break

            item = json.loads(line)

            # Skip items with empty answers
            if not item.get("answer", "").strip():
                continue

            golden = {
                "input": item["query"],
                "expected_output": item["answer"],
                # context is a list of strings for DeepEval
                "context": [item["context"]],
            }
            goldens.append(golden)

    with open(output_path, "w") as f:
        json.dump(goldens, f, indent=2)

    print(f"Converted {len(goldens)} goldens to {output_path}")


if __name__ == "__main__":
    input_file = (
        "/Users/frueda/.cache/huggingface/hub/"
        "datasets--llmware--rag_instruct_test_dataset_0.1/"
        "snapshots/955dba764a07802e3d7a5beac9325255788507f1/"
        "rag_instruct_test_dataset_0.jsonl"
    )
    output_file = Path(__file__).parent / ".dataset.json"
    convert_to_deepeval_format(input_file, str(output_file), limit=50)

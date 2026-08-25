"""Load test documents into MongoDB for E2E evaluation.

This script loads the llmware RAG dataset contexts into MongoDB
so you can run full E2E tests against real retrieval.

Usage:
    python tests/evals/load_test_documents.py

Requires:
    MONGODB_URI and GEMINI_API_KEY environment variables.
"""

import json
import os
import sys
import uuid
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


def load_test_documents(dataset_path: str, limit: int = 20):
    """Load unique contexts from the dataset into MongoDB.

    Args:
        dataset_path: Path to the JSONL dataset.
        limit: Max number of unique documents to load.
    """
    # Import to register embedders and storages
    from embedders import gemini  # noqa: F401
    from storages import mongodb  # noqa: F401

    from embedders.base import get_embedder
    from storages.base import get_storage

    # Check environment
    if not os.environ.get("MONGODB_URI"):
        print("Error: MONGODB_URI not set")
        sys.exit(1)
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY not set")
        sys.exit(1)

    print(f"Loading test documents from: {dataset_path}")

    # Extract unique contexts
    seen_contexts = set()
    documents = []

    with open(dataset_path) as f:
        for line in f:
            item = json.loads(line)
            context = item.get("context", "").strip()

            # Skip empty or duplicate contexts
            if not context or context in seen_contexts:
                continue

            seen_contexts.add(context)
            documents.append(
                {
                    "text": context,
                    "filename": f"test_doc_{len(documents)}.txt",
                    "sample_number": item.get("sample_number", len(documents)),
                }
            )

            if len(documents) >= limit:
                break

    print(f"Found {len(documents)} unique documents")

    # Get embedder and storage
    embedder = get_embedder("gemini")
    storage = get_storage()

    # Embed and store each document
    for i, doc in enumerate(documents):
        print(f"Processing document {i + 1}/{len(documents)}: {doc['filename']}")

        # Generate embedding
        embedding = embedder.embed_query(doc["text"])

        # Store in MongoDB using store_embeddings
        doc_id = str(uuid.uuid4())
        storage.store_embeddings(
            document_id=doc_id,
            filename=doc["filename"],
            chunks=[doc["text"]],
            embeddings=[embedding],
            model="gemini",
            chunk_metadata=[{"pages": [1]}],
        )

    print(f"\nSuccessfully loaded {len(documents)} test documents into MongoDB")
    print("You can now run E2E tests with:")
    print("  deepeval test run tests/evals/test_rag_e2e.py")


def clear_test_documents():
    """Clear all test documents from MongoDB."""
    from storages import mongodb  # noqa: F401
    from storages.base import get_storage

    storage = get_storage()

    # Delete documents with test filenames
    result = storage.collection.delete_many({"filename": {"$regex": "^test_doc_"}})
    print(f"Deleted {result.deleted_count} test documents")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Load test documents into MongoDB")
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing test documents",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max documents to load (default: 20)",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="/Users/frueda/.cache/huggingface/hub/datasets--llmware--rag_instruct_test_dataset_0.1/snapshots/955dba764a07802e3d7a5beac9325255788507f1/rag_instruct_test_dataset_0.jsonl",
        help="Path to dataset JSONL file",
    )

    args = parser.parse_args()

    if args.clear:
        clear_test_documents()
    else:
        load_test_documents(args.dataset, args.limit)

"""Load the NVIDIA annual report PDFs into MongoDB via the app's real ingestion path.

Unlike load_test_documents.py (which stores each llmware "context" string as a
single fake one-chunk document), this script runs the PDFs through the same
Docling chunking + embedding + storage path that POST /api/v1/embed-from-gcs
uses in production, so eval retrieval exercises real chunk boundaries.

IMPORTANT: this writes to a dedicated MONGODB_DATABASE (default
"rag_agent_eval", see EVAL_DATABASE below), NOT the production database, so
eval runs never insert into or delete from the same collection the deployed
app uses. The database name is set via os.environ.setdefault before storages
are imported, so it can still be overridden by exporting MONGODB_DATABASE
yourself. Before first use, create an Atlas Vector Search index named
"vector_index" (or your MONGODB_INDEX_NAME) on that database's "documents"
collection - see README.md's "MongoDB Atlas Setup" section for the index
definition to reuse.

Usage:
    python tests/evals/load_nvidia_documents.py
    python tests/evals/load_nvidia_documents.py --clear
    python tests/evals/load_nvidia_documents.py --max-pages 40  # faster, partial run

Requires:
    MONGODB_URI and GEMINI_API_KEY environment variables.

Note: the source PDFs are large (~16-55MB each, hundreds of pages). A full run
processes all three documents through Docling and embeds every chunk with
Gemini, which can take several minutes and makes real API calls. Run it once
and reuse the loaded data across eval iterations rather than reloading per run.
"""

import os
import sys
import uuid
from pathlib import Path

EVAL_DATABASE = "rag_agent_eval"
os.environ.setdefault("MONGODB_DATABASE", EVAL_DATABASE)

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

DATA_DIR = project_root / "tests" / "data"
NVIDIA_FILENAMES = [
    "2026AnnualReportNvidia.pdf",
    "NVIDIA-2024-Report.pdf",
    "NVIDIA-2025-Annual-Report.pdf",
]


def load_nvidia_documents(max_pages: int | None = None) -> None:
    """Chunk, embed, and store the NVIDIA PDFs under tests/data/ into MongoDB.

    Args:
        max_pages: If set, only process the first N pages of each PDF
            (faster/cheaper partial run).
    """
    # Import to register embedders and storages
    from embedders import gemini  # noqa: F401
    from storages import mongodb  # noqa: F401

    from embedders.base import get_embedder
    from services.document import process_file, _get_converter
    from storages.base import get_storage

    if not os.environ.get("MONGODB_URI"):
        print("Error: MONGODB_URI not set")
        sys.exit(1)
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY not set")
        sys.exit(1)

    print(f"Using MongoDB database: {os.environ['MONGODB_DATABASE']}")
    embedder = get_embedder("gemini-embedding-2")
    storage = get_storage()

    for filename in NVIDIA_FILENAMES:
        pdf_path = DATA_DIR / filename
        if not pdf_path.exists():
            print(f"Skipping missing file: {pdf_path}")
            continue

        print(f"Processing {filename} ({pdf_path.stat().st_size / 1e6:.1f} MB)...")

        if max_pages:
            # Convert directly with a page range instead of process_file's
            # default full-document conversion.
            from docling.chunking import HybridChunker
            from services.document import DocumentChunk

            converter = _get_converter()
            result = converter.convert(str(pdf_path), page_range=(1, max_pages))
            chunker = HybridChunker()
            chunks = [
                DocumentChunk(content=chunker.contextualize(c), metadata={})
                for c in chunker.chunk(result.document)
            ]
        else:
            file_content = pdf_path.read_bytes()
            chunks = process_file(file_content, filename)

        print(f"  {len(chunks)} chunks created")
        if not chunks:
            continue

        texts = [c.content for c in chunks]
        embeddings = embedder.embed_batched(texts)
        chunk_metadata = [c.metadata for c in chunks]

        document_id = str(uuid.uuid4())
        stored = storage.store_embeddings(
            document_id=document_id,
            filename=filename,
            chunks=texts,
            embeddings=embeddings,
            model="gemini-embedding-2",
            chunk_metadata=chunk_metadata,
        )
        print(f"  Stored {stored} chunks (document_id={document_id})")

    print("\nDone. You can now run:")
    print("  deepeval test run tests/evals/test_rag_chatbot_e2e.py")


def clear_nvidia_documents() -> None:
    """Remove the NVIDIA eval documents from the eval MongoDB database."""
    from storages import mongodb  # noqa: F401
    from storages.base import get_storage

    print(f"Using MongoDB database: {os.environ['MONGODB_DATABASE']}")
    storage = get_storage()
    result = storage._collection.delete_many({"filename": {"$in": NVIDIA_FILENAMES}})
    print(f"Deleted {result.deleted_count} chunks")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Load NVIDIA annual report PDFs into MongoDB for eval"
    )
    parser.add_argument("--clear", action="store_true", help="Clear existing NVIDIA eval documents")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Only process the first N pages of each PDF (faster partial run)",
    )

    args = parser.parse_args()

    if args.clear:
        clear_nvidia_documents()
    else:
        load_nvidia_documents(max_pages=args.max_pages)

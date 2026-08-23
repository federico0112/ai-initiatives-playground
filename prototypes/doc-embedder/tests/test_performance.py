"""Performance tests for document processing."""

import time
from pathlib import Path

import pytest

from services.document import _get_converter, process_file

# Path to test file on desktop
PLAYHANDBOOK_PATH = Path.home() / "Desktop" / "players_handbook_2024_ocr.pdf"


@pytest.mark.skipif(
    not PLAYHANDBOOK_PATH.exists(),
    reason=f"Test file not found: {PLAYHANDBOOK_PATH}",
)
class TestDocumentPerformance:
    """Performance tests using players handbook PDF."""

    def test_converter_timing(self):
        """Measure execution time of document converter."""
        file_content = PLAYHANDBOOK_PATH.read_bytes()
        filename = PLAYHANDBOOK_PATH.name

        print(f"\n{'='*60}")
        print(f"File: {filename}")
        print(f"Size: {len(file_content) / 1024 / 1024:.2f} MB")
        print(f"{'='*60}")

        # Measure converter initialization (first call)
        start = time.perf_counter()
        converter = _get_converter()
        init_time = time.perf_counter() - start
        print(f"Converter init (first call): {init_time:.3f}s")

        # Measure converter retrieval (cached)
        start = time.perf_counter()
        converter = _get_converter()
        cache_time = time.perf_counter() - start
        print(f"Converter retrieval (cached): {cache_time:.6f}s")

        # Write to temp file and convert
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_content)
            tmp_path = Path(tmp.name)

        try:
            # Measure conversion time
            start = time.perf_counter()
            result = converter.convert(str(tmp_path))
            convert_time = time.perf_counter() - start
            print(f"Document conversion: {convert_time:.3f}s")

            document = result.document
            page_count = len(document.pages) if hasattr(document, "pages") else 0
            print(f"Pages processed: {page_count}")

            # Measure chunking time
            from docling.chunking import HybridChunker

            chunker = HybridChunker()

            start = time.perf_counter()
            chunks = list(chunker.chunk(document))
            chunk_time = time.perf_counter() - start
            print(f"Chunking: {chunk_time:.3f}s")
            print(f"Chunks created: {len(chunks)}")

            # Measure contextualization time
            start = time.perf_counter()
            for chunk in chunks:
                _ = chunker.contextualize(chunk)
            context_time = time.perf_counter() - start
            print(f"Contextualization: {context_time:.3f}s")

            # Total time
            total_time = convert_time + chunk_time + context_time
            print(f"{'='*60}")
            print(f"TOTAL (convert + chunk + contextualize): {total_time:.3f}s")
            print(f"{'='*60}")

        finally:
            tmp_path.unlink()

    def test_full_process_file_timing(self):
        """Measure execution time of full process_file function."""
        file_content = PLAYHANDBOOK_PATH.read_bytes()
        filename = PLAYHANDBOOK_PATH.name

        print(f"\n{'='*60}")
        print(f"Full process_file() timing")
        print(f"File: {filename}")
        print(f"Size: {len(file_content) / 1024 / 1024:.2f} MB")
        print(f"{'='*60}")

        start = time.perf_counter()
        chunks = process_file(file_content, filename)
        total_time = time.perf_counter() - start

        print(f"Total time: {total_time:.3f}s")
        print(f"Chunks created: {len(chunks)}")
        print(f"Avg time per chunk: {total_time / len(chunks) * 1000:.2f}ms")
        print(f"{'='*60}")

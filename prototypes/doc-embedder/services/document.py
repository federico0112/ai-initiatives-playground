"""Document processing service using Docling."""

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableStructureOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.chunking import HybridChunker

logger = logging.getLogger(__name__)

# Supported file extensions
SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx", ".pptx", ".html", ".md"}

# Cached converter instance for performance
_converter: DocumentConverter | None = None


def _get_converter() -> DocumentConverter:
    """Get or create a cached DocumentConverter with optimized settings.

    Uses pypdfium2 backend and disables OCR/formula/code enrichment for speed.
    """
    global _converter
    if _converter is not None:
        return _converter

    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        table_structure_options=TableStructureOptions(mode="fast"),
        do_code_enrichment=False,
        do_formula_enrichment=False,
        images_scale=1.0,
        generate_page_images=False,
        document_timeout=120,
    )

    _converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                backend=PyPdfiumDocumentBackend,
            )
        }
    )
    logger.info("DocumentConverter initialized with optimized PDF pipeline")
    return _converter


@dataclass
class DocumentChunk:
    """Represents a chunk of a document."""

    content: str
    metadata: dict


def process_file(file_content: bytes, filename: str) -> list[DocumentChunk]:
    """Process an uploaded file into chunked documents using Docling.

    Args:
        file_content: Raw bytes of the uploaded file.
        filename: Original filename (used to determine file type).

    Returns:
        List of DocumentChunk objects, one per chunk.
    """
    logger.info("Processing file: %s (%d bytes)", filename, len(file_content))

    extension = Path(filename).suffix.lower()
    logger.debug("Detected file extension: %s", extension)

    if extension not in SUPPORTED_EXTENSIONS:
        logger.error(
            "Unsupported file type: %s (supported: %s)",
            extension,
            SUPPORTED_EXTENSIONS,
        )
        raise ValueError(
            f"Unsupported file type: {extension}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    # Write to temp file for Docling
    logger.debug("Writing content to temporary file")
    with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as tmp:
        tmp.write(file_content)
        tmp_path = Path(tmp.name)
    logger.debug("Temporary file created: %s", tmp_path)

    try:
        # Convert file to Docling document
        logger.info("Converting file to document using Docling")
        converter = _get_converter()

        logger.debug("Running converter on: %s", tmp_path)
        result = converter.convert(str(tmp_path))
        document = result.document

        logger.info("Conversion complete: document parsed successfully")
        logger.debug(
            "Document has %d pages",
            len(document.pages) if hasattr(document, "pages") else 0,
        )

        # Chunk using HybridChunker (structure-aware)
        logger.info("Chunking document using HybridChunker")
        chunker = HybridChunker()

        chunks = []
        for chunk in chunker.chunk(document):
            # Get contextualized text (includes section hierarchy)
            text = chunker.contextualize(chunk)

            # Build metadata from chunk
            metadata = {}
            if hasattr(chunk, "meta") and chunk.meta:
                if hasattr(chunk.meta, "origin") and chunk.meta.origin:
                    metadata["doc_hash"] = getattr(
                        chunk.meta.origin, "binary_hash", None
                    )
                if hasattr(chunk.meta, "headings") and chunk.meta.headings:
                    metadata["headings"] = chunk.meta.headings

                # Extract page numbers from chunk's doc_items provenance
                if hasattr(chunk.meta, "doc_items") and chunk.meta.doc_items:
                    pages = set()
                    for item in chunk.meta.doc_items:
                        if hasattr(item, "prov") and item.prov:
                            for prov in item.prov:
                                if hasattr(prov, "page_no"):
                                    pages.add(prov.page_no)
                    if pages:
                        metadata["pages"] = sorted(pages)

            chunks.append(DocumentChunk(content=text, metadata=metadata))

        logger.info("Chunking complete: %d chunks created", len(chunks))
        for i, chunk in enumerate(chunks):
            logger.debug(
                "Chunk %d: %d characters",
                i,
                len(chunk.content) if chunk.content else 0,
            )

        return chunks

    finally:
        logger.debug("Cleaning up temporary file: %s", tmp_path)
        tmp_path.unlink()
        logger.debug("Temporary file removed")

"""Custom Haystack components for RAG pipeline."""

import logging
from typing import Any

from haystack import Document, component

from embedders.base import get_embedder
from storages.base import get_storage

logger = logging.getLogger(__name__)


@component
class QueryEmbedder:
    """Haystack component that embeds a query string."""

    def __init__(self, embedder_name: str = "gemini"):
        """Initialize the query embedder.

        Args:
            embedder_name: Name of the embedder to use.
        """
        self.embedder_name = embedder_name
        logger.info("QueryEmbedder initialized: embedder=%s", embedder_name)

    @component.output_types(embedding=list[float])
    def run(self, query: str) -> dict[str, list[float]]:
        """Embed a query string.

        Args:
            query: The query string to embed.

        Returns:
            Dict with 'embedding' key containing the embedding vector.
        """
        logger.info(
            "Embedding query: %r",
            query[:50] + "..." if len(query) > 50 else query,
        )

        embedder = get_embedder(self.embedder_name)
        embedding = embedder.embed_query(query)

        logger.debug("Query embedded: dimension=%d", len(embedding))

        return {"embedding": embedding}


@component
class MongoDBRetriever:
    """Haystack component that retrieves documents from MongoDB using vector search."""

    def __init__(
        self,
        top_k: int = 5,
        filenames: list[str] | None = None,
        model: str | None = None,
    ):
        """Initialize the retriever.

        Args:
            top_k: Default number of documents to retrieve.
            filenames: Optional list of filenames to filter by.
            model: Optional embedding model to filter by.
        """
        self.top_k = top_k
        self.filenames = filenames
        self.model = model
        logger.info(
            "MongoDBRetriever initialized: top_k=%d, filenames=%s, model=%s",
            top_k, filenames, model,
        )

    @component.output_types(documents=list[Document])
    def run(
        self,
        embedding: list[float],
        top_k: int | None = None,
    ) -> dict[str, list[Document]]:
        """Retrieve documents similar to the embedding.

        Args:
            embedding: The query embedding vector.
            top_k: Number of documents to retrieve (overrides default).

        Returns:
            Dict with 'documents' key containing list of Haystack Documents.
        """
        k = top_k if top_k is not None else self.top_k
        logger.info(
            "Retrieving documents: embedding_dim=%d, top_k=%d, filenames=%s, model=%s",
            len(embedding),
            k,
            self.filenames,
            self.model,
        )

        # Search storage
        storage = get_storage()
        results = storage.vector_search(
            query_embedding=embedding,
            limit=k,
            filenames=self.filenames,
            model=self.model,
        )
        logger.info("Retrieved %d documents", len(results))

        # Convert to Haystack Documents
        documents = []
        for result in results:
            doc = Document(
                content=result.get("text", ""),
                meta={
                    "document_id": result.get("document_id"),
                    "filename": result.get("filename"),
                    "chunk_index": result.get("chunk_index"),
                    "pages": result.get("pages"),
                    "score": result.get("score"),
                    "model": result.get("model"),
                },
            )
            documents.append(doc)
            logger.debug(
                "Document: filename=%s, score=%.4f",
                result.get("filename"),
                result.get("score", 0),
            )

        return {"documents": documents}


def format_sources(documents: list[Document]) -> list[dict[str, Any]]:
    """Format retrieved documents as source metadata.

    Args:
        documents: List of Haystack Documents.

    Returns:
        List of source metadata dicts.
    """
    sources = []
    seen = set()

    for doc in documents:
        filename = doc.meta.get("filename", "unknown")
        pages = doc.meta.get("pages", [])
        score = doc.meta.get("score", 0)

        # Deduplicate by filename
        if filename not in seen:
            sources.append({
                "filename": filename,
                "pages": pages,
                "score": round(score, 4) if score else None,
            })
            seen.add(filename)

    return sources

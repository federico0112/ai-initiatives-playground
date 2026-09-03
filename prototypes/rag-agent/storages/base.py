"""Base storage interface and registry."""

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class BaseStorage(ABC):
    """Abstract base class for vector storage backends."""

    @abstractmethod
    def store_embeddings(
        self,
        document_id: str,
        filename: str,
        chunks: list[str],
        embeddings: list[list[float]],
        model: str,
        chunk_metadata: list[dict] | None = None,
    ) -> int:
        """Store document chunks with their embeddings.

        Args:
            document_id: Unique identifier for the document.
            filename: Original filename.
            chunks: List of text chunks.
            embeddings: List of embedding vectors (one per chunk).
            model: Name of the embedding model used.
            chunk_metadata: Optional list of metadata dicts (one per chunk),
                containing fields like 'page', 'pages', 'headings'.

        Returns:
            Number of chunks stored.
        """
        pass

    @abstractmethod
    def vector_search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        filenames: list[str] | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar documents using vector similarity.

        Args:
            query_embedding: The query vector to search with.
            limit: Maximum number of results to return.
            filenames: Optional list of filenames to filter results.
            model: Optional embedding model name to filter results.

        Returns:
            List of matching documents with scores.
        """
        pass

    @abstractmethod
    def list_documents(self, model: str | None = None) -> list[dict[str, Any]]:
        """List all unique documents in storage.

        Args:
            model: Optional embedding model name to filter results.

        Returns:
            List of documents with filename, document_id, chunk_count, model, created_at.
        """
        pass

    @abstractmethod
    def list_unique_models(self) -> list[str]:
        """List all unique embedding models used in storage.

        Returns:
            List of unique model names.
        """
        pass

    @abstractmethod
    def delete_document(self, document_id: str) -> int:
        """Delete all chunks for a document.

        Args:
            document_id: The unique identifier of the document to delete.

        Returns:
            Number of chunks deleted.
        """
        pass

    def validate_config(self) -> None:
        """Validate storage configuration. Override for custom validation."""
        pass

    def health_check(self) -> dict:
        """Check storage health. Override for actual health checks."""
        return {"healthy": True}

    @property
    def capabilities(self) -> dict:
        """Return storage capabilities."""
        return {"max_batch_size": 1000}


# Registry of available storage backends
STORAGES: dict[str, type[BaseStorage]] = {}

# Singleton cache for storage instances
_storage_instances: dict[str, BaseStorage] = {}


def register_storage(name: str):
    """Decorator to register a storage backend class."""
    def decorator(cls: type[BaseStorage]):
        logger.info("Registering storage backend: %s -> %s", name, cls.__name__)
        STORAGES[name] = cls
        logger.debug("Registered storage backends: %s", list(STORAGES.keys()))
        return cls
    return decorator


def get_storage(name: str | None = None) -> BaseStorage:
    """Get a storage backend instance by name.

    Uses singleton caching for connection reuse.

    Args:
        name: Name of the storage backend. If None, reads from
              STORAGE_BACKEND env var (default: "mongodb").

    Returns:
        Storage backend instance.

    Raises:
        ValueError: If storage backend name is not registered.
    """
    if name is None:
        name = os.environ.get("STORAGE_BACKEND", "mongodb")

    logger.debug("Requesting storage backend: %s", name)

    if name not in STORAGES:
        available = ", ".join(STORAGES.keys())
        logger.error(
            "Unknown storage backend requested: %s (available: %s)",
            name,
            available,
        )
        raise ValueError(f"Unknown storage backend: {name}. Available: {available}")

    # Return cached instance if available
    if name in _storage_instances:
        logger.debug("Returning cached storage instance: %s", name)
        return _storage_instances[name]

    logger.info("Creating storage backend instance: %s", name)
    instance = STORAGES[name]()
    _storage_instances[name] = instance
    logger.debug("Storage backend created and cached: %s", name)
    return instance


def reset_storage():
    """Clear cached storage instances. Used for testing."""
    logger.debug("Resetting storage instances")
    _storage_instances.clear()

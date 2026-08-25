"""Base embedder interface and registry."""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseEmbedder(ABC):
    """Abstract base class for text embedders."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts into vectors (for documents).

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (list of floats).
        """
        pass

    def embed_query(self, query: str) -> list[float]:
        """Embed a query string for retrieval.

        Override this method if the embedder uses different task types
        for queries vs documents.

        Args:
            query: Query string to embed.

        Returns:
            Embedding vector for the query.
        """
        return self.embed([query])[0]

    def embed_batched(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, respecting max_batch_size.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (list of floats).
        """
        batch_size = self.max_batch_size
        if len(texts) <= batch_size:
            return self.embed(texts)

        logger.info(
            "Batching %d texts into %d batches of %d",
            len(texts),
            (len(texts) + batch_size - 1) // batch_size,
            batch_size,
        )

        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            logger.debug("Processing batch %d-%d", i, i + len(batch))
            embeddings = self.embed(batch)
            all_embeddings.extend(embeddings)

        return all_embeddings

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the dimension of the embedding vectors."""
        pass

    @property
    def max_batch_size(self) -> int:
        """Maximum batch size for embedding. Override if needed."""
        return 100

    def validate_config(self) -> None:
        """Validate embedder configuration. Override for custom validation."""
        pass

    def health_check(self) -> dict:
        """Check embedder health. Override for actual health checks."""
        return {"healthy": True}

    @property
    def capabilities(self) -> dict:
        """Return embedder capabilities."""
        return {
            "dimension": self.dimension,
            "max_batch_size": self.max_batch_size,
        }


# Registry of available embedders - add new embedders here
EMBEDDERS: dict[str, type[BaseEmbedder]] = {}


def register_embedder(name: str):
    """Decorator to register an embedder class."""
    def decorator(cls: type[BaseEmbedder]):
        logger.info("Registering embedder: %s -> %s", name, cls.__name__)
        EMBEDDERS[name] = cls
        logger.debug("Registered embedders: %s", list(EMBEDDERS.keys()))
        return cls
    return decorator


def get_embedder(name: str) -> BaseEmbedder:
    """Get an embedder instance by name.

    Args:
        name: Name of the embedder (e.g., "gemini").

    Returns:
        Instantiated embedder.

    Raises:
        ValueError: If embedder name is not registered.
    """
    logger.debug("Requesting embedder: %s", name)

    if name not in EMBEDDERS:
        available = ", ".join(EMBEDDERS.keys())
        logger.error(
            "Unknown embedder requested: %s (available: %s)",
            name,
            available,
        )
        raise ValueError(f"Unknown embedder: {name}. Available: {available}")

    logger.info("Creating embedder instance: %s", name)
    embedder = EMBEDDERS[name]()
    logger.debug(
        "Embedder created: %s (dimension=%d)",
        name,
        embedder.dimension,
    )
    return embedder

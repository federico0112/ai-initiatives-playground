"""Gemini embedder implementation."""

import logging
import os

from google import genai
from google.genai import types
from google.api_core import exceptions as google_exceptions

from .base import BaseEmbedder, register_embedder
from utils.exceptions import ErrorCode, ExternalAPIError
from utils.retry import with_retry

logger = logging.getLogger(__name__)

# Exceptions to retry on
RETRYABLE_EXCEPTIONS = (
    google_exceptions.ServiceUnavailable,
    google_exceptions.DeadlineExceeded,
    google_exceptions.ResourceExhausted,
)


class GeminiEmbedderBase(BaseEmbedder):
    """Base class for Gemini embedders with shared API logic."""

    MODEL_NAME: str  # Override in subclass
    DIMENSION: int = 768

    def __init__(self):
        logger.debug("Initializing %s", self.__class__.__name__)

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY environment variable not set")
            raise ValueError("GEMINI_API_KEY environment variable is required")

        logger.debug("Configuring Gemini API client")
        self.client = genai.Client(api_key=api_key)
        logger.info(
            "%s initialized: model=%s, dimension=%d",
            self.__class__.__name__,
            self.MODEL_NAME,
            self.DIMENSION,
        )

    @with_retry(retries=3, delay=1.0, backoff=2.0, exceptions=RETRYABLE_EXCEPTIONS)
    def _call_embed_api(
        self, texts: list[str], task_type: str
    ) -> list[list[float]]:
        """Call the Gemini embedding API with retry logic."""
        try:
            # Each text must be wrapped as a Content object for batch embedding
            contents = [
                types.Content(parts=[types.Part(text=t)]) for t in texts
            ]
            result = self.client.models.embed_content(
                model=self.MODEL_NAME,
                contents=contents,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=self.DIMENSION,
                ),
            )
            return [e.values for e in result.embeddings]
        except google_exceptions.ResourceExhausted as e:
            logger.warning("Rate limit exceeded: %s", str(e))
            raise ExternalAPIError(
                message="Gemini API rate limit exceeded",
                code=ErrorCode.API_RATE_LIMIT,
                details={"model": self.MODEL_NAME},
                retryable=True,
            ) from e
        except google_exceptions.Unauthenticated as e:
            logger.error("Authentication failed: %s", str(e))
            raise ExternalAPIError(
                message="Gemini API authentication failed",
                code=ErrorCode.API_AUTH_FAILURE,
                details={"model": self.MODEL_NAME},
                retryable=False,
            ) from e
        except google_exceptions.ServiceUnavailable as e:
            logger.warning("Service unavailable: %s", str(e))
            raise ExternalAPIError(
                message="Gemini API service unavailable",
                code=ErrorCode.API_UNAVAILABLE,
                details={"model": self.MODEL_NAME},
                retryable=True,
            ) from e
        except google_exceptions.GoogleAPICallError as e:
            logger.error("API call error: %s", str(e))
            raise ExternalAPIError(
                message=f"Gemini API error: {str(e)}",
                code=ErrorCode.API_ERROR,
                details={"model": self.MODEL_NAME},
                retryable=False,
            ) from e

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using Gemini embedding model."""
        logger.info("Embedding %d texts with %s", len(texts), self.MODEL_NAME)
        logger.debug(
            "Text lengths: %s",
            [len(t) for t in texts],
        )

        total_chars = sum(len(t) for t in texts)
        logger.debug("Total characters to embed: %d", total_chars)

        logger.debug(
            "Calling Gemini API: model=%s, task_type=RETRIEVAL_DOCUMENT",
            self.MODEL_NAME,
        )

        embeddings = self._call_embed_api(texts, "RETRIEVAL_DOCUMENT")

        logger.info(
            "Gemini embedding complete: %d vectors generated",
            len(embeddings),
        )
        logger.debug(
            "Embedding dimensions: %s",
            [len(e) for e in embeddings] if embeddings else [],
        )

        return embeddings

    def embed_query(self, query: str) -> list[float]:
        """Embed a query string for retrieval."""
        logger.info("Embedding query with %s", self.MODEL_NAME)
        logger.debug("Query length: %d", len(query))

        embeddings = self._call_embed_api([query], "RETRIEVAL_QUERY")
        embedding = embeddings[0]

        logger.info("Query embedding complete: dimension=%d", len(embedding))
        return embedding

    @property
    def dimension(self) -> int:
        return self.DIMENSION

    def health_check(self) -> dict:
        """Check Gemini API health by making a minimal API call."""
        try:
            self._call_embed_api(["health check"], "RETRIEVAL_DOCUMENT")
            return {
                "healthy": True,
                "model": self.MODEL_NAME,
            }
        except Exception as e:
            logger.warning("Gemini health check failed: %s", str(e))
            return {
                "healthy": False,
                "model": self.MODEL_NAME,
                "error": str(e),
            }


@register_embedder("gemini-embedding-2")
class GeminiEmbedding2(GeminiEmbedderBase):
    """Gemini Embedding 2 - multimodal embedding model (recommended)."""

    MODEL_NAME = "gemini-embedding-2"


@register_embedder("gemini-embedding-001")
class GeminiEmbedding001(GeminiEmbedderBase):
    """Gemini Embedding 001 - text-only embedding model."""

    MODEL_NAME = "gemini-embedding-001"

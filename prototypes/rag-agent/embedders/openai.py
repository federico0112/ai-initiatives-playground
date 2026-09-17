"""OpenAI embedder implementation."""

import logging
import os

import openai

from .base import BaseEmbedder, register_embedder
from utils.exceptions import ErrorCode, ExternalAPIError
from utils.retry import with_retry

logger = logging.getLogger(__name__)

# Exceptions to retry on
RETRYABLE_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)


class OpenAIEmbedderBase(BaseEmbedder):
    """Base class for OpenAI embedders with shared API logic."""

    MODEL_NAME: str  # Override in subclass
    DIMENSION: int = 1536

    def __init__(self):
        logger.debug("Initializing %s", self.__class__.__name__)

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.error("OPENAI_API_KEY environment variable not set")
            raise ValueError("OPENAI_API_KEY environment variable is required")

        logger.debug("Configuring OpenAI API client")
        self.client = openai.OpenAI(api_key=api_key)
        logger.info(
            "%s initialized: model=%s, dimension=%d",
            self.__class__.__name__,
            self.MODEL_NAME,
            self.DIMENSION,
        )

    @with_retry(retries=3, delay=1.0, backoff=2.0, exceptions=RETRYABLE_EXCEPTIONS)
    def _call_embed_api(self, texts: list[str]) -> list[list[float]]:
        """Call the OpenAI embeddings API with retry logic."""
        try:
            result = self.client.embeddings.create(
                model=self.MODEL_NAME,
                input=texts,
            )
            return [e.embedding for e in result.data]
        except openai.RateLimitError as e:
            logger.warning("Rate limit exceeded: %s", str(e))
            raise ExternalAPIError(
                message="OpenAI API rate limit exceeded",
                code=ErrorCode.API_RATE_LIMIT,
                details={"model": self.MODEL_NAME},
                retryable=True,
            ) from e
        except openai.AuthenticationError as e:
            logger.error("Authentication failed: %s", str(e))
            raise ExternalAPIError(
                message="OpenAI API authentication failed",
                code=ErrorCode.API_AUTH_FAILURE,
                details={"model": self.MODEL_NAME},
                retryable=False,
            ) from e
        except (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        ) as e:
            logger.warning("Service unavailable: %s", str(e))
            raise ExternalAPIError(
                message="OpenAI API service unavailable",
                code=ErrorCode.API_UNAVAILABLE,
                details={"model": self.MODEL_NAME},
                retryable=True,
            ) from e
        except openai.APIStatusError as e:
            logger.error("API call error: %s", str(e))
            raise ExternalAPIError(
                message=f"OpenAI API error: {str(e)}",
                code=ErrorCode.API_ERROR,
                details={"model": self.MODEL_NAME},
                retryable=False,
            ) from e

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts using an OpenAI embedding model."""
        logger.info("Embedding %d texts with %s", len(texts), self.MODEL_NAME)

        embeddings = self._call_embed_api(texts)

        logger.info(
            "OpenAI embedding complete: %d vectors generated",
            len(embeddings),
        )
        return embeddings

    @property
    def dimension(self) -> int:
        return self.DIMENSION

    def health_check(self) -> dict:
        """Check OpenAI API health by making a minimal API call."""
        try:
            self._call_embed_api(["health check"])
            return {
                "healthy": True,
                "model": self.MODEL_NAME,
            }
        except Exception as e:
            logger.warning("OpenAI health check failed: %s", str(e))
            return {
                "healthy": False,
                "model": self.MODEL_NAME,
                "error": str(e),
            }


@register_embedder("openai-text-embedding-3-small")
class OpenAITextEmbedding3Small(OpenAIEmbedderBase):
    """OpenAI text-embedding-3-small - efficient, low-cost embedding model."""

    MODEL_NAME = "text-embedding-3-small"
    DIMENSION = 1536


@register_embedder("openai-text-embedding-3-large")
class OpenAITextEmbedding3Large(OpenAIEmbedderBase):
    """OpenAI text-embedding-3-large - highest quality embedding model."""

    MODEL_NAME = "text-embedding-3-large"
    DIMENSION = 3072

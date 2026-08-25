"""RAG (Retrieval-Augmented Generation) module for document Q&A."""

from .components import QueryEmbedder, MongoDBRetriever, format_sources
from .pipeline import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    build_retrieval_pipeline,
    init_haystack_tracing,
    run_rag_query,
)
from .session import (
    Session,
    SessionMemory,
    get_session_memory,
    reset_session_memory,
)

__all__ = [
    "QueryEmbedder",
    "MongoDBRetriever",
    "format_sources",
    "DEFAULT_MODEL",
    "SUPPORTED_MODELS",
    "build_retrieval_pipeline",
    "init_haystack_tracing",
    "run_rag_query",
    "Session",
    "SessionMemory",
    "get_session_memory",
    "reset_session_memory",
]

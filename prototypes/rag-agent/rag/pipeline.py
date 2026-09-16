"""RAG pipeline construction using Haystack."""

import logging
import os
from typing import Any, Generator

from deepeval.tracing import observe, update_current_span, update_current_trace
from haystack import Pipeline, tracing
from haystack.components.builders import ChatPromptBuilder
from haystack.dataclasses import ChatMessage, StreamingChunk
from haystack.tracing.logging_tracer import LoggingTracer
from haystack.utils import Secret
from haystack_integrations.components.generators.google_genai import (
    GoogleGenAIChatGenerator,
)

from .components import QueryEmbedder, MongoDBRetriever, format_sources

logger = logging.getLogger(__name__)

# Configure Haystack logging
logging.getLogger("haystack").setLevel(logging.DEBUG)

# Flag to track if tracing has been initialized
_tracing_initialized = False


def init_haystack_tracing() -> None:
    """Initialize Haystack tracing for pipeline debugging.

    Call this once at application startup to enable detailed
    logging of pipeline inputs and outputs.
    """
    global _tracing_initialized
    if _tracing_initialized:
        return

    logger.info("Initializing Haystack tracing")

    # Enable content tracing to see actual data flowing through components
    tracing.tracer.is_content_tracing_enabled = True

    # Enable LoggingTracer for real-time debugging
    tracing.enable_tracing(
        LoggingTracer(
            tags_color_strings={
                "haystack.component.input": "\x1b[1;32m",  # Green for inputs
                "haystack.component.output": "\x1b[1;36m",  # Cyan for outputs
                "haystack.component.name": "\x1b[1;34m",  # Blue for component names
            },
        ),
    )

    _tracing_initialized = True
    logger.info("Haystack tracing enabled with content tracing")

DEFAULT_MODEL = "gemini-2.5-flash"
SUPPORTED_MODELS = {"gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.8-flash"}

SYSTEM_PROMPT = """You are a helpful assistant that answers questions based on the provided documents.
Use the document context to answer the user's question accurately and concisely.
If the documents don't contain relevant information, say so clearly.
Always cite specific documents when referencing information from them."""

RAG_TEMPLATE = """{% if documents %}
## Retrieved Documents

{% for doc in documents %}
### Document: {{ doc.meta.filename }}{% if doc.meta.pages %} (Pages: {{ doc.meta.pages | join(', ') }}){% endif %}

{{ doc.content }}

---
{% endfor %}
{% else %}
No relevant documents were found.
{% endif %}

## User Question
{{ query }}

Please answer based on the documents above."""


def build_retrieval_pipeline(
    top_k: int = 5,
    embedder_name: str = "gemini-embedding-2",
    filenames: list[str] | None = None,
) -> Pipeline:
    """Build a pipeline for document retrieval and prompt building.

    Pipeline architecture:
        query -> QueryEmbedder -> MongoDBRetriever -> ChatPromptBuilder

    Args:
        top_k: Number of documents to retrieve.
        embedder_name: Name of the embedder to use.
        filenames: Optional list of filenames to filter by.

    Returns:
        Configured Haystack Pipeline for retrieval.
    """
    logger.info(
        "Building retrieval pipeline: top_k=%d, embedder=%s, filenames=%s",
        top_k, embedder_name, filenames,
    )

    query_embedder = QueryEmbedder(embedder_name=embedder_name)
    retriever = MongoDBRetriever(top_k=top_k, filenames=filenames, model=embedder_name)

    prompt_builder = ChatPromptBuilder(
        template=[
            ChatMessage.from_system(SYSTEM_PROMPT),
            ChatMessage.from_user(RAG_TEMPLATE),
        ],
        required_variables=["query", "documents"],
    )

    pipeline = Pipeline()
    pipeline.add_component("query_embedder", query_embedder)
    pipeline.add_component("retriever", retriever)
    pipeline.add_component("prompt_builder", prompt_builder)

    # Connect: query_embedder.embedding -> retriever.embedding
    pipeline.connect("query_embedder.embedding", "retriever.embedding")
    # Connect: retriever.documents -> prompt_builder.documents
    pipeline.connect("retriever.documents", "prompt_builder.documents")

    logger.info("Retrieval pipeline built successfully")
    return pipeline

@observe(type="retriever")
def _run_retrieval(
    query: str,
    top_k: int,
    embedder_name: str,
    filenames: list[str] | None,
) -> dict[str, Any]:
    """Run the retrieval pipeline as its own traced span."""
    retrieval_pipeline = build_retrieval_pipeline(
        top_k=top_k,
        embedder_name=embedder_name,
        filenames=filenames,
    )

    retrieval_result = retrieval_pipeline.run(
        {
            "query_embedder": {"query": query},
            "prompt_builder": {"query": query},
        },
        include_outputs_from={"query_embedder", "retriever", "prompt_builder"},
    )

    documents = retrieval_result.get("retriever", {}).get("documents", [])
    update_current_span(
        input=query,
        output=[doc.content for doc in documents],
        metadata={
            "top_k": top_k,
            "embedder": embedder_name,
            "filenames": filenames,
            "retrieved_documents": len(documents),
        },
    )
    return retrieval_result


@observe(type="llm")
def _generate_chat_response(
    model: str, messages: list[ChatMessage]
) -> tuple[list[str], dict[str, Any]]:
    """Run the chat generator as its own traced span, returning streamed chunks."""
    api_key = os.environ.get("GEMINI_API_KEY")

    chunks_queue: list[str] = []

    def streaming_callback(chunk: StreamingChunk) -> None:
        if chunk.content:
            chunks_queue.append(chunk.content)

    generator = GoogleGenAIChatGenerator(
        api_key=Secret.from_token(api_key),
        model=model,
        generation_kwargs={"temperature": 0.7, "max_output_tokens": 2048},
        streaming_callback=streaming_callback,
    )

    result = generator.run(messages=messages)

    update_current_span(
        input=[
            {"role": msg.role.value if hasattr(msg.role, "value") else str(msg.role), "content": msg.text}
            for msg in messages
        ],
        output=[{"role": "assistant", "content": "".join(chunks_queue)}],
        metadata={"model": model},
    )
    return chunks_queue, result


@observe(type="agent")
def run_rag_query(
    query: str,
    chat_history: list[dict[str, str]] | None = None,
    model: str = DEFAULT_MODEL,
    top_k: int = 5,
    embedder: str = "gemini-embedding-2",
    filenames: list[str] | None = None,
) -> Generator[dict[str, Any], None, None]:
    """Run a RAG query using the pipeline and yield streaming responses.

    Args:
        query: The user's question.
        chat_history: Optional list of previous messages.
        model: The Gemini chat model to use.
        top_k: Number of documents to retrieve.
        embedder: The embedding model to use for query embedding.
        filenames: Optional list of filenames to filter documents.

    Yields:
        Dict events with 'type' and associated data.
    """
    # Initialize tracing on first call
    init_haystack_tracing()

    logger.info(
        "Running RAG query: query=%r, model=%s, top_k=%d, embedder=%s, filenames=%s, history_len=%d",
        query[:50] + "..." if len(query) > 50 else query,
        model,
        top_k,
        embedder,
        filenames,
        len(chat_history) if chat_history else 0,
    )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required")

    update_current_trace(
        input=query,
        tags=["rag", "chatbot"],
        metadata={
            "model": model,
            "top_k": top_k,
            "embedder": embedder,
            "filenames": filenames,
            "history_len": len(chat_history) if chat_history else 0,
        },
    )

    # Step 1: Run retrieval pipeline to get documents and build prompt
    logger.debug("Step 1: Building and running retrieval pipeline")
    retrieval_result = _run_retrieval(
        query=query,
        top_k=top_k,
        embedder_name=embedder,
        filenames=filenames,
    )

    # Log pipeline outputs
    logger.debug(
        "Retrieval pipeline output keys: %s",
        list(retrieval_result.keys()),
    )

    # Log embedding output
    embedding = retrieval_result.get("query_embedder", {}).get("embedding", [])
    logger.debug("Query embedding dimension: %d", len(embedding))

    # Extract documents from retriever output
    documents = retrieval_result.get("retriever", {}).get("documents", [])

    logger.info("Retrieved %d documents via pipeline", len(documents))
    for i, doc in enumerate(documents):
        logger.debug(
            "Document %d: filename=%s, score=%.4f, content_length=%d",
            i,
            doc.meta.get("filename", "unknown"),
            doc.meta.get("score", 0),
            len(doc.content),
        )

    # Yield sources
    sources = format_sources(documents)
    yield {"type": "sources", "documents": sources}

    # Step 2: Get the built prompt from pipeline result
    logger.debug("Step 2: Extracting built prompt from pipeline")
    built_messages = retrieval_result.get("prompt_builder", {}).get("prompt", [])
    logger.debug("Built messages count: %d", len(built_messages))
    for i, msg in enumerate(built_messages):
        role = msg.role if hasattr(msg, 'role') else 'unknown'
        content_len = len(msg.text) if hasattr(msg, 'text') else len(str(msg.content)) if hasattr(msg, 'content') else 0
        logger.debug("Built message %d: role=%s, content_length=%d", i, role, content_len)

    # Step 3: Prepend chat history if provided
    logger.debug("Step 3: Building final messages with chat history")
    final_messages = []
    if built_messages:
        # Add system message first
        final_messages.append(built_messages[0])

        # Add chat history after system message
        if chat_history:
            for msg in chat_history:
                if msg["role"] == "user":
                    final_messages.append(ChatMessage.from_user(msg["content"]))
                elif msg["role"] == "assistant":
                    final_messages.append(ChatMessage.from_assistant(msg["content"]))

        # Add the rest of the built messages (user message with context)
        final_messages.extend(built_messages[1:])
    else:
        # Fallback: build messages manually if pipeline didn't return them
        final_messages = [ChatMessage.from_system(SYSTEM_PROMPT)]
        if chat_history:
            for msg in chat_history:
                if msg["role"] == "user":
                    final_messages.append(ChatMessage.from_user(msg["content"]))
                elif msg["role"] == "assistant":
                    final_messages.append(ChatMessage.from_assistant(msg["content"]))
        final_messages.append(ChatMessage.from_user(f"Query: {query}"))

    logger.debug("Final messages count: %d", len(final_messages))
    for i, msg in enumerate(final_messages):
        role = msg.role if hasattr(msg, 'role') else 'unknown'
        content_len = len(msg.text) if hasattr(msg, 'text') else len(str(msg.content)) if hasattr(msg, 'content') else 0
        logger.debug("Final message %d: role=%s, content_length=%d", i, role, content_len)

    # Step 4: Run generator with streaming
    logger.debug("Step 4: Running generator with model=%s", model)
    chunks_queue, result = _generate_chat_response(model=model, messages=final_messages)

    logger.debug(
        "Generator output: %d chunks received, result keys=%s",
        len(chunks_queue),
        list(result.keys()),
    )

    # Yield chunks that were collected
    for chunk_content in chunks_queue:
        yield {"type": "chunk", "content": chunk_content}

    # If no streaming chunks, fall back to full response
    if not chunks_queue:
        replies = result.get("replies", [])
        if replies:
            response_text = replies[0].text if hasattr(replies[0], 'text') else str(replies[0].content)
            # Yield in chunks for consistency
            chunk_size = 50
            for i in range(0, len(response_text), chunk_size):
                yield {"type": "chunk", "content": response_text[i:i + chunk_size]}
        else:
            yield {"type": "chunk", "content": "I couldn't generate a response."}

    yield {"type": "done"}

    total_response_length = sum(len(c) for c in chunks_queue)
    logger.info(
        "RAG query completed: total_chunks=%d, response_length=%d",
        len(chunks_queue),
        total_response_length,
    )
    update_current_trace(output="".join(chunks_queue))

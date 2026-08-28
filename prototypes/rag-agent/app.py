"""RAG agent service prototype."""

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from dotenv import load_dotenv
load_dotenv()  # Load .env before other imports that need env vars

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from version import __version__, __git_sha__, get_version_info
from embedders.base import get_embedder, EMBEDDERS
from embedders import gemini  # noqa: F401 - registers the embedder
from services.document import process_file, SUPPORTED_EXTENSIONS
from storages.base import get_storage, STORAGES
from storages import mongodb  # noqa: F401 - registers the storage backend
from utils.exceptions import (
    RagAgentError,
    ValidationError,
    ExternalAPIError,
    StorageError,
    GCSError,
    ErrorCode,
)
from services.gcs import (
    generate_upload_url,
    download_file,
    delete_file,
    extract_filename,
)
from rag import (
    DEFAULT_MODEL,
    SUPPORTED_MODELS,
    run_rag_query,
    get_session_memory,
)

# Configure logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Validation constants
MAX_QUERY_LENGTH = 10000
MAX_MESSAGE_LENGTH = 10000
MAX_LIMIT = 100
MIN_LIMIT = 1
DEFAULT_TOP_K = 5
MAX_TOP_K = 20


# Pydantic models for request validation
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_LENGTH)
    limit: int = Field(default=5, ge=MIN_LIMIT, le=MAX_LIMIT)
    model: str = "gemini"


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)
    session_id: str | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    model: str = DEFAULT_MODEL


class SignedUrlRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream")


class EmbedFromGCSRequest(BaseModel):
    gcs_path: str = Field(..., min_length=1)
    model: str = "gemini"
    cleanup: bool = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    logger.info("Starting RAG Agent service")
    logger.info("Log level: %s", log_level)
    logger.info("Available models: %s", list(EMBEDDERS.keys()))
    logger.info("Available storage backends: %s", list(STORAGES.keys()))
    yield
    logger.info("Shutting down RAG Agent service")


app = FastAPI(
    title="RAG Agent API",
    version=__version__,
    lifespan=lifespan,
)


# Exception handlers
@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    """Handle validation errors (400)."""
    logger.warning("Validation error: %s (code=%s)", exc.message, exc.code.value)
    return JSONResponse(status_code=400, content=exc.to_dict())


@app.exception_handler(ExternalAPIError)
async def external_api_error_handler(request: Request, exc: ExternalAPIError):
    """Handle external API errors (429 for rate limit, 502 otherwise)."""
    logger.error("External API error: %s (code=%s)", exc.message, exc.code.value)
    if exc.code == ErrorCode.API_RATE_LIMIT:
        return JSONResponse(status_code=429, content=exc.to_dict())
    return JSONResponse(status_code=502, content=exc.to_dict())


@app.exception_handler(StorageError)
async def storage_error_handler(request: Request, exc: StorageError):
    """Handle storage errors (503)."""
    logger.error("Storage error: %s (code=%s)", exc.message, exc.code.value)
    return JSONResponse(status_code=503, content=exc.to_dict())


@app.exception_handler(GCSError)
async def gcs_error_handler(request: Request, exc: GCSError):
    """Handle GCS errors (404 for not found, 403 for access denied, 500 otherwise)."""
    logger.error("GCS error: %s (code=%s)", exc.message, exc.code.value)
    if exc.code == ErrorCode.GCS_FILE_NOT_FOUND:
        return JSONResponse(status_code=404, content=exc.to_dict())
    if exc.code == ErrorCode.GCS_ACCESS_DENIED:
        return JSONResponse(status_code=403, content=exc.to_dict())
    if exc.code == ErrorCode.GCS_INVALID_PATH:
        return JSONResponse(status_code=400, content=exc.to_dict())
    return JSONResponse(status_code=500, content=exc.to_dict())


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    """Handle unexpected errors (500)."""
    # Don't catch our custom exceptions that already have handlers
    if isinstance(exc, RagAgentError):
        raise exc

    logger.exception("Unexpected error: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "error": "An internal error occurred",
            "code": ErrorCode.INTERNAL_ERROR.value,
            "retryable": False,
        },
    )


# Middleware for request/response logging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log incoming request details."""
    logger.info("Request: %s %s", request.method, request.url.path)
    logger.debug("Request headers: %s", dict(request.headers))
    if request.headers.get("content-type"):
        logger.debug("Content-Type: %s", request.headers.get("content-type"))

    response = await call_next(request)

    logger.info(
        "Response: %s %s -> %s",
        request.method,
        request.url.path,
        response.status_code,
    )
    return response


# Root health check for Cloud Run
@app.get("/health")
async def health_root(details: bool = False):
    """Health check endpoint.

    Use ?details=true for component-level health status.
    """
    logger.debug("Health check requested")

    # Fast path for load balancer probes
    if not details:
        return {"status": "healthy"}

    # Detailed health check
    logger.info("Detailed health check requested")
    components = {}
    all_healthy = True

    # Check embedder health
    try:
        embedder = get_embedder("gemini")
        components["embedder"] = embedder.health_check()
        if not components["embedder"].get("healthy"):
            all_healthy = False
    except Exception as e:
        logger.warning("Embedder health check failed: %s", str(e))
        components["embedder"] = {"healthy": False, "error": str(e)}
        all_healthy = False

    # Check storage health
    try:
        storage = get_storage()
        components["storage"] = storage.health_check()
        if not components["storage"].get("healthy"):
            all_healthy = False
    except Exception as e:
        logger.warning("Storage health check failed: %s", str(e))
        components["storage"] = {"healthy": False, "error": str(e)}
        all_healthy = False

    status = "healthy" if all_healthy else "unhealthy"
    response = {
        "status": status,
        "components": components,
    }

    if all_healthy:
        return response
    return JSONResponse(status_code=503, content=response)


# Serve index.html at root
@app.get("/")
async def index():
    """Serve the main frontend page."""
    logger.debug("Serving index page")
    return FileResponse("static/index.html")


# API v1 routes
@app.get("/api/v1/health")
async def api_health(details: bool = False):
    """API health check endpoint."""
    return await health_root(details)


@app.get("/api/v1/version")
async def api_version():
    """Return version information."""
    version_info = get_version_info()
    logger.debug("Version info requested: %s", version_info)
    return version_info


@app.get("/api/v1/models")
async def list_models():
    """List available embedding models."""
    models = list(EMBEDDERS.keys())
    logger.debug("Available models requested: %s", models)
    return {"models": models}


@app.get("/api/v1/storages")
async def list_storages():
    """List available storage backends."""
    storages = list(STORAGES.keys())
    logger.debug("Available storage backends requested: %s", storages)
    return {"storages": storages}


@app.get("/api/v1/chat-models")
async def list_chat_models():
    """List available chat models for RAG."""
    models = list(SUPPORTED_MODELS)
    logger.debug("Available chat models requested: %s", models)
    return {"models": models}


@app.post("/api/v1/upload")
async def upload(
    file: UploadFile = File(...),
    model: str = Form(default="gemini"),
):
    """Upload and embed a document.

    Args:
        file: The document file (PDF, TXT, or DOCX)
        model: Embedding model to use, default "gemini"

    Returns:
        JSON with document_id and chunks_stored count.
    """
    logger.info("Upload request received")

    if not file.filename:
        logger.warning("Upload request has empty filename")
        raise ValidationError(
            message="No filename provided",
            code=ErrorCode.VALIDATION_MISSING_FILENAME,
        )

    logger.info(
        "Processing upload: filename=%s, model=%s",
        file.filename,
        model,
    )

    # Validate model
    if model not in EMBEDDERS:
        available = ", ".join(EMBEDDERS.keys())
        logger.warning(
            "Invalid model requested: %s (available: %s)",
            model,
            available,
        )
        raise ValidationError(
            message=f"Unknown model: {model}. Available: {available}",
            code=ErrorCode.VALIDATION_INVALID_MODEL,
            details={"available_models": list(EMBEDDERS.keys())},
        )

    # Process file into chunks
    logger.debug("Reading file content")
    file_content = await file.read()
    logger.info(
        "File read: %s (%d bytes)",
        file.filename,
        len(file_content),
    )

    try:
        logger.debug("Processing file into chunks")
        documents = process_file(file_content, file.filename)
    except ValueError as e:
        if "Unsupported file type" in str(e):
            raise ValidationError(
                message=str(e),
                code=ErrorCode.VALIDATION_UNSUPPORTED_FILE,
                details={"supported_extensions": list(SUPPORTED_EXTENSIONS)},
            ) from e
        raise

    if not documents:
        logger.warning("No content extracted from file: %s", file.filename)
        raise ValidationError(
            message="No content extracted from file",
            code=ErrorCode.VALIDATION_EMPTY_CONTENT,
        )

    # Extract text and metadata from documents
    chunks = [doc.content for doc in documents]
    chunk_metadata = [doc.metadata for doc in documents]
    logger.info(
        "File processed into %d chunks",
        len(chunks),
    )
    logger.debug(
        "Chunk sizes: %s",
        [len(c) for c in chunks],
    )

    # Generate embeddings
    logger.debug("Initializing embedder: %s", model)
    embedder = get_embedder(model)

    logger.info("Generating embeddings for %d chunks", len(chunks))
    embeddings = embedder.embed_batched(chunks)
    logger.info(
        "Embeddings generated: %d vectors of dimension %d",
        len(embeddings),
        len(embeddings[0]) if embeddings else 0,
    )

    # Store in storage backend
    document_id = str(uuid.uuid4())
    logger.info(
        "Storing document: id=%s, chunks=%d",
        document_id,
        len(chunks),
    )

    storage = get_storage()
    chunks_stored = storage.store_embeddings(
        document_id=document_id,
        filename=file.filename,
        chunks=chunks,
        embeddings=embeddings,
        model=model,
        chunk_metadata=chunk_metadata,
    )

    logger.info(
        "Upload complete: document_id=%s, chunks_stored=%d",
        document_id,
        chunks_stored,
    )

    return {
        "document_id": document_id,
        "chunks_stored": chunks_stored,
    }


@app.post("/api/v1/upload/signed-url")
async def get_signed_upload_url(request: SignedUrlRequest):
    """Get a signed URL for direct upload to GCS.

    Args:
        request: SignedUrlRequest with filename and content_type

    Returns:
        JSON with signed_url, gcs_path, and expires_in_minutes
    """
    logger.info("Signed URL request received for: %s", request.filename)

    # Validate file extension
    from pathlib import Path
    extension = Path(request.filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValidationError(
            message=f"Unsupported file type: {extension}",
            code=ErrorCode.VALIDATION_UNSUPPORTED_FILE,
            details={"supported_extensions": list(SUPPORTED_EXTENSIONS)},
        )

    result = generate_upload_url(request.filename, request.content_type)
    logger.info("Signed URL generated: %s", result["gcs_path"])

    return result


@app.post("/api/v1/embed-from-gcs")
async def embed_from_gcs(request: EmbedFromGCSRequest):
    """Embed a document that was uploaded to GCS.

    Args:
        request: EmbedFromGCSRequest with gcs_path, model, and cleanup flag

    Returns:
        JSON with document_id, chunks_stored, and source
    """
    logger.info("Embed from GCS request: %s", request.gcs_path)

    # Validate model
    if request.model not in EMBEDDERS:
        available = ", ".join(EMBEDDERS.keys())
        raise ValidationError(
            message=f"Unknown model: {request.model}. Available: {available}",
            code=ErrorCode.VALIDATION_INVALID_MODEL,
            details={"available_models": list(EMBEDDERS.keys())},
        )

    # Download file from GCS
    file_content = download_file(request.gcs_path)
    filename = extract_filename(request.gcs_path)

    logger.info("Downloaded from GCS: %s (%d bytes)", filename, len(file_content))

    # Process file into chunks (reuse existing logic)
    try:
        documents = process_file(file_content, filename)
    except ValueError as e:
        if "Unsupported file type" in str(e):
            raise ValidationError(
                message=str(e),
                code=ErrorCode.VALIDATION_UNSUPPORTED_FILE,
                details={"supported_extensions": list(SUPPORTED_EXTENSIONS)},
            ) from e
        raise

    if not documents:
        raise ValidationError(
            message="No content extracted from file",
            code=ErrorCode.VALIDATION_EMPTY_CONTENT,
        )

    # Extract text and metadata
    chunks = [doc.content for doc in documents]
    chunk_metadata = [doc.metadata for doc in documents]
    logger.info("File processed into %d chunks", len(chunks))

    # Generate embeddings
    embedder = get_embedder(request.model)
    embeddings = embedder.embed_batched(chunks)
    logger.info(
        "Embeddings generated: %d vectors of dimension %d",
        len(embeddings),
        len(embeddings[0]) if embeddings else 0,
    )

    # Store in storage backend
    document_id = str(uuid.uuid4())
    storage = get_storage()
    chunks_stored = storage.store_embeddings(
        document_id=document_id,
        filename=filename,
        chunks=chunks,
        embeddings=embeddings,
        model=request.model,
        chunk_metadata=chunk_metadata,
    )

    logger.info(
        "Embed from GCS complete: document_id=%s, chunks_stored=%d",
        document_id,
        chunks_stored,
    )

    # Cleanup GCS file if requested
    if request.cleanup:
        delete_file(request.gcs_path)

    return {
        "document_id": document_id,
        "chunks_stored": chunks_stored,
        "source": "gcs",
    }


@app.post("/api/v1/search")
async def search(request: SearchRequest):
    """Search for similar documents.

    Args:
        request: SearchRequest with query, limit, and model

    Returns:
        JSON with list of matching document chunks and scores.
    """
    logger.info("Search request received")

    query = request.query
    limit = request.limit
    model_name = request.model

    logger.info(
        "Processing search: query=%r, limit=%d, model=%s",
        query[:50] + "..." if len(query) > 50 else query,
        limit,
        model_name,
    )

    # Validate model
    if model_name not in EMBEDDERS:
        available = ", ".join(EMBEDDERS.keys())
        logger.warning(
            "Invalid model requested: %s (available: %s)",
            model_name,
            available,
        )
        raise ValidationError(
            message=f"Unknown model: {model_name}. Available: {available}",
            code=ErrorCode.VALIDATION_INVALID_MODEL,
            details={"available_models": list(EMBEDDERS.keys())},
        )

    # Embed the query
    logger.debug("Initializing embedder: %s", model_name)
    embedder = get_embedder(model_name)

    logger.info("Generating query embedding")
    query_embedding = embedder.embed_query(query)
    logger.debug("Query embedding dimension: %d", len(query_embedding))

    # Search storage backend
    logger.info("Executing vector search")
    storage = get_storage()
    results = storage.vector_search(
        query_embedding=query_embedding,
        limit=limit,
    )

    logger.info("Search complete: %d results", len(results))

    return {
        "query": query,
        "results": results,
    }


@app.post("/api/v1/chat")
async def chat(request: ChatRequest):
    """RAG-based document Q&A with streaming response.

    Args:
        request: ChatRequest with message, session_id, top_k, and model

    Returns:
        NDJSON streaming response with events:
        - {"type": "metadata", "session_id": "...", "model": "..."}
        - {"type": "sources", "documents": [...]}
        - {"type": "chunk", "content": "..."}
        - {"type": "done"}
    """
    logger.info("Chat request received")

    message = request.message
    session_id = request.session_id
    top_k = request.top_k
    model = request.model

    # Validate model
    if model not in SUPPORTED_MODELS:
        available = ", ".join(SUPPORTED_MODELS)
        logger.warning(
            "Invalid model requested: %s (available: %s)",
            model,
            available,
        )
        raise ValidationError(
            message=f"Unknown model: {model}. Available: {available}",
            code=ErrorCode.VALIDATION_INVALID_MODEL,
            details={"available_models": list(SUPPORTED_MODELS)},
        )

    logger.info(
        "Processing chat: message=%r, session_id=%s, top_k=%d, model=%s",
        message[:50] + "..." if len(message) > 50 else message,
        session_id,
        top_k,
        model,
    )

    # Get or create session
    session_memory = get_session_memory()
    session = session_memory.get_or_create(session_id)
    actual_session_id = session.session_id

    # Get chat history
    chat_history = session.get_history()

    async def generate() -> AsyncGenerator[str, None]:
        """Generator for streaming NDJSON response."""
        # Emit metadata first
        yield json.dumps({
            "type": "metadata",
            "session_id": actual_session_id,
            "model": model,
        }) + "\n"

        full_response = []

        try:
            # Run RAG query and stream results
            for event in run_rag_query(
                query=message,
                chat_history=chat_history,
                model=model,
                top_k=top_k,
            ):
                yield json.dumps(event) + "\n"

                # Collect response chunks
                if event.get("type") == "chunk":
                    full_response.append(event.get("content", ""))

        except Exception as e:
            logger.exception("Error during RAG query: %s", str(e))
            yield json.dumps({
                "type": "error",
                "error": str(e),
            }) + "\n"
            return

        # Store messages in session
        session_memory.add_message(actual_session_id, "user", message)
        if full_response:
            session_memory.add_message(
                actual_session_id,
                "assistant",
                "".join(full_response),
            )

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# Legacy routes for backwards compatibility (redirect to /api/v1/)
@app.get("/version")
async def version_legacy():
    """Legacy version endpoint."""
    return await api_version()


@app.get("/models")
async def models_legacy():
    """Legacy models endpoint."""
    return await list_models()


@app.get("/storages")
async def storages_legacy():
    """Legacy storages endpoint."""
    return await list_storages()


@app.post("/upload")
async def upload_legacy(
    file: UploadFile = File(...),
    model: str = Form(default="gemini"),
):
    """Legacy upload endpoint."""
    return await upload(file=file, model=model)


@app.post("/search")
async def search_legacy(request: SearchRequest):
    """Legacy search endpoint."""
    return await search(request)


@app.post("/chat")
async def chat_legacy(request: ChatRequest):
    """Legacy chat endpoint."""
    return await chat(request)


# Mount static files AFTER routes (so routes take precedence)
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# Entrypoint for direct execution
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting RAG Agent service on port 8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)

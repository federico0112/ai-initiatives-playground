"""Document embedding service prototype."""

import logging
import os
import uuid

from flask import Flask, request, jsonify

from version import get_version_info
from embedders.base import get_embedder, EMBEDDERS
from embedders import gemini  # noqa: F401 - registers the embedder
from services.document import process_file, SUPPORTED_EXTENSIONS
from storages.base import get_storage, STORAGES
from storages import mongodb  # noqa: F401 - registers the storage backend
from utils.exceptions import (
    DocEmbedderError,
    ValidationError,
    ExternalAPIError,
    StorageError,
    ErrorCode,
)

# Configure logging
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Validation constants
MAX_QUERY_LENGTH = 10000
MAX_LIMIT = 100
MIN_LIMIT = 1


# Error handlers
@app.errorhandler(ValidationError)
def handle_validation_error(e: ValidationError):
    """Handle validation errors (400)."""
    logger.warning("Validation error: %s (code=%s)", e.message, e.code.value)
    return jsonify(e.to_dict()), 400


@app.errorhandler(ExternalAPIError)
def handle_external_api_error(e: ExternalAPIError):
    """Handle external API errors (429 for rate limit, 502 otherwise)."""
    logger.error("External API error: %s (code=%s)", e.message, e.code.value)
    if e.code == ErrorCode.API_RATE_LIMIT:
        return jsonify(e.to_dict()), 429
    return jsonify(e.to_dict()), 502


@app.errorhandler(StorageError)
def handle_storage_error(e: StorageError):
    """Handle storage errors (503)."""
    logger.error("Storage error: %s (code=%s)", e.message, e.code.value)
    return jsonify(e.to_dict()), 503


@app.errorhandler(Exception)
def handle_generic_error(e: Exception):
    """Handle unexpected errors (500)."""
    # Don't catch our custom exceptions that already have handlers
    if isinstance(e, DocEmbedderError):
        raise e

    logger.exception("Unexpected error: %s", str(e))
    return jsonify({
        "error": "An internal error occurred",
        "code": ErrorCode.INTERNAL_ERROR.value,
        "retryable": False,
    }), 500


@app.before_request
def log_request_info():
    """Log incoming request details."""
    logger.info(
        "Request: %s %s",
        request.method,
        request.path,
    )
    logger.debug(
        "Request headers: %s",
        dict(request.headers),
    )
    if request.content_type:
        logger.debug("Content-Type: %s", request.content_type)


@app.after_request
def log_response_info(response):
    """Log outgoing response details."""
    logger.info(
        "Response: %s %s -> %s",
        request.method,
        request.path,
        response.status_code,
    )
    logger.debug("Response headers: %s", dict(response.headers))
    return response


@app.route("/")
def index():
    """Return a simple HTML page describing the service."""
    logger.debug("Serving index page")
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Document Embedder</title>
    </head>
    <body>
        <h1>Document Embedder</h1>
        <p>Upload documents to embed and store in MongoDB Atlas.</p>
        <h2>Endpoints</h2>
        <ul>
            <li>POST /upload - Upload and embed a document</li>
            <li>POST /search - Search for similar documents</li>
            <li>GET /models - List available embedding models</li>
            <li>GET /storages - List available storage backends</li>
            <li>GET /health - Health check (add ?details=true for component status)</li>
            <li>GET /version - Version info</li>
        </ul>
    </body>
    </html>
    """


@app.route("/health")
def health():
    """Health check endpoint.

    Use ?details=true for component-level health status.
    """
    logger.debug("Health check requested")

    # Fast path for load balancer probes
    if request.args.get("details") != "true":
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

    status_code = 200 if all_healthy else 503
    return jsonify(response), status_code


@app.route("/version")
def version():
    """Return version information."""
    version_info = get_version_info()
    logger.debug("Version info requested: %s", version_info)
    return version_info


@app.route("/upload", methods=["POST"])
def upload():
    """Upload and embed a document.

    Expects multipart/form-data with:
    - file: The document file (PDF, TXT, or DOCX)
    - model: (optional) Embedding model to use, default "gemini"

    Returns:
        JSON with document_id and chunks_stored count.
    """
    logger.info("Upload request received")

    if "file" not in request.files:
        logger.warning("Upload request missing file")
        raise ValidationError(
            message="No file provided",
            code=ErrorCode.VALIDATION_MISSING_FILE,
        )

    file = request.files["file"]
    if not file.filename:
        logger.warning("Upload request has empty filename")
        raise ValidationError(
            message="No filename provided",
            code=ErrorCode.VALIDATION_MISSING_FILENAME,
        )

    model_name = request.form.get("model", "gemini")
    logger.info(
        "Processing upload: filename=%s, model=%s",
        file.filename,
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

    # Process file into chunks
    logger.debug("Reading file content")
    file_content = file.read()
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
    logger.debug("Initializing embedder: %s", model_name)
    embedder = get_embedder(model_name)

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
        model=model_name,
        chunk_metadata=chunk_metadata,
    )

    logger.info(
        "Upload complete: document_id=%s, chunks_stored=%d",
        document_id,
        chunks_stored,
    )

    return jsonify({
        "document_id": document_id,
        "chunks_stored": chunks_stored,
    })


@app.route("/search", methods=["POST"])
def search():
    """Search for similar documents.

    Expects JSON with:
    - query: The search query string
    - limit: (optional) Maximum results to return, default 5 (range: 1-100)
    - model: (optional) Embedding model to use, default "gemini"

    Returns:
        JSON with list of matching document chunks and scores.
    """
    logger.info("Search request received")

    data = request.get_json()
    if not data or "query" not in data:
        logger.warning("Search request missing query")
        raise ValidationError(
            message="No query provided",
            code=ErrorCode.VALIDATION_INVALID_QUERY,
        )

    query = data["query"]

    # Validate query
    if not query or not query.strip():
        logger.warning("Search request has empty query")
        raise ValidationError(
            message="Query cannot be empty",
            code=ErrorCode.VALIDATION_INVALID_QUERY,
        )

    if len(query) > MAX_QUERY_LENGTH:
        logger.warning("Query too long: %d characters (max %d)", len(query), MAX_QUERY_LENGTH)
        raise ValidationError(
            message=f"Query exceeds maximum length of {MAX_QUERY_LENGTH} characters",
            code=ErrorCode.VALIDATION_QUERY_TOO_LONG,
            details={"max_length": MAX_QUERY_LENGTH, "actual_length": len(query)},
        )

    limit = data.get("limit", 5)

    # Validate limit
    if not isinstance(limit, int) or limit < MIN_LIMIT or limit > MAX_LIMIT:
        logger.warning("Invalid limit: %s (valid range: %d-%d)", limit, MIN_LIMIT, MAX_LIMIT)
        raise ValidationError(
            message=f"Limit must be an integer between {MIN_LIMIT} and {MAX_LIMIT}",
            code=ErrorCode.VALIDATION_INVALID_LIMIT,
            details={"min": MIN_LIMIT, "max": MAX_LIMIT},
        )

    model_name = data.get("model", "gemini")

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

    return jsonify({
        "query": query,
        "results": results,
    })


@app.route("/models", methods=["GET"])
def list_models():
    """List available embedding models."""
    models = list(EMBEDDERS.keys())
    logger.debug("Available models requested: %s", models)
    return jsonify({"models": models})


@app.route("/storages", methods=["GET"])
def list_storages():
    """List available storage backends."""
    storages = list(STORAGES.keys())
    logger.debug("Available storage backends requested: %s", storages)
    return jsonify({"storages": storages})


if __name__ == "__main__":
    logger.info("Starting Document Embedder service on port 8080")
    logger.info("Log level: %s", log_level)
    logger.info("Available models: %s", list(EMBEDDERS.keys()))
    logger.info("Available storage backends: %s", list(STORAGES.keys()))
    app.run(host="0.0.0.0", port=8080)

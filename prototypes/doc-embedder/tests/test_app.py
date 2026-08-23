"""Tests for doc-embedder prototype."""

import io
import pytest
from unittest.mock import patch, MagicMock

from app import app
from version import __version__, get_version_info
from utils.exceptions import ErrorCode, ExternalAPIError, StorageError


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Document Embedder" in response.data


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "healthy"}


@patch("app.get_embedder")
@patch("app.get_storage")
def test_health_details_all_healthy(mock_get_storage, mock_get_embedder, client):
    """Test detailed health check when all components are healthy."""
    mock_embedder = MagicMock()
    mock_embedder.health_check.return_value = {"healthy": True, "model": "gemini"}
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.health_check.return_value = {"healthy": True, "database": "test_db"}
    mock_get_storage.return_value = mock_storage

    response = client.get("/health?details=true")

    assert response.status_code == 200
    assert response.json["status"] == "healthy"
    assert response.json["components"]["embedder"]["healthy"] is True
    assert response.json["components"]["storage"]["healthy"] is True


@patch("app.get_embedder")
@patch("app.get_storage")
def test_health_details_embedder_unhealthy(mock_get_storage, mock_get_embedder, client):
    """Test detailed health check when embedder is unhealthy."""
    mock_embedder = MagicMock()
    mock_embedder.health_check.return_value = {"healthy": False, "error": "API key invalid"}
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.health_check.return_value = {"healthy": True, "database": "test_db"}
    mock_get_storage.return_value = mock_storage

    response = client.get("/health?details=true")

    assert response.status_code == 503
    assert response.json["status"] == "unhealthy"
    assert response.json["components"]["embedder"]["healthy"] is False


@patch("app.get_embedder")
@patch("app.get_storage")
def test_health_details_storage_unhealthy(mock_get_storage, mock_get_embedder, client):
    """Test detailed health check when storage is unhealthy."""
    mock_embedder = MagicMock()
    mock_embedder.health_check.return_value = {"healthy": True, "model": "gemini"}
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.health_check.return_value = {"healthy": False, "error": "Connection timeout"}
    mock_get_storage.return_value = mock_storage

    response = client.get("/health?details=true")

    assert response.status_code == 503
    assert response.json["status"] == "unhealthy"
    assert response.json["components"]["storage"]["healthy"] is False


def test_version_endpoint(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert "version" in response.json
    assert "git_sha" in response.json


def test_version_format():
    version = __version__
    parts = version.split(".")
    assert len(parts) == 3, "Version should be in X.Y.Z format"
    for part in parts:
        assert part.isdigit(), f"Version part '{part}' should be numeric"


def test_get_version_info():
    info = get_version_info()
    assert "version" in info
    assert "git_sha" in info
    assert info["version"] == __version__


def test_models_endpoint(client):
    response = client.get("/models")
    assert response.status_code == 200
    assert "models" in response.json
    assert "gemini" in response.json["models"]


def test_storages_endpoint(client):
    response = client.get("/storages")
    assert response.status_code == 200
    assert "storages" in response.json
    assert "mongodb" in response.json["storages"]


def test_upload_no_file(client):
    """Test upload endpoint with missing file returns VALIDATION_MISSING_FILE."""
    response = client.post("/upload")
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_MISSING_FILE"
    assert "error" in response.json


def test_upload_empty_filename(client):
    """Test upload with empty filename returns VALIDATION_MISSING_FILENAME."""
    data = {
        "file": (io.BytesIO(b"test content"), ""),
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_MISSING_FILENAME"


def test_upload_invalid_model(client):
    """Test upload with invalid model returns VALIDATION_INVALID_MODEL."""
    data = {
        "file": (io.BytesIO(b"test content"), "test.txt"),
        "model": "invalid-model",
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_INVALID_MODEL"
    assert "available_models" in response.json.get("details", {})


@patch("app.process_file")
@patch("app.get_embedder")
@patch("app.get_storage")
def test_upload_success(mock_get_storage, mock_get_embedder, mock_process, client):
    # Setup mocks
    mock_doc = MagicMock()
    mock_doc.content = "test chunk"
    mock_doc.metadata = {"pages": [1]}
    mock_process.return_value = [mock_doc]

    mock_embedder = MagicMock()
    mock_embedder.embed_batched.return_value = [[0.1, 0.2, 0.3]]
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.store_embeddings.return_value = 1
    mock_get_storage.return_value = mock_storage

    # Make request
    data = {
        "file": (io.BytesIO(b"test content"), "test.txt"),
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data")

    assert response.status_code == 200
    assert "document_id" in response.json
    assert response.json["chunks_stored"] == 1


def test_upload_unsupported_file_type(client):
    """Test upload with unsupported file type returns VALIDATION_UNSUPPORTED_FILE."""
    with patch("app.process_file") as mock_process:
        mock_process.side_effect = ValueError("Unsupported file type: .xyz")
        data = {
            "file": (io.BytesIO(b"test content"), "test.xyz"),
        }
        response = client.post("/upload", data=data, content_type="multipart/form-data")
        assert response.status_code == 400
        assert response.json["code"] == "VALIDATION_UNSUPPORTED_FILE"


@patch("app.process_file")
def test_upload_empty_content(mock_process, client):
    """Test upload with empty content returns VALIDATION_EMPTY_CONTENT."""
    mock_process.return_value = []  # No documents extracted
    data = {
        "file": (io.BytesIO(b""), "test.txt"),
    }
    response = client.post("/upload", data=data, content_type="multipart/form-data")
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_EMPTY_CONTENT"


def test_search_no_query(client):
    """Test search endpoint with missing query returns VALIDATION_INVALID_QUERY."""
    response = client.post("/search", json={})
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_INVALID_QUERY"


def test_search_empty_query(client):
    """Test search with empty query returns VALIDATION_INVALID_QUERY."""
    response = client.post("/search", json={"query": ""})
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_INVALID_QUERY"


def test_search_query_too_long(client):
    """Test search with query exceeding max length returns VALIDATION_QUERY_TOO_LONG."""
    long_query = "x" * 10001
    response = client.post("/search", json={"query": long_query})
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_QUERY_TOO_LONG"
    assert "max_length" in response.json.get("details", {})


def test_search_invalid_limit_too_low(client):
    """Test search with limit below minimum returns VALIDATION_INVALID_LIMIT."""
    response = client.post("/search", json={"query": "test", "limit": 0})
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_INVALID_LIMIT"


def test_search_invalid_limit_too_high(client):
    """Test search with limit above maximum returns VALIDATION_INVALID_LIMIT."""
    response = client.post("/search", json={"query": "test", "limit": 101})
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_INVALID_LIMIT"


def test_search_invalid_model(client):
    """Test search with invalid model returns VALIDATION_INVALID_MODEL."""
    response = client.post("/search", json={"query": "test", "model": "invalid"})
    assert response.status_code == 400
    assert response.json["code"] == "VALIDATION_INVALID_MODEL"


@patch("app.get_embedder")
@patch("app.get_storage")
def test_search_success(mock_get_storage, mock_get_embedder, client):
    """Test successful search request."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.return_value = [0.1, 0.2, 0.3]
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.vector_search.return_value = [
        {"text": "test result", "score": 0.95}
    ]
    mock_get_storage.return_value = mock_storage

    response = client.post("/search", json={"query": "test query"})

    assert response.status_code == 200
    assert response.json["query"] == "test query"
    assert len(response.json["results"]) == 1


@patch("app.get_embedder")
def test_rate_limit_error_returns_429(mock_get_embedder, client):
    """Test that API rate limit errors return 429 status code."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.side_effect = ExternalAPIError(
        message="Rate limit exceeded",
        code=ErrorCode.API_RATE_LIMIT,
        retryable=True,
    )
    mock_get_embedder.return_value = mock_embedder

    response = client.post("/search", json={"query": "test"})

    assert response.status_code == 429
    assert response.json["code"] == "API_RATE_LIMIT"
    assert response.json["retryable"] is True


@patch("app.get_embedder")
def test_api_unavailable_returns_502(mock_get_embedder, client):
    """Test that API unavailable errors return 502 status code."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.side_effect = ExternalAPIError(
        message="Service unavailable",
        code=ErrorCode.API_UNAVAILABLE,
        retryable=True,
    )
    mock_get_embedder.return_value = mock_embedder

    response = client.post("/search", json={"query": "test"})

    assert response.status_code == 502
    assert response.json["code"] == "API_UNAVAILABLE"


@patch("app.get_embedder")
@patch("app.get_storage")
def test_storage_error_returns_503(mock_get_storage, mock_get_embedder, client):
    """Test that storage errors return 503 status code."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.return_value = [0.1, 0.2, 0.3]
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.vector_search.side_effect = StorageError(
        message="Connection failed",
        code=ErrorCode.STORAGE_CONNECTION,
        retryable=True,
    )
    mock_get_storage.return_value = mock_storage

    response = client.post("/search", json={"query": "test"})

    assert response.status_code == 503
    assert response.json["code"] == "STORAGE_CONNECTION"
    assert response.json["retryable"] is True


@patch("app.get_embedder")
def test_unexpected_error_returns_500(mock_get_embedder, client):
    """Test that unexpected errors return 500 with generic message."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.side_effect = RuntimeError("Unexpected internal error")
    mock_get_embedder.return_value = mock_embedder

    response = client.post("/search", json={"query": "test"})

    assert response.status_code == 500
    assert response.json["code"] == "INTERNAL_ERROR"
    assert "internal error" in response.json["error"].lower()
    # Should not expose internal error details
    assert "Unexpected internal error" not in response.json["error"]

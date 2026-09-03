"""Tests for rag-agent prototype."""

import io
import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app import app
from version import __version__, get_version_info
from utils.exceptions import ErrorCode, ExternalAPIError, StorageError


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"RAG Agent" in response.content


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


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
    assert response.json()["status"] == "healthy"
    assert response.json()["components"]["embedder"]["healthy"] is True
    assert response.json()["components"]["storage"]["healthy"] is True


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
    assert response.json()["status"] == "unhealthy"
    assert response.json()["components"]["embedder"]["healthy"] is False


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
    assert response.json()["status"] == "unhealthy"
    assert response.json()["components"]["storage"]["healthy"] is False


def test_version_endpoint(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert "version" in response.json()
    assert "git_sha" in response.json()


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
    assert "models" in response.json()
    models = response.json()["models"]
    # Check that expected models are registered
    assert "gemini-embedding-2" in models
    assert "gemini-embedding-001" in models


def test_storages_endpoint(client):
    response = client.get("/storages")
    assert response.status_code == 200
    assert "storages" in response.json()
    assert "mongodb" in response.json()["storages"]


def test_search_no_query(client):
    """Test search endpoint with missing query returns 422 (FastAPI validation)."""
    response = client.post("/search", json={})
    assert response.status_code == 422  # FastAPI validation error for missing required field


def test_search_empty_query(client):
    """Test search with empty query returns 422 (Pydantic validation)."""
    response = client.post("/search", json={"query": ""})
    assert response.status_code == 422  # Pydantic min_length validation


def test_search_query_too_long(client):
    """Test search with query exceeding max length returns 422 (Pydantic validation)."""
    long_query = "x" * 10001
    response = client.post("/search", json={"query": long_query})
    assert response.status_code == 422  # Pydantic max_length validation


def test_search_invalid_limit_too_low(client):
    """Test search with limit below minimum returns 422 (Pydantic validation)."""
    response = client.post("/search", json={"query": "test", "limit": 0})
    assert response.status_code == 422  # Pydantic ge validation


def test_search_invalid_limit_too_high(client):
    """Test search with limit above maximum returns 422 (Pydantic validation)."""
    response = client.post("/search", json={"query": "test", "limit": 101})
    assert response.status_code == 422  # Pydantic le validation


def test_search_invalid_model(client):
    """Test search with invalid model returns VALIDATION_INVALID_MODEL."""
    response = client.post("/search", json={"query": "test", "model": "invalid"})
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_INVALID_MODEL"


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
    assert response.json()["query"] == "test query"
    assert len(response.json()["results"]) == 1


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
    assert response.json()["code"] == "API_RATE_LIMIT"
    assert response.json()["retryable"] is True


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
    assert response.json()["code"] == "API_UNAVAILABLE"


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
    assert response.json()["code"] == "STORAGE_CONNECTION"
    assert response.json()["retryable"] is True


@patch("app.get_embedder")
def test_unexpected_error_returns_500(mock_get_embedder, client):
    """Test that unexpected errors return 500 with generic message."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.side_effect = RuntimeError("Unexpected internal error")
    mock_get_embedder.return_value = mock_embedder

    response = client.post("/search", json={"query": "test"})

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "internal error" in response.json()["error"].lower()
    # Should not expose internal error details
    assert "Unexpected internal error" not in response.json()["error"]


# API v1 endpoint tests
def test_api_v1_health(client):
    """Test /api/v1/health endpoint."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_api_v1_version(client):
    """Test /api/v1/version endpoint."""
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    assert "version" in response.json()


def test_api_v1_models(client):
    """Test /api/v1/models endpoint."""
    response = client.get("/api/v1/models")
    assert response.status_code == 200
    assert "models" in response.json()


def test_api_v1_storages(client):
    """Test /api/v1/storages endpoint."""
    response = client.get("/api/v1/storages")
    assert response.status_code == 200
    assert "storages" in response.json()


def test_api_v1_chat_models(client):
    """Test /api/v1/chat-models endpoint."""
    response = client.get("/api/v1/chat-models")
    assert response.status_code == 200
    assert "models" in response.json()


@patch("app.get_storage")
def test_api_v1_documents_returns_list(mock_get_storage, client):
    """Test /api/v1/documents returns document list."""
    mock_storage = MagicMock()
    mock_storage.list_documents.return_value = [
        {"document_id": "doc1", "filename": "report.pdf", "chunk_count": 10, "created_at": "2024-01-01T00:00:00"},
        {"document_id": "doc2", "filename": "notes.txt", "chunk_count": 5, "created_at": "2024-01-02T00:00:00"},
    ]
    mock_get_storage.return_value = mock_storage

    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    assert "documents" in response.json()
    assert len(response.json()["documents"]) == 2
    assert response.json()["documents"][0]["filename"] == "report.pdf"


@patch("app.get_storage")
def test_api_v1_documents_empty_list(mock_get_storage, client):
    """Test /api/v1/documents returns empty list when no documents."""
    mock_storage = MagicMock()
    mock_storage.list_documents.return_value = []
    mock_get_storage.return_value = mock_storage

    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    assert response.json()["documents"] == []


@patch("app.get_embedder")
@patch("app.get_storage")
def test_search_with_filename_filter(mock_get_storage, mock_get_embedder, client):
    """Test search with filenames filter passes filter to storage."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.return_value = [0.1, 0.2, 0.3]
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.vector_search.return_value = [
        {"text": "filtered result", "score": 0.95, "filename": "report.pdf"}
    ]
    mock_get_storage.return_value = mock_storage

    response = client.post("/search", json={
        "query": "test query",
        "filenames": ["report.pdf", "notes.txt"]
    })

    assert response.status_code == 200
    # Verify filenames were passed to vector_search
    mock_storage.vector_search.assert_called_once()
    call_kwargs = mock_storage.vector_search.call_args
    assert call_kwargs[1]["filenames"] == ["report.pdf", "notes.txt"]


@patch("app.get_embedder")
@patch("app.get_storage")
def test_search_without_filename_filter(mock_get_storage, mock_get_embedder, client):
    """Test search without filenames filter passes None to storage."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.return_value = [0.1, 0.2, 0.3]
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.vector_search.return_value = [
        {"text": "all results", "score": 0.95}
    ]
    mock_get_storage.return_value = mock_storage

    response = client.post("/search", json={"query": "test query"})

    assert response.status_code == 200
    # Verify filenames is None when not provided
    mock_storage.vector_search.assert_called_once()
    call_kwargs = mock_storage.vector_search.call_args
    assert call_kwargs[1]["filenames"] is None


@patch("app.get_storage")
def test_delete_document_success(mock_get_storage, client):
    """Test DELETE /api/v1/documents/{id} returns deleted count."""
    mock_storage = MagicMock()
    mock_storage.delete_document.return_value = 10
    mock_get_storage.return_value = mock_storage

    response = client.delete("/api/v1/documents/doc-123")

    assert response.status_code == 200
    assert response.json()["document_id"] == "doc-123"
    assert response.json()["deleted_chunks"] == 10
    mock_storage.delete_document.assert_called_once_with("doc-123")


@patch("app.get_storage")
def test_delete_document_not_found(mock_get_storage, client):
    """Test DELETE non-existent document returns 404."""
    mock_storage = MagicMock()
    mock_storage.delete_document.return_value = 0
    mock_get_storage.return_value = mock_storage

    response = client.delete("/api/v1/documents/nonexistent-doc")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@patch("app.get_storage")
def test_api_v1_document_models(mock_get_storage, client):
    """Test /api/v1/document-models returns unique models."""
    mock_storage = MagicMock()
    mock_storage.list_unique_models.return_value = ["gemini-embedding-2", "gemini-embedding-001"]
    mock_get_storage.return_value = mock_storage

    response = client.get("/api/v1/document-models")

    assert response.status_code == 200
    assert "models" in response.json()
    assert response.json()["models"] == ["gemini-embedding-2", "gemini-embedding-001"]


@patch("app.get_storage")
def test_api_v1_documents_with_model_filter(mock_get_storage, client):
    """Test /api/v1/documents with model filter."""
    mock_storage = MagicMock()
    mock_storage.list_documents.return_value = [
        {"document_id": "doc1", "filename": "report.pdf", "model": "gemini-embedding-2", "chunk_count": 10},
    ]
    mock_get_storage.return_value = mock_storage

    response = client.get("/api/v1/documents?model=gemini-embedding-2")

    assert response.status_code == 200
    mock_storage.list_documents.assert_called_once_with("gemini-embedding-2")


@patch("app.get_storage")
def test_api_v1_documents_includes_model_field(mock_get_storage, client):
    """Test /api/v1/documents returns model field."""
    mock_storage = MagicMock()
    mock_storage.list_documents.return_value = [
        {"document_id": "doc1", "filename": "report.pdf", "model": "gemini-embedding-2", "chunk_count": 10},
    ]
    mock_get_storage.return_value = mock_storage

    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    assert response.json()["documents"][0]["model"] == "gemini-embedding-2"


@patch("app.get_embedder")
@patch("app.get_storage")
def test_search_with_filter_model(mock_get_storage, mock_get_embedder, client):
    """Test search with filter_model passes filter to storage."""
    mock_embedder = MagicMock()
    mock_embedder.embed_query.return_value = [0.1, 0.2, 0.3]
    mock_get_embedder.return_value = mock_embedder

    mock_storage = MagicMock()
    mock_storage.vector_search.return_value = [
        {"text": "result", "score": 0.95, "model": "gemini-embedding-2"}
    ]
    mock_get_storage.return_value = mock_storage

    response = client.post("/search", json={
        "query": "test query",
        "filter_model": "gemini-embedding-2"
    })

    assert response.status_code == 200
    # Verify filter_model was passed to vector_search
    mock_storage.vector_search.assert_called_once()
    call_kwargs = mock_storage.vector_search.call_args
    assert call_kwargs[1]["model"] == "gemini-embedding-2"

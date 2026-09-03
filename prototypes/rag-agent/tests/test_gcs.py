"""Tests for GCS integration endpoints."""

import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app import app
from utils.exceptions import ErrorCode, GCSError


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


class TestSignedUrlEndpoint:
    """Tests for POST /api/v1/upload/signed-url"""

    def test_signed_url_success(self, client):
        """Test successful signed URL generation."""
        with patch("app.generate_upload_url") as mock_generate:
            mock_generate.return_value = {
                "signed_url": "https://storage.googleapis.com/test-signed-url",
                "gcs_path": "uploads/abc123/test.pdf",
                "expires_in_minutes": 15,
            }

            response = client.post(
                "/api/v1/upload/signed-url",
                json={"filename": "test.pdf", "content_type": "application/pdf"},
            )

            assert response.status_code == 200
            data = response.json()
            assert "signed_url" in data
            assert "gcs_path" in data
            assert data["expires_in_minutes"] == 15

    def test_signed_url_unsupported_file_type(self, client):
        """Test signed URL request with unsupported file type."""
        response = client.post(
            "/api/v1/upload/signed-url",
            json={"filename": "test.xyz", "content_type": "application/octet-stream"},
        )

        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_UNSUPPORTED_FILE"

    def test_signed_url_missing_filename(self, client):
        """Test signed URL request without filename."""
        response = client.post(
            "/api/v1/upload/signed-url",
            json={"content_type": "application/pdf"},
        )

        assert response.status_code == 422  # FastAPI validation

    def test_signed_url_empty_filename(self, client):
        """Test signed URL request with empty filename."""
        response = client.post(
            "/api/v1/upload/signed-url",
            json={"filename": "", "content_type": "application/pdf"},
        )

        assert response.status_code == 422  # Pydantic min_length validation

    def test_signed_url_default_content_type(self, client):
        """Test signed URL request uses default content type."""
        with patch("app.generate_upload_url") as mock_generate:
            mock_generate.return_value = {
                "signed_url": "https://storage.googleapis.com/test",
                "gcs_path": "uploads/abc/test.txt",
                "expires_in_minutes": 15,
            }

            response = client.post(
                "/api/v1/upload/signed-url",
                json={"filename": "test.txt"},
            )

            assert response.status_code == 200
            mock_generate.assert_called_once_with("test.txt", "application/octet-stream")

    def test_signed_url_gcs_error(self, client):
        """Test signed URL when GCS fails."""
        with patch("app.generate_upload_url") as mock_generate:
            mock_generate.side_effect = GCSError(
                message="Failed to generate URL",
                code=ErrorCode.GCS_ERROR,
                retryable=True,
            )

            response = client.post(
                "/api/v1/upload/signed-url",
                json={"filename": "test.pdf", "content_type": "application/pdf"},
            )

            assert response.status_code == 500
            assert response.json()["code"] == "GCS_ERROR"


class TestEmbedFromGCSEndpoint:
    """Tests for POST /api/v1/embed-from-gcs"""

    @patch("app.delete_file")
    @patch("app.get_storage")
    @patch("app.get_embedder")
    @patch("app.process_file")
    @patch("app.download_file")
    @patch("app.extract_filename")
    def test_embed_from_gcs_success(
        self,
        mock_extract,
        mock_download,
        mock_process,
        mock_get_embedder,
        mock_get_storage,
        mock_delete,
        client,
    ):
        """Test successful embedding from GCS."""
        mock_extract.return_value = "test.pdf"
        mock_download.return_value = b"fake pdf content"

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

        response = client.post(
            "/api/v1/embed-from-gcs",
            json={"gcs_path": "uploads/uuid/test.pdf", "model": "gemini"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "document_id" in data
        assert data["chunks_stored"] == 1
        assert data["source"] == "gcs"

        # Verify cleanup was called
        mock_delete.assert_called_once_with("uploads/uuid/test.pdf")

    @patch("app.download_file")
    @patch("app.extract_filename")
    def test_embed_from_gcs_no_cleanup(
        self, mock_extract, mock_download, client
    ):
        """Test embedding without cleanup."""
        mock_extract.return_value = "test.txt"
        mock_download.return_value = b"test content"

        with patch("app.process_file") as mock_process, \
             patch("app.get_embedder") as mock_get_embedder, \
             patch("app.get_storage") as mock_get_storage, \
             patch("app.delete_file") as mock_delete:

            mock_doc = MagicMock()
            mock_doc.content = "test"
            mock_doc.metadata = {}
            mock_process.return_value = [mock_doc]

            mock_embedder = MagicMock()
            mock_embedder.embed_batched.return_value = [[0.1]]
            mock_get_embedder.return_value = mock_embedder

            mock_storage = MagicMock()
            mock_storage.store_embeddings.return_value = 1
            mock_get_storage.return_value = mock_storage

            response = client.post(
                "/api/v1/embed-from-gcs",
                json={
                    "gcs_path": "uploads/uuid/test.txt",
                    "model": "gemini",
                    "cleanup": False,
                },
            )

            assert response.status_code == 200
            mock_delete.assert_not_called()

    def test_embed_from_gcs_invalid_model(self, client):
        """Test embedding with invalid model."""
        response = client.post(
            "/api/v1/embed-from-gcs",
            json={"gcs_path": "uploads/uuid/test.pdf", "model": "invalid"},
        )

        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_INVALID_MODEL"

    def test_embed_from_gcs_file_not_found(self, client):
        """Test embedding when file not found in GCS."""
        with patch("app.download_file") as mock_download:
            mock_download.side_effect = GCSError(
                message="File not found",
                code=ErrorCode.GCS_FILE_NOT_FOUND,
                details={"gcs_path": "uploads/uuid/missing.pdf"},
            )

            response = client.post(
                "/api/v1/embed-from-gcs",
                json={"gcs_path": "uploads/uuid/missing.pdf"},
            )

            assert response.status_code == 404
            assert response.json()["code"] == "GCS_FILE_NOT_FOUND"

    def test_embed_from_gcs_access_denied(self, client):
        """Test embedding when access denied."""
        with patch("app.download_file") as mock_download:
            mock_download.side_effect = GCSError(
                message="Access denied",
                code=ErrorCode.GCS_ACCESS_DENIED,
            )

            response = client.post(
                "/api/v1/embed-from-gcs",
                json={"gcs_path": "uploads/uuid/test.pdf"},
            )

            assert response.status_code == 403
            assert response.json()["code"] == "GCS_ACCESS_DENIED"

    def test_embed_from_gcs_invalid_path(self, client):
        """Test embedding with invalid GCS path."""
        with patch("app.download_file") as mock_download:
            mock_download.side_effect = GCSError(
                message="Invalid path",
                code=ErrorCode.GCS_INVALID_PATH,
            )

            response = client.post(
                "/api/v1/embed-from-gcs",
                json={"gcs_path": "invalid/path/test.pdf"},
            )

            assert response.status_code == 400
            assert response.json()["code"] == "GCS_INVALID_PATH"

    @patch("app.download_file")
    @patch("app.extract_filename")
    @patch("app.process_file")
    def test_embed_from_gcs_empty_content(
        self, mock_process, mock_extract, mock_download, client
    ):
        """Test embedding when file has no extractable content."""
        mock_extract.return_value = "empty.pdf"
        mock_download.return_value = b"fake content"
        mock_process.return_value = []

        response = client.post(
            "/api/v1/embed-from-gcs",
            json={"gcs_path": "uploads/uuid/empty.pdf"},
        )

        assert response.status_code == 400
        assert response.json()["code"] == "VALIDATION_EMPTY_CONTENT"

    def test_embed_from_gcs_missing_path(self, client):
        """Test embedding without gcs_path."""
        response = client.post(
            "/api/v1/embed-from-gcs",
            json={"model": "gemini"},
        )

        assert response.status_code == 422  # FastAPI validation

"""Google Cloud Storage service for large file uploads."""

import logging
import os
import uuid
from datetime import timedelta

from google.cloud import storage
from google.cloud.exceptions import NotFound, Forbidden

from utils.exceptions import GCSError, ErrorCode

logger = logging.getLogger(__name__)

# Configuration
GCS_BUCKET = os.environ.get("GCS_BUCKET", "rag-agent-uploads")
SIGNED_URL_EXPIRY_MINUTES = 15

# Cached client
_client: storage.Client | None = None


def _get_client() -> storage.Client:
    """Get or create a cached GCS client."""
    global _client
    if _client is None:
        _client = storage.Client()
        logger.info("GCS client initialized")
    return _client


def _get_bucket() -> storage.Bucket:
    """Get the configured GCS bucket."""
    client = _get_client()
    return client.bucket(GCS_BUCKET)


def generate_upload_url(filename: str, content_type: str) -> dict:
    """Generate a signed URL for direct upload to GCS.

    Args:
        filename: Original filename (used in the GCS path)
        content_type: MIME type of the file

    Returns:
        Dict with signed_url, gcs_path, and expires_in_minutes
    """
    logger.info("Generating signed upload URL for: %s", filename)

    # Generate unique path: uploads/<uuid>/<filename>
    upload_id = str(uuid.uuid4())
    gcs_path = f"uploads/{upload_id}/{filename}"

    bucket = _get_bucket()
    blob = bucket.blob(gcs_path)

    try:
        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=SIGNED_URL_EXPIRY_MINUTES),
            method="PUT",
            content_type=content_type,
        )
    except Exception as e:
        logger.error("Failed to generate signed URL: %s", str(e))
        raise GCSError(
            message=f"Failed to generate upload URL: {str(e)}",
            code=ErrorCode.GCS_ERROR,
            retryable=True,
        ) from e

    logger.info("Signed URL generated for path: %s", gcs_path)

    return {
        "signed_url": signed_url,
        "gcs_path": gcs_path,
        "expires_in_minutes": SIGNED_URL_EXPIRY_MINUTES,
    }


def download_file(gcs_path: str) -> bytes:
    """Download file content from GCS.

    Args:
        gcs_path: Path to the file in the bucket (e.g., uploads/uuid/file.pdf)

    Returns:
        File content as bytes

    Raises:
        GCSError: If file not found, access denied, or other GCS error
    """
    logger.info("Downloading file from GCS: %s", gcs_path)

    # Validate path format
    if not gcs_path.startswith("uploads/"):
        raise GCSError(
            message="Invalid GCS path format",
            code=ErrorCode.GCS_INVALID_PATH,
            details={"gcs_path": gcs_path},
        )

    bucket = _get_bucket()
    blob = bucket.blob(gcs_path)

    try:
        content = blob.download_as_bytes()
        logger.info("Downloaded %d bytes from GCS", len(content))
        return content
    except NotFound as e:
        logger.warning("File not found in GCS: %s", gcs_path)
        raise GCSError(
            message="File not found in cloud storage",
            code=ErrorCode.GCS_FILE_NOT_FOUND,
            details={"gcs_path": gcs_path},
        ) from e
    except Forbidden as e:
        logger.error("Access denied to GCS file: %s", gcs_path)
        raise GCSError(
            message="Access denied to cloud storage",
            code=ErrorCode.GCS_ACCESS_DENIED,
            details={"gcs_path": gcs_path},
        ) from e
    except Exception as e:
        logger.error("GCS download error: %s", str(e))
        raise GCSError(
            message=f"Failed to download from cloud storage: {str(e)}",
            code=ErrorCode.GCS_ERROR,
            retryable=True,
        ) from e


def delete_file(gcs_path: str) -> bool:
    """Delete a file from GCS.

    Args:
        gcs_path: Path to the file in the bucket

    Returns:
        True if deleted, False if file didn't exist
    """
    logger.info("Deleting file from GCS: %s", gcs_path)

    bucket = _get_bucket()
    blob = bucket.blob(gcs_path)

    try:
        blob.delete()
        logger.info("Deleted file from GCS: %s", gcs_path)
        return True
    except NotFound:
        logger.warning("File not found for deletion: %s", gcs_path)
        return False
    except Exception as e:
        logger.error("Failed to delete from GCS: %s", str(e))
        # Don't raise on cleanup failure - just log it
        return False


def extract_filename(gcs_path: str) -> str:
    """Extract the original filename from a GCS path.

    Args:
        gcs_path: Path like uploads/uuid/filename.pdf

    Returns:
        The filename portion
    """
    return gcs_path.split("/")[-1]

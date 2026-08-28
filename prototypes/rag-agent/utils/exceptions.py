"""Custom exceptions for rag-agent service."""

from enum import Enum


class ErrorCode(str, Enum):
    """Error codes for categorizing exceptions."""

    # Validation errors (400)
    VALIDATION_MISSING_FILE = "VALIDATION_MISSING_FILE"
    VALIDATION_MISSING_FILENAME = "VALIDATION_MISSING_FILENAME"
    VALIDATION_INVALID_MODEL = "VALIDATION_INVALID_MODEL"
    VALIDATION_INVALID_QUERY = "VALIDATION_INVALID_QUERY"
    VALIDATION_INVALID_LIMIT = "VALIDATION_INVALID_LIMIT"
    VALIDATION_QUERY_TOO_LONG = "VALIDATION_QUERY_TOO_LONG"
    VALIDATION_UNSUPPORTED_FILE = "VALIDATION_UNSUPPORTED_FILE"
    VALIDATION_EMPTY_CONTENT = "VALIDATION_EMPTY_CONTENT"
    VALIDATION_INVALID_MESSAGE = "VALIDATION_INVALID_MESSAGE"

    # External API errors (502/429)
    API_RATE_LIMIT = "API_RATE_LIMIT"
    API_AUTH_FAILURE = "API_AUTH_FAILURE"
    API_UNAVAILABLE = "API_UNAVAILABLE"
    API_ERROR = "API_ERROR"

    # Storage errors (503)
    STORAGE_CONNECTION = "STORAGE_CONNECTION"
    STORAGE_TIMEOUT = "STORAGE_TIMEOUT"
    STORAGE_ERROR = "STORAGE_ERROR"

    # GCS errors (various)
    GCS_FILE_NOT_FOUND = "GCS_FILE_NOT_FOUND"
    GCS_INVALID_PATH = "GCS_INVALID_PATH"
    GCS_ACCESS_DENIED = "GCS_ACCESS_DENIED"
    GCS_ERROR = "GCS_ERROR"

    # Internal errors (500)
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RagAgentError(Exception):
    """Base exception for rag-agent service."""

    def __init__(
        self,
        message: str,
        code: ErrorCode,
        details: dict | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}
        self.retryable = retryable

    def to_dict(self) -> dict:
        """Convert exception to JSON-serializable dict."""
        result = {
            "error": self.message,
            "code": self.code.value,
            "retryable": self.retryable,
        }
        if self.details:
            result["details"] = self.details
        return result


class ValidationError(RagAgentError):
    """Validation error (400)."""

    def __init__(
        self,
        message: str,
        code: ErrorCode,
        details: dict | None = None,
    ):
        super().__init__(message, code, details, retryable=False)


class ExternalAPIError(RagAgentError):
    """External API error (502/429)."""

    def __init__(
        self,
        message: str,
        code: ErrorCode,
        details: dict | None = None,
        retryable: bool = True,
    ):
        super().__init__(message, code, details, retryable)


class StorageError(RagAgentError):
    """Storage backend error (503)."""

    def __init__(
        self,
        message: str,
        code: ErrorCode,
        details: dict | None = None,
        retryable: bool = True,
    ):
        super().__init__(message, code, details, retryable)


class GCSError(RagAgentError):
    """Google Cloud Storage error."""

    def __init__(
        self,
        message: str,
        code: ErrorCode,
        details: dict | None = None,
        retryable: bool = False,
    ):
        super().__init__(message, code, details, retryable)

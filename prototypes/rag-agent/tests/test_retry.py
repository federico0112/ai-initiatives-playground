"""Tests for retry utility."""

import pytest
from unittest.mock import MagicMock, patch

from utils.retry import with_retry


def test_success_on_first_try():
    """Test function succeeds on first try."""
    mock_func = MagicMock(return_value="success")

    @with_retry(retries=3, delay=0.01)
    def test_func():
        return mock_func()

    result = test_func()

    assert result == "success"
    assert mock_func.call_count == 1


def test_success_after_failures():
    """Test function succeeds after some failures."""
    call_count = 0

    @with_retry(retries=3, delay=0.01, exceptions=(ValueError,))
    def test_func():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("temporary failure")
        return "success"

    result = test_func()

    assert result == "success"
    assert call_count == 3


def test_exhausted_retries_raises():
    """Test that exhausted retries raises the last exception."""
    call_count = 0

    @with_retry(retries=2, delay=0.01, exceptions=(ValueError,))
    def test_func():
        nonlocal call_count
        call_count += 1
        raise ValueError(f"failure {call_count}")

    with pytest.raises(ValueError) as exc_info:
        test_func()

    assert "failure 3" in str(exc_info.value)
    assert call_count == 3  # Initial + 2 retries


def test_non_matching_exception_not_retried():
    """Test that non-matching exceptions are not retried."""
    call_count = 0

    @with_retry(retries=3, delay=0.01, exceptions=(ValueError,))
    def test_func():
        nonlocal call_count
        call_count += 1
        raise TypeError("wrong type")

    with pytest.raises(TypeError):
        test_func()

    assert call_count == 1  # No retries for non-matching exception


def test_backoff_multiplier():
    """Test exponential backoff with mocked sleep."""
    delays = []

    @with_retry(retries=3, delay=1.0, backoff=2.0, exceptions=(ValueError,))
    def test_func():
        raise ValueError("always fails")

    with patch("utils.retry.time.sleep") as mock_sleep:
        mock_sleep.side_effect = lambda d: delays.append(d)

        with pytest.raises(ValueError):
            test_func()

    assert delays == [1.0, 2.0, 4.0]


def test_zero_retries():
    """Test with zero retries (just one attempt)."""
    call_count = 0

    @with_retry(retries=0, delay=0.01, exceptions=(ValueError,))
    def test_func():
        nonlocal call_count
        call_count += 1
        raise ValueError("fail")

    with pytest.raises(ValueError):
        test_func()

    assert call_count == 1


def test_preserves_function_metadata():
    """Test that decorator preserves function metadata."""
    @with_retry(retries=1)
    def documented_func():
        """This is the docstring."""
        return "result"

    assert documented_func.__name__ == "documented_func"
    assert documented_func.__doc__ == "This is the docstring."


def test_multiple_exception_types():
    """Test retry on multiple exception types."""
    errors = [ValueError("val error"), TypeError("type error")]
    call_count = 0

    @with_retry(retries=2, delay=0.01, exceptions=(ValueError, TypeError))
    def test_func():
        nonlocal call_count
        if call_count < len(errors):
            call_count += 1
            raise errors[call_count - 1]
        return "success"

    result = test_func()

    assert result == "success"
    assert call_count == 2

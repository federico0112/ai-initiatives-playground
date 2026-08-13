"""Tests for the Hello World application."""
import pytest
from app import app
from version import __version__, get_version_info


@pytest.fixture
def client():
    """Create a test client for the app."""
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_hello_returns_200(client):
    """Test that the root endpoint returns 200."""
    response = client.get("/")
    assert response.status_code == 200


def test_hello_contains_hello_world(client):
    """Test that the response contains Hello World."""
    response = client.get("/")
    assert b"Hello World" in response.data


def test_hello_returns_html(client):
    """Test that the response is HTML."""
    response = client.get("/")
    assert b"<!DOCTYPE html>" in response.data


def test_health_endpoint(client):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "healthy"}


def test_version_endpoint(client):
    """Test the version endpoint."""
    response = client.get("/version")
    assert response.status_code == 200
    assert "version" in response.json
    assert "git_sha" in response.json


def test_version_format():
    """Test version string format."""
    assert __version__ is not None
    parts = __version__.split(".")
    assert len(parts) == 3  # semantic versioning


def test_get_version_info():
    """Test get_version_info returns correct structure."""
    info = get_version_info()
    assert "version" in info
    assert "git_sha" in info
    assert info["version"] == __version__

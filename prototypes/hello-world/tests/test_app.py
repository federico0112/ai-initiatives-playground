"""Tests for the Hello World application."""
import pytest
from app import app


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

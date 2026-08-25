"""Tests for RAG chat endpoint and components."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from rag.session import (
    Session,
    SessionMemory,
    ChatMessage,
    get_session_memory,
    reset_session_memory,
)
from rag.components import MongoDBRetriever, format_sources
from rag.pipeline import DEFAULT_MODEL, SUPPORTED_MODELS


class TestSessionMemory:
    """Tests for session memory management."""

    def setup_method(self):
        """Reset session memory before each test."""
        reset_session_memory()

    def teardown_method(self):
        """Clean up after each test."""
        reset_session_memory()

    def test_create_new_session(self):
        """Test creating a new session."""
        memory = SessionMemory()
        session = memory.get_or_create()

        assert session.session_id is not None
        assert len(session.messages) == 0
        assert memory.session_count == 1

    def test_create_session_with_id(self):
        """Test creating a session with a specific ID."""
        memory = SessionMemory()
        session = memory.get_or_create("test-session-123")

        assert session.session_id == "test-session-123"
        assert memory.session_count == 1

    def test_get_existing_session(self):
        """Test retrieving an existing session."""
        memory = SessionMemory()
        session1 = memory.get_or_create("test-session")
        session1.add_message("user", "Hello")

        session2 = memory.get_or_create("test-session")

        assert session2.session_id == "test-session"
        assert len(session2.messages) == 1
        assert memory.session_count == 1

    def test_add_message(self):
        """Test adding messages to a session."""
        memory = SessionMemory()
        session = memory.get_or_create("test-session")

        memory.add_message("test-session", "user", "Hello")
        memory.add_message("test-session", "assistant", "Hi there!")

        history = memory.get_history("test-session")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "Hello"
        assert history[1]["role"] == "assistant"

    def test_get_history_window(self):
        """Test getting limited history window."""
        memory = SessionMemory()
        memory.get_or_create("test-session")

        # Add more messages than the window
        for i in range(15):
            memory.add_message("test-session", "user", f"Message {i}")

        history = memory.get_history("test-session", window=5)
        assert len(history) == 5
        assert history[0]["content"] == "Message 10"
        assert history[4]["content"] == "Message 14"

    def test_session_expiry(self):
        """Test that expired sessions are cleaned up."""
        memory = SessionMemory(ttl_seconds=1)
        memory.get_or_create("old-session")

        time.sleep(1.5)

        # Creating a new session triggers cleanup
        memory.get_or_create("new-session")

        assert memory.session_count == 1

    def test_max_messages_per_session(self):
        """Test message limit per session."""
        session = Session(session_id="test")

        # Add more than max messages
        for i in range(60):
            session.add_message("user", f"Message {i}")

        assert len(session.messages) == 50  # MAX_MESSAGES_PER_SESSION

    def test_global_session_memory(self):
        """Test global session memory singleton."""
        memory1 = get_session_memory()
        memory2 = get_session_memory()

        assert memory1 is memory2


class TestChatMessage:
    """Tests for ChatMessage dataclass."""

    def test_create_message(self):
        """Test creating a chat message."""
        msg = ChatMessage(role="user", content="Hello")

        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.timestamp > 0


class TestFormatSources:
    """Tests for source formatting."""

    def test_format_sources_basic(self):
        """Test basic source formatting."""
        from haystack import Document

        docs = [
            Document(
                content="Test content",
                meta={"filename": "test.pdf", "pages": [1, 2], "score": 0.95},
            ),
        ]

        sources = format_sources(docs)

        assert len(sources) == 1
        assert sources[0]["filename"] == "test.pdf"
        assert sources[0]["pages"] == [1, 2]
        assert sources[0]["score"] == 0.95

    def test_format_sources_deduplication(self):
        """Test that duplicate filenames are deduplicated."""
        from haystack import Document

        docs = [
            Document(
                content="Chunk 1",
                meta={"filename": "test.pdf", "pages": [1], "score": 0.95},
            ),
            Document(
                content="Chunk 2",
                meta={"filename": "test.pdf", "pages": [2], "score": 0.90},
            ),
        ]

        sources = format_sources(docs)

        assert len(sources) == 1
        assert sources[0]["filename"] == "test.pdf"

    def test_format_sources_missing_fields(self):
        """Test handling of missing metadata fields."""
        from haystack import Document

        docs = [
            Document(content="Test content", meta={}),
        ]

        sources = format_sources(docs)

        assert len(sources) == 1
        assert sources[0]["filename"] == "unknown"
        assert sources[0]["pages"] == []


class TestQueryEmbedder:
    """Tests for QueryEmbedder component."""

    @patch("rag.components.get_embedder")
    def test_query_embedder_run(self, mock_get_embedder):
        """Test query embedder execution."""
        from rag.components import QueryEmbedder

        # Mock embedder
        mock_embedder = MagicMock()
        mock_embedder.embed_query.return_value = [0.1] * 768
        mock_get_embedder.return_value = mock_embedder

        query_embedder = QueryEmbedder(embedder_name="gemini")
        result = query_embedder.run(query="test query")

        assert "embedding" in result
        assert len(result["embedding"]) == 768
        assert result["embedding"] == [0.1] * 768

        mock_embedder.embed_query.assert_called_once_with("test query")


class TestMongoDBRetriever:
    """Tests for MongoDB retriever component."""

    @patch("rag.components.get_storage")
    def test_retriever_run(self, mock_get_storage):
        """Test retriever execution with embedding input."""
        # Mock storage
        mock_storage = MagicMock()
        mock_storage.vector_search.return_value = [
            {
                "text": "Test content",
                "document_id": "doc-123",
                "filename": "test.pdf",
                "chunk_index": 0,
                "pages": [1],
                "score": 0.95,
                "model": "gemini",
            }
        ]
        mock_get_storage.return_value = mock_storage

        # Create embedding input
        test_embedding = [0.1] * 768

        retriever = MongoDBRetriever(top_k=5)
        result = retriever.run(embedding=test_embedding)

        assert "documents" in result
        assert len(result["documents"]) == 1
        assert result["documents"][0].content == "Test content"
        assert result["documents"][0].meta["filename"] == "test.pdf"

        mock_storage.vector_search.assert_called_once_with(
            query_embedding=test_embedding,
            limit=5,
        )


class TestPipelineConstants:
    """Tests for pipeline constants."""

    def test_default_model(self):
        """Test default model is set."""
        assert DEFAULT_MODEL == "gemini-2.5-flash"

    def test_supported_models(self):
        """Test supported models list."""
        assert "gemini-2.5-flash" in SUPPORTED_MODELS
        assert "gemini-2.5-pro" in SUPPORTED_MODELS
        assert "gemini-2.0-flash" in SUPPORTED_MODELS


class TestRetrievalPipeline:
    """Tests for retrieval pipeline."""

    def test_build_retrieval_pipeline(self):
        """Test building retrieval pipeline."""
        from rag.pipeline import build_retrieval_pipeline

        pipeline = build_retrieval_pipeline(top_k=5)

        # Verify pipeline has expected components
        assert "query_embedder" in pipeline.graph.nodes
        assert "retriever" in pipeline.graph.nodes
        assert "prompt_builder" in pipeline.graph.nodes

    @patch("rag.components.get_embedder")
    @patch("rag.components.get_storage")
    def test_retrieval_pipeline_run(self, mock_get_storage, mock_get_embedder):
        """Test running the retrieval pipeline."""
        from rag.pipeline import build_retrieval_pipeline

        # Mock embedder
        mock_embedder = MagicMock()
        mock_embedder.embed_query.return_value = [0.1] * 768
        mock_get_embedder.return_value = mock_embedder

        # Mock storage
        mock_storage = MagicMock()
        mock_storage.vector_search.return_value = [
            {
                "text": "Test content",
                "document_id": "doc-123",
                "filename": "test.pdf",
                "chunk_index": 0,
                "pages": [1],
                "score": 0.95,
                "model": "gemini",
            }
        ]
        mock_get_storage.return_value = mock_storage

        pipeline = build_retrieval_pipeline(top_k=5)
        result = pipeline.run(
            {
                "query_embedder": {"query": "test query"},
                "prompt_builder": {"query": "test query"},
            },
            include_outputs_from={"query_embedder", "retriever"},
        )

        # Verify query_embedder output is included
        assert "query_embedder" in result
        assert "embedding" in result["query_embedder"]
        assert len(result["query_embedder"]["embedding"]) == 768

        # Verify retriever output is included
        assert "retriever" in result
        assert "documents" in result["retriever"]
        assert len(result["retriever"]["documents"]) == 1

        # Verify prompt_builder output
        assert "prompt_builder" in result
        assert "prompt" in result["prompt_builder"]


# Integration tests with Flask app
@pytest.fixture
def client():
    """Create test client."""
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


class TestChatEndpoint:
    """Tests for /chat endpoint."""

    def setup_method(self):
        """Reset session memory before each test."""
        reset_session_memory()

    def teardown_method(self):
        """Clean up after each test."""
        reset_session_memory()

    def test_chat_missing_body(self, client):
        """Test chat with missing request body."""
        response = client.post(
            "/chat",
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_chat_missing_message(self, client):
        """Test chat with missing message field."""
        response = client.post(
            "/chat",
            json={},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["code"] == "VALIDATION_INVALID_MESSAGE"

    def test_chat_empty_message(self, client):
        """Test chat with empty message."""
        response = client.post(
            "/chat",
            json={"message": "   "},
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_chat_message_too_long(self, client):
        """Test chat with message exceeding max length."""
        response = client.post(
            "/chat",
            json={"message": "x" * 10001},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["code"] == "VALIDATION_INVALID_MESSAGE"

    def test_chat_invalid_top_k(self, client):
        """Test chat with invalid top_k."""
        response = client.post(
            "/chat",
            json={"message": "test", "top_k": 100},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["code"] == "VALIDATION_INVALID_LIMIT"

    def test_chat_invalid_model(self, client):
        """Test chat with invalid model."""
        response = client.post(
            "/chat",
            json={"message": "test", "model": "invalid-model"},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.get_json()
        assert data["code"] == "VALIDATION_INVALID_MODEL"

    @patch("app.run_rag_query")
    def test_chat_success_streaming(self, mock_run_rag_query, client):
        """Test successful chat with streaming response."""
        # Mock RAG query to yield events
        mock_run_rag_query.return_value = iter([
            {"type": "sources", "documents": [{"filename": "test.pdf", "pages": [1], "score": 0.95}]},
            {"type": "chunk", "content": "Hello "},
            {"type": "chunk", "content": "world!"},
            {"type": "done"},
        ])

        response = client.post(
            "/chat",
            json={"message": "What is in the document?"},
            content_type="application/json",
        )

        assert response.status_code == 200
        assert response.content_type == "application/x-ndjson"

        # Parse NDJSON response
        lines = response.data.decode().strip().split("\n")
        events = [json.loads(line) for line in lines]

        assert events[0]["type"] == "metadata"
        assert "session_id" in events[0]
        assert events[0]["model"] == "gemini-2.5-flash"

        assert events[1]["type"] == "sources"
        assert events[2]["type"] == "chunk"
        assert events[3]["type"] == "chunk"
        assert events[4]["type"] == "done"

    @patch("app.run_rag_query")
    def test_chat_with_session_id(self, mock_run_rag_query, client):
        """Test chat with provided session ID."""
        mock_run_rag_query.return_value = iter([
            {"type": "sources", "documents": []},
            {"type": "chunk", "content": "Response"},
            {"type": "done"},
        ])

        response = client.post(
            "/chat",
            json={"message": "Hello", "session_id": "my-session-123"},
            content_type="application/json",
        )

        assert response.status_code == 200

        lines = response.data.decode().strip().split("\n")
        events = [json.loads(line) for line in lines]

        assert events[0]["session_id"] == "my-session-123"

    @patch("app.run_rag_query")
    def test_chat_session_persistence(self, mock_run_rag_query, client):
        """Test that chat history persists across requests."""
        # Create side effect to return fresh iterator each time
        def mock_generator(*args, **kwargs):
            yield {"type": "sources", "documents": []}
            yield {"type": "chunk", "content": "Response"}
            yield {"type": "done"}

        mock_run_rag_query.side_effect = mock_generator

        # First request
        response1 = client.post(
            "/chat",
            json={"message": "Hello", "session_id": "persistent-session"},
            content_type="application/json",
        )
        assert response1.status_code == 200
        # Consume the streaming response
        _ = response1.data

        # Second request should have history
        response2 = client.post(
            "/chat",
            json={"message": "Follow up", "session_id": "persistent-session"},
            content_type="application/json",
        )
        assert response2.status_code == 200
        # Consume the streaming response
        _ = response2.data

        # Verify history was passed on the second call
        assert mock_run_rag_query.call_count == 2
        second_call_kwargs = mock_run_rag_query.call_args_list[1].kwargs
        # Check that chat_history was passed and has messages
        assert "chat_history" in second_call_kwargs
        assert len(second_call_kwargs["chat_history"]) >= 1

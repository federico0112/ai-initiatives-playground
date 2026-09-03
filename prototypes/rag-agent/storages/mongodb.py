"""MongoDB Atlas vector storage backend."""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from pymongo import MongoClient
from pymongo.errors import (
    ConnectionFailure,
    ServerSelectionTimeoutError,
    PyMongoError,
)

from .base import BaseStorage, register_storage
from utils.exceptions import ErrorCode, StorageError

logger = logging.getLogger(__name__)


@register_storage("mongodb")
class MongoDBStorage(BaseStorage):
    """MongoDB Atlas storage backend for document embeddings."""

    def __init__(self):
        """Initialize MongoDB connection."""
        logger.debug("Initializing MongoDBStorage")

        uri = os.environ.get("MONGODB_URI")
        if not uri:
            logger.error("MONGODB_URI environment variable not set")
            raise ValueError("MONGODB_URI environment variable is required")

        # Log sanitized URI (hide credentials)
        sanitized_uri = uri.split("@")[-1] if "@" in uri else uri
        logger.debug("Connecting to MongoDB: ...@%s", sanitized_uri)

        self._client = MongoClient(uri)
        self._db_name = os.environ.get("MONGODB_DATABASE", "rag_agent")
        self._collection_name = os.environ.get("MONGODB_COLLECTION", "documents")
        self._index_name = os.environ.get("MONGODB_INDEX_NAME", "vector_index")

        logger.info(
            "MongoDBStorage initialized: db=%s, collection=%s, index=%s",
            self._db_name,
            self._collection_name,
            self._index_name,
        )

    @property
    def _collection(self):
        """Get the MongoDB collection."""
        return self._client[self._db_name][self._collection_name]

    def store_embeddings(
        self,
        document_id: str,
        filename: str,
        chunks: list[str],
        embeddings: list[list[float]],
        model: str,
        chunk_metadata: list[dict] | None = None,
    ) -> int:
        """Store document chunks with their embeddings in MongoDB.

        Args:
            document_id: Unique identifier for the document.
            filename: Original filename.
            chunks: List of text chunks.
            embeddings: List of embedding vectors (one per chunk).
            model: Name of the embedding model used.
            chunk_metadata: Optional list of metadata dicts (one per chunk),
                containing fields like 'page', 'pages', 'headings'.

        Returns:
            Number of chunks stored.
        """
        logger.info(
            "Storing embeddings: document_id=%s, filename=%s, chunks=%d, model=%s",
            document_id,
            filename,
            len(chunks),
            model,
        )

        if len(chunks) != len(embeddings):
            logger.error(
                "Chunk/embedding count mismatch: %d chunks, %d embeddings",
                len(chunks),
                len(embeddings),
            )
            raise ValueError("Number of chunks must match number of embeddings")

        if chunk_metadata and len(chunk_metadata) != len(chunks):
            logger.error(
                "Chunk/metadata count mismatch: %d chunks, %d metadata entries",
                len(chunks),
                len(chunk_metadata),
            )
            raise ValueError("Number of metadata entries must match number of chunks")

        logger.debug("Preparing %d documents for insertion", len(chunks))
        documents = []
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            doc = {
                "document_id": document_id,
                "filename": filename,
                "chunk_index": i,
                "text": chunk,
                "embedding": embedding,
                "model": model,
                "created_at": datetime.now(timezone.utc),
            }
            # Add page info from metadata if available
            if chunk_metadata:
                meta = chunk_metadata[i]
                if "pages" in meta:
                    doc["pages"] = meta["pages"]
            documents.append(doc)

        if documents:
            logger.info("Inserting %d documents into MongoDB", len(documents))
            logger.debug(
                "Document sizes (text): %s",
                [len(d["text"]) for d in documents],
            )
            logger.debug(
                "Embedding dimensions: %s",
                [len(d["embedding"]) for d in documents],
            )

            try:
                result = self._collection.insert_many(documents)
                logger.info(
                    "Insert complete: %d documents inserted",
                    len(result.inserted_ids),
                )
                logger.debug("Inserted IDs: %s", result.inserted_ids)
            except ConnectionFailure as e:
                logger.error("MongoDB connection failed: %s", str(e))
                raise StorageError(
                    message="MongoDB connection failed",
                    code=ErrorCode.STORAGE_CONNECTION,
                    details={"database": self._db_name},
                    retryable=True,
                ) from e
            except ServerSelectionTimeoutError as e:
                logger.error("MongoDB server selection timeout: %s", str(e))
                raise StorageError(
                    message="MongoDB server selection timeout",
                    code=ErrorCode.STORAGE_TIMEOUT,
                    details={"database": self._db_name},
                    retryable=True,
                ) from e
            except PyMongoError as e:
                logger.error("MongoDB error: %s", str(e))
                raise StorageError(
                    message=f"MongoDB error: {str(e)}",
                    code=ErrorCode.STORAGE_ERROR,
                    details={"database": self._db_name},
                    retryable=False,
                ) from e
        else:
            logger.warning("No documents to insert")

        return len(documents)

    def vector_search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        filenames: list[str] | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search for similar documents using MongoDB Atlas Vector Search.

        Args:
            query_embedding: The query vector to search with.
            limit: Maximum number of results to return.
            filenames: Optional list of filenames to filter results.
            model: Optional embedding model name to filter results.

        Returns:
            List of matching documents with scores.
        """
        logger.info(
            "Vector search: limit=%d, index=%s, embedding_dim=%d, filenames=%s, model=%s",
            limit,
            self._index_name,
            len(query_embedding),
            filenames,
            model,
        )

        vector_search_stage = {
            "$vectorSearch": {
                "index": self._index_name,
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": limit * 10,
                "limit": limit,
            }
        }

        # Build filter conditions
        filter_conditions = {}
        if filenames:
            filter_conditions["filename"] = {"$in": filenames}
        if model:
            filter_conditions["model"] = model

        if filter_conditions:
            vector_search_stage["$vectorSearch"]["filter"] = filter_conditions

        pipeline = [
            vector_search_stage,
            {
                "$project": {
                    "_id": 0,
                    "document_id": 1,
                    "filename": 1,
                    "chunk_index": 1,
                    "pages": 1,
                    "text": 1,
                    "model": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]

        try:
            logger.debug("Executing vector search pipeline")
            results = list(self._collection.aggregate(pipeline))
            logger.info("Vector search complete: %d results", len(results))
            logger.debug(
                "Result scores: %s",
                [r.get("score") for r in results],
            )
            return results
        except ConnectionFailure as e:
            logger.error("MongoDB connection failed: %s", str(e))
            raise StorageError(
                message="MongoDB connection failed",
                code=ErrorCode.STORAGE_CONNECTION,
                details={"database": self._db_name},
                retryable=True,
            ) from e
        except ServerSelectionTimeoutError as e:
            logger.error("MongoDB server selection timeout: %s", str(e))
            raise StorageError(
                message="MongoDB server selection timeout",
                code=ErrorCode.STORAGE_TIMEOUT,
                details={"database": self._db_name},
                retryable=True,
            ) from e
        except PyMongoError as e:
            logger.error("MongoDB error: %s", str(e))
            raise StorageError(
                message=f"MongoDB error: {str(e)}",
                code=ErrorCode.STORAGE_ERROR,
                details={"database": self._db_name},
                retryable=False,
            ) from e

    def health_check(self) -> dict:
        """Check MongoDB connection health by pinging the server."""
        try:
            self._client.admin.command("ping")
            return {
                "healthy": True,
                "database": self._db_name,
                "collection": self._collection_name,
            }
        except Exception as e:
            logger.warning("MongoDB health check failed: %s", str(e))
            return {
                "healthy": False,
                "database": self._db_name,
                "error": str(e),
            }

    def list_documents(self, model: str | None = None) -> list[dict[str, Any]]:
        """List all unique documents in storage.

        Args:
            model: Optional embedding model name to filter results.

        Returns:
            List of documents with filename, document_id, chunk_count, model, created_at.
        """
        logger.info("Listing documents from MongoDB (model=%s)", model)

        pipeline = []

        # Add match stage if model filter is provided
        if model:
            pipeline.append({"$match": {"model": model}})

        pipeline.extend([
            {
                "$group": {
                    "_id": {
                        "document_id": "$document_id",
                        "filename": "$filename",
                        "model": "$model",
                    },
                    "chunk_count": {"$sum": 1},
                    "created_at": {"$min": "$created_at"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "document_id": "$_id.document_id",
                    "filename": "$_id.filename",
                    "model": "$_id.model",
                    "chunk_count": 1,
                    "created_at": 1,
                }
            },
            {"$sort": {"created_at": -1}},
        ])

        try:
            results = list(self._collection.aggregate(pipeline))
            logger.info("Listed %d documents", len(results))
            return results
        except ConnectionFailure as e:
            logger.error("MongoDB connection failed: %s", str(e))
            raise StorageError(
                message="MongoDB connection failed",
                code=ErrorCode.STORAGE_CONNECTION,
                details={"database": self._db_name},
                retryable=True,
            ) from e
        except ServerSelectionTimeoutError as e:
            logger.error("MongoDB server selection timeout: %s", str(e))
            raise StorageError(
                message="MongoDB server selection timeout",
                code=ErrorCode.STORAGE_TIMEOUT,
                details={"database": self._db_name},
                retryable=True,
            ) from e
        except PyMongoError as e:
            logger.error("MongoDB error: %s", str(e))
            raise StorageError(
                message=f"MongoDB error: {str(e)}",
                code=ErrorCode.STORAGE_ERROR,
                details={"database": self._db_name},
                retryable=False,
            ) from e

    def list_unique_models(self) -> list[str]:
        """List all unique embedding models used in storage.

        Returns:
            List of unique model names.
        """
        logger.info("Listing unique models from MongoDB")

        try:
            results = self._collection.distinct("model")
            # Filter out None values and sort
            models = sorted([m for m in results if m is not None])
            logger.info("Found %d unique models: %s", len(models), models)
            return models
        except ConnectionFailure as e:
            logger.error("MongoDB connection failed: %s", str(e))
            raise StorageError(
                message="MongoDB connection failed",
                code=ErrorCode.STORAGE_CONNECTION,
                details={"database": self._db_name},
                retryable=True,
            ) from e
        except ServerSelectionTimeoutError as e:
            logger.error("MongoDB server selection timeout: %s", str(e))
            raise StorageError(
                message="MongoDB server selection timeout",
                code=ErrorCode.STORAGE_TIMEOUT,
                details={"database": self._db_name},
                retryable=True,
            ) from e
        except PyMongoError as e:
            logger.error("MongoDB error: %s", str(e))
            raise StorageError(
                message=f"MongoDB error: {str(e)}",
                code=ErrorCode.STORAGE_ERROR,
                details={"database": self._db_name},
                retryable=False,
            ) from e

    def delete_document(self, document_id: str) -> int:
        """Delete all chunks for a document.

        Args:
            document_id: The unique identifier of the document to delete.

        Returns:
            Number of chunks deleted.
        """
        logger.info("Deleting document: %s", document_id)

        try:
            result = self._collection.delete_many({"document_id": document_id})
            logger.info(
                "Deleted %d chunks for document %s",
                result.deleted_count,
                document_id,
            )
            return result.deleted_count
        except ConnectionFailure as e:
            logger.error("MongoDB connection failed: %s", str(e))
            raise StorageError(
                message="MongoDB connection failed",
                code=ErrorCode.STORAGE_CONNECTION,
                details={"database": self._db_name},
                retryable=True,
            ) from e
        except ServerSelectionTimeoutError as e:
            logger.error("MongoDB server selection timeout: %s", str(e))
            raise StorageError(
                message="MongoDB server selection timeout",
                code=ErrorCode.STORAGE_TIMEOUT,
                details={"database": self._db_name},
                retryable=True,
            ) from e
        except PyMongoError as e:
            logger.error("MongoDB error: %s", str(e))
            raise StorageError(
                message=f"MongoDB error: {str(e)}",
                code=ErrorCode.STORAGE_ERROR,
                details={"database": self._db_name},
                retryable=False,
            ) from e

"""Streamlit UI for Document Embedder."""

import uuid

from dotenv import load_dotenv
load_dotenv()

import streamlit as st

# Import modules to register embedders and storages
from embedders import gemini  # noqa: F401
from storages import mongodb  # noqa: F401

from embedders.base import get_embedder, EMBEDDERS
from storages.base import get_storage, STORAGES
from services.document import process_file, SUPPORTED_EXTENSIONS

st.set_page_config(page_title="Document Embedder", layout="wide")
st.title("Document Embedder")

# Get available options
embedder_options = list(EMBEDDERS.keys())
storage_options = list(STORAGES.keys())

tab_upload, tab_search = st.tabs(["Upload & Vectorize", "Search"])

with tab_upload:
    st.header("Upload & Vectorize")

    uploaded_file = st.file_uploader(
        "Choose a file",
        type=[ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS],
    )

    col1, col2 = st.columns(2)
    with col1:
        upload_embedder = st.selectbox(
            "Embedder",
            embedder_options,
            key="upload_embedder",
        )
    with col2:
        upload_storage = st.selectbox(
            "Storage",
            storage_options,
            key="upload_storage",
        )

    if st.button("Upload", disabled=uploaded_file is None):
        if uploaded_file is not None:
            with st.spinner("Processing..."):
                try:
                    # Process file into chunks
                    file_bytes = uploaded_file.read()
                    documents = process_file(file_bytes, uploaded_file.name)

                    if not documents:
                        st.error("No content extracted from file")
                    else:
                        # Extract text chunks and metadata
                        chunks = [doc.content for doc in documents]
                        chunk_metadata = [doc.metadata for doc in documents]

                        # Generate embeddings
                        embedder = get_embedder(upload_embedder)
                        embeddings = embedder.embed_batched(chunks)

                        # Store in storage backend
                        document_id = str(uuid.uuid4())
                        storage = get_storage(upload_storage)
                        chunks_stored = storage.store_embeddings(
                            document_id=document_id,
                            filename=uploaded_file.name,
                            chunks=chunks,
                            embeddings=embeddings,
                            model=upload_embedder,
                            chunk_metadata=chunk_metadata,
                        )

                        # Display raw JSON response
                        response = {
                            "document_id": document_id,
                            "chunks_stored": chunks_stored,
                        }
                        st.subheader("Response")
                        st.json(response)

                except Exception as e:
                    st.error(f"Error: {e}")

with tab_search:
    st.header("Search")

    query = st.text_input("Query")

    col1, col2, col3 = st.columns(3)
    with col1:
        limit = st.slider("Limit", min_value=1, max_value=100, value=5)
    with col2:
        search_embedder = st.selectbox(
            "Embedder",
            embedder_options,
            key="search_embedder",
        )
    with col3:
        search_storage = st.selectbox(
            "Storage",
            storage_options,
            key="search_storage",
        )

    if st.button("Search", disabled=not query.strip()):
        if query.strip():
            with st.spinner("Searching..."):
                try:
                    # Generate query embedding
                    embedder = get_embedder(search_embedder)
                    query_embedding = embedder.embed_query(query)

                    # Search storage backend
                    storage = get_storage(search_storage)
                    results = storage.vector_search(
                        query_embedding=query_embedding,
                        limit=limit,
                    )

                    # Display raw JSON response
                    response = {
                        "query": query,
                        "results": results,
                    }
                    st.subheader("Response")
                    st.json(response)

                except Exception as e:
                    st.error(f"Error: {e}")

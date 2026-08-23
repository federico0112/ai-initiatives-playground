# Document Embedder

Service that uploads documents (PDF, TXT, DOCX), embeds them using configurable models, and stores vectors in MongoDB Atlas.

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service info page |
| `/health` | GET | Health check |
| `/version` | GET | Version info |
| `/upload` | POST | Upload and embed a document |
| `/models` | GET | List available embedding models |

## Upload Endpoint

```bash
curl -X POST http://localhost:8080/upload \
  -F "file=@document.pdf" \
  -F "model=gemini"
```

**Parameters:**
- `file`: Document file (PDF, TXT, or DOCX)
- `model`: (optional) Embedding model, default "gemini"

**Response:**
```json
{
  "document_id": "uuid",
  "chunks_stored": 10
}
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MONGODB_URI` | Yes | MongoDB Atlas connection string |
| `MONGODB_DATABASE` | No | Database name (default: "doc_embedder") |
| `GEMINI_API_KEY` | Yes | Google AI API key for Gemini embeddings |
| `LOG_LEVEL` | No | Logging level: DEBUG, INFO, WARNING, ERROR (default: "INFO") |

### Production Setup (GCP Secret Manager)

Required secrets must be created in GCP Secret Manager before deployment:

```bash
# Create secrets
gcloud secrets create doc-embedder-mongodb-uri --replication-policy="automatic"
gcloud secrets create doc-embedder-gemini-api-key --replication-policy="automatic"

# Add values
echo -n "mongodb+srv://..." | gcloud secrets versions add doc-embedder-mongodb-uri --data-file=-
echo -n "your-api-key" | gcloud secrets versions add doc-embedder-gemini-api-key --data-file=-

# Grant access to Cloud Run service account
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
for SECRET in doc-embedder-mongodb-uri doc-embedder-gemini-api-key; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
```

## Run Locally

```bash
export MONGODB_URI="mongodb+srv://..."
export GEMINI_API_KEY="..."
pip install -r requirements.txt
python app.py
```

## Run Tests

```bash
pytest tests/ -v
```

## Streamlit UI

Run the interactive UI locally:

```bash
streamlit run streamlit_app.py
```

The UI provides two tabs:
- **Upload & Vectorize**: Upload files (PDF, TXT, DOCX), select embedder and storage backends, and view the raw JSON response
- **Search**: Enter queries, adjust result limit, select backends, and view search results

## Docker Deployment

When deployed via Docker, both Flask API and Streamlit UI are served from a single container:

| Path | Service |
|------|---------|
| `/` | Flask API (all endpoints) |
| `/ui` | Streamlit UI |

Build and run:

```bash
docker build -t doc-embedder .
docker run -p 8080:8080 \
  -e MONGODB_URI="mongodb+srv://..." \
  -e GEMINI_API_KEY="..." \
  doc-embedder
```

Access:
- API: `http://localhost:8080/`
- UI: `http://localhost:8080/ui`

## Adding New Embedding Models

1. Create a new file in `embedders/` (e.g., `openai.py`)
2. Implement `BaseEmbedder` interface
3. Use `@register_embedder("model-name")` decorator
4. Import in `app.py` to register

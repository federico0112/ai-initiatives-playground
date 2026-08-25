# AI Initiatives Playground

Monorepo for AI initiative prototypes.

## Prototypes

| Prototype | Description |
|-----------|-------------|
| [hello-world](prototypes/hello-world) | Sample Flask app template |
| [rag-agent](prototypes/rag-agent) | RAG agent with document embedding and chat using MongoDB Atlas vector storage |

## Deployment

Prototypes are automatically deployed to GCP Cloud Run when changes are pushed to `main`. Tests must pass before deployment.

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `GCP_PROJECT_ID` | GCP project ID |
| `GCP_SA_KEY` | Service account JSON key |

### GCP Secret Manager

Prototype-specific secrets are stored in GCP Secret Manager and automatically injected into Cloud Run services during deployment.

**Naming convention:** `<prototype>-<secret-name>` (e.g., `rag-agent-mongodb-uri`)

**Setup secrets:**

```bash
# Create a secret
gcloud secrets create rag-agent-mongodb-uri --replication-policy="automatic"

# Add a version with the secret value
echo -n "mongodb+srv://..." | gcloud secrets versions add rag-agent-mongodb-uri --data-file=-

# Grant Cloud Run service account access
gcloud secrets add-iam-policy-binding rag-agent-mongodb-uri \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

**Per-prototype configuration:** Each prototype defines its secrets in `cloud-run.yaml`:

```yaml
secrets:
  - env: MONGODB_URI           # Environment variable name
    secret: rag-agent-mongodb-uri  # Secret Manager secret name
    required: true

env:
  - name: LOG_LEVEL            # Non-secret environment variable
    value: INFO
```

## Adding a New Prototype

1. Create a folder under `prototypes/`
2. Include `app.py`, `requirements.txt`, `Dockerfile`, and `README.md`
3. Add a `tests/` folder with pytest tests
4. Add `cloud-run.yaml` for secrets/env vars (can be empty if none needed)
5. Update the test matrix in `.github/workflows/test.yml`
6. Add an entry to the table above
7. Create any required secrets in GCP Secret Manager

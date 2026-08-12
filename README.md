# AI Initiatives Playground

Monorepo for AI initiative prototypes.

## Prototypes

| Prototype | Description |
|-----------|-------------|
| [hello-world](prototypes/hello-world) | Sample Flask app template |

## Deployment

Prototypes are automatically deployed to GCP Cloud Run when changes are pushed to `main`. Tests must pass before deployment.

### Required GitHub Secrets

| Secret | Description |
|--------|-------------|
| `GCP_PROJECT_ID` | GCP project ID |
| `GCP_SA_KEY` | Service account JSON key |

## Adding a New Prototype

1. Create a folder under `prototypes/`
2. Include `app.py`, `requirements.txt`, `Dockerfile`, and `README.md`
3. Add a `tests/` folder with pytest tests
4. Update the test matrix in `.github/workflows/test.yml`
5. Add an entry to the table above

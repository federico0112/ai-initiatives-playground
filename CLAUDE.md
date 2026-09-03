# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Monorepo for AI initiative prototypes. Each prototype is a self-contained Flask application deployed to GCP Cloud Run.

## Commands

```bash
# Run a prototype locally
cd prototypes/<name>
pip install -r requirements.txt
python app.py

# Run tests
cd prototypes/<name>
pytest tests/ -v

# Run a single test
cd prototypes/<name>
pytest tests/test_app.py::test_health_endpoint -v

# Build and run Docker image
cd prototypes/<name>
docker build -t <name> .
docker run -p 8080:8080 <name>

# Manual deploy (usually automatic via CI)
# Trigger via GitHub Actions workflow_dispatch
```

## Environment Variables

Some prototypes require environment variables. Check each prototype's README.md for specifics.

Example for rag-agent:
```bash
export MONGODB_URI="mongodb+srv://..."
export GEMINI_API_KEY="..."
export LOG_LEVEL="DEBUG"  # optional
```

## Architecture

```
prototypes/
└── <prototype>/
    ├── app.py           # Flask app with /, /health, /version endpoints
    ├── version.py       # __version__ and __git_sha__ (SHA injected at build)
    ├── requirements.txt
    ├── Dockerfile
    ├── cloud-run.yaml   # Secrets and env vars for Cloud Run deployment
    ├── README.md
    └── tests/
        └── test_app.py
```

## Conventions

- Python 3.11, Flask apps on port 8080
- Every prototype requires: `app.py`, `version.py`, `requirements.txt`, `Dockerfile`, `cloud-run.yaml`, `README.md`, `tests/`
- Required endpoints: `/health` (returns `{"status": "healthy"}`), `/version` (returns version info)
- Tests must pass before Cloud Run deployment
- Secrets go in GCP Secret Manager, referenced via `cloud-run.yaml`

## Versioning

- `version.py` contains `__version__` (semantic, e.g., "0.1.0") and `__git_sha__` (injected at Docker build)
- Docker tags: `<version>-<short-sha>` (deployment), `<version>`, `latest`
- To bump version: edit `__version__` in `version.py`

## Adding a New Prototype

1. Create folder: `mkdir -p prototypes/<name>/tests && touch prototypes/<name>/tests/__init__.py`
2. Copy structure from `prototypes/hello-world/` as template
3. Create `cloud-run.yaml` with required secrets/env vars
4. Create secrets in GCP Secret Manager (naming: `<prototype>-<secret-name>`)
5. Update `.github/workflows/test.yml` matrix to include new prototype
6. Add entry to root `README.md` prototypes table
7. Verify locally: `pytest tests/ -v && python app.py`

## Testing Patterns

Tests use pytest with Flask's test client:

```python
@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
```

## Extensibility Patterns

For pluggable components (see rag-agent), use registry pattern:

```python
# embedders/base.py
EMBEDDERS: dict[str, type] = {}

def register_embedder(name: str):
    def decorator(cls):
        EMBEDDERS[name] = cls
        return cls
    return decorator

def get_embedder(name: str) -> BaseEmbedder:
    return EMBEDDERS[name]()

# embedders/gemini.py
@register_embedder("gemini")
class GeminiEmbedder(BaseEmbedder):
    ...

# app.py - import to register
from embedders import gemini  # noqa: F401
```

## Deployment

- Auto-deploys to Cloud Run on push to `main` when `prototypes/**` changes
- Region: `us-central1`
- Artifact Registry: `us-central1-docker.pkg.dev/<project>/ai-prototypes/`
- Required GitHub secrets: `GCP_PROJECT_ID`, `GCP_SA_KEY`
- Prototype secrets: Stored in GCP Secret Manager, configured via `cloud-run.yaml`

---

## Behavioral Guidelines

### 1. Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

### 3. Surgical Changes
Touch only what you must. Clean up only your own mess.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.
- Remove imports/variables/functions that YOUR changes made unused.
- Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
Define success criteria. Loop until verified.
- Transform tasks into verifiable goals with tests
- For multi-step tasks, state a brief plan with verification steps
- Strong success criteria let you loop independently; weak criteria require clarification

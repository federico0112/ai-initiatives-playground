# AI Initiatives Playground

## Project Structure

```
ai-initiatives-playground/
├── prototypes/           # All prototype applications
│   └── <prototype>/      # Each prototype is self-contained
│       ├── app.py        # Main application (Flask)
│       ├── requirements.txt
│       ├── Dockerfile
│       ├── README.md
│       └── tests/
│           └── test_app.py
├── .github/workflows/
│   ├── deploy.yml        # Cloud Run deployment (requires tests to pass)
│   └── test.yml          # Runs pytest on PR/push
└── README.md
```

## Commands

### Run a prototype locally
```bash
cd prototypes/<name>
pip install -r requirements.txt
python app.py
```

### Run tests
```bash
cd prototypes/<name>
pytest tests/ -v
```

### Build Docker image
```bash
cd prototypes/<name>
docker build -t <name> .
docker run -p 8080:8080 <name>
```

## Conventions

- Each prototype must have: `app.py`, `version.py`, `requirements.txt`, `Dockerfile`, `README.md`, and `tests/` folder
- Flask apps run on port 8080
- All prototypes must include `/health` and `/version` endpoints
- Tests must pass before deployment to Cloud Run
- Use Python 3.11

## Versioning

Each prototype has a `version.py` with:
- `__version__`: Manual semantic version (e.g., "0.1.0")
- `__git_sha__`: Git commit SHA (injected at Docker build time)

Docker images are tagged as:
- `<version>-<short-sha>` (e.g., `0.1.0-abc1234`) - used for deployment
- `<version>` (e.g., `0.1.0`)
- `latest`

To bump version, edit `__version__` in `version.py`.

## Deployment

- Automatic deployment to GCP Cloud Run on push to `main`
- Artifact Registry: `us-central1-docker.pkg.dev/<project>/ai-prototypes/`
- Region: `us-central1`

## Adding a New Prototype

### Step 1: Create the folder structure
```bash
mkdir -p prototypes/<name>/tests
touch prototypes/<name>/tests/__init__.py
```

### Step 2: Create version.py
```python
"""Version information for the <name> prototype."""

__version__ = "0.1.0"
__git_sha__ = "development"

def get_version_info() -> dict:
    return {"version": __version__, "git_sha": __git_sha__}
```

### Step 3: Create app.py
```python
"""<Name> prototype application."""
from flask import Flask
from version import __version__, __git_sha__, get_version_info

app = Flask(__name__)

@app.route("/")
def index():
    return """
    <!DOCTYPE html>
    <html>
    <head><title><Name></title></head>
    <body><h1><Name> Prototype</h1></body>
    </html>
    """

@app.route("/health")
def health():
    return {"status": "healthy"}

@app.route("/version")
def version():
    return get_version_info()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

### Step 4: Create requirements.txt
```
flask==3.0.0
gunicorn==21.2.0
pytest==7.4.3
```

### Step 5: Create Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app

ARG GIT_SHA=development
ENV GIT_SHA=${GIT_SHA}

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN sed -i "s/__git_sha__ = \"development\"/__git_sha__ = \"${GIT_SHA}\"/" version.py

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
```

### Step 6: Create tests/test_app.py
```python
"""Tests for <name> prototype."""
import pytest
from app import app
from version import __version__, get_version_info

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_index_returns_200(client):
    response = client.get("/")
    assert response.status_code == 200

def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "healthy"}

def test_version_endpoint(client):
    response = client.get("/version")
    assert response.status_code == 200
    assert "version" in response.json
    assert "git_sha" in response.json
```

### Step 7: Create README.md
```markdown
# <Name>

Brief description of the prototype.

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `/` | Main page |
| `/health` | Health check |
| `/version` | Version info |

## Run Locally

\`\`\`bash
pip install -r requirements.txt
python app.py
\`\`\`

## Run Tests

\`\`\`bash
pytest tests/ -v
\`\`\`
```

### Step 8: Update test workflow
Edit `.github/workflows/test.yml` and add the prototype to the matrix:
```yaml
strategy:
  matrix:
    prototype: [hello-world, <name>]
```

### Step 9: Update root README
Add entry to the prototypes table in `README.md`:
```markdown
| [<name>](prototypes/<name>) | Brief description |
```

### Step 10: Verify locally
```bash
cd prototypes/<name>
pip install -r requirements.txt
pytest tests/ -v
python app.py
# Visit http://localhost:8080
```

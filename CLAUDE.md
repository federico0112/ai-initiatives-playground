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

1. Create folder: `prototypes/<name>/`
2. Add required files (app.py, requirements.txt, Dockerfile, README.md, tests/)
3. Update `.github/workflows/test.yml` matrix to include new prototype
4. Add entry to root README.md prototypes table

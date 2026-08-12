# AI Initiatives Playground

Monorepo for holding prototypes and initial framework for AI initiatives.

## Structure

```
ai-initiatives-playground/
├── prototypes/
│   └── hello-world/          # Sample prototype
│       ├── app.py            # Flask application
│       ├── requirements.txt  # Python dependencies
│       ├── Dockerfile        # Container configuration
│       └── tests/            # Pytest tests
│           └── test_app.py
├── .github/
│   └── workflows/
│       ├── deploy.yml        # Cloud Run deployment
│       └── test.yml          # Run tests on PR/push
└── README.md
```

## Prototypes

### hello-world

A simple Flask application that displays "Hello World" on a webpage.

**Run locally:**
```bash
cd prototypes/hello-world
pip install -r requirements.txt
python app.py
```

**Run with Docker:**
```bash
cd prototypes/hello-world
docker build -t hello-world .
docker run -p 8080:8080 hello-world
```

**Run tests:**
```bash
cd prototypes/hello-world
pytest tests/ -v
```

## Deployment

Prototypes are automatically deployed to GCP Cloud Run when changes are pushed to the `main` branch.

### Required GitHub Secrets

- `GCP_PROJECT_ID`: Your GCP project ID
- `GCP_SA_KEY`: Service account JSON key with Cloud Run and Artifact Registry permissions

### Manual Deployment

Use the workflow dispatch to manually deploy a specific prototype:
1. Go to Actions > Deploy to Cloud Run
2. Click "Run workflow"
3. Enter the prototype name (e.g., `hello-world`)

## Adding a New Prototype

1. Create a new folder under `prototypes/`
2. Add your application code
3. Include a `Dockerfile` for containerization
4. Add a `tests/` folder with pytest tests
5. Update the test workflow matrix in `.github/workflows/test.yml`

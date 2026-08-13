# Hello World

A simple Flask application that displays "Hello World" on a webpage. This is a template prototype demonstrating the standard structure for AI initiative prototypes.

## Structure

```
hello-world/
├── app.py              # Flask application
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container configuration
├── README.md           # This file
└── tests/
    └── test_app.py     # Pytest tests
```

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `/` | HTML page displaying "Hello World" |
| `/health` | Health check endpoint returning JSON |
| `/version` | Version info (version + git SHA) |

## Versioning

Edit `version.py` to bump the version:
```python
__version__ = "0.1.0"  # Change this for new releases
```

The git SHA is automatically injected during Docker build.

## Run Locally

```bash
pip install -r requirements.txt
python app.py
```

Visit http://localhost:8080

## Run with Docker

```bash
docker build -t hello-world .
docker run -p 8080:8080 hello-world
```

## Run Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Deployment

This prototype is automatically deployed to GCP Cloud Run when changes are pushed to the `main` branch. Tests must pass before deployment.

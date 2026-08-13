"""Simple Hello World Flask application."""
from flask import Flask
from version import __version__, __git_sha__, get_version_info

app = Flask(__name__)


@app.route("/")
def hello():
    """Return a simple HTML page with Hello World."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Hello World</title>
    </head>
    <body>
        <h1>Hello World</h1>
        <p>Welcome to the AI Initiatives Playground!</p>
    </body>
    </html>
    """


@app.route("/health")
def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.route("/version")
def version():
    """Return version information."""
    return get_version_info()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

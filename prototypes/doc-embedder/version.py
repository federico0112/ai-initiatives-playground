"""Version information for the doc-embedder prototype."""

__version__ = "0.1.0"
__git_sha__ = "development"


def get_version_info() -> dict:
    """Return version information."""
    return {
        "version": __version__,
        "git_sha": __git_sha__,
    }

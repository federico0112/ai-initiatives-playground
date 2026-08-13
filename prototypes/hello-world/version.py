"""Version information for the hello-world prototype."""

__version__ = "0.1.0"

# Git commit SHA injected at build time
__git_sha__ = "development"


def get_version_info() -> dict:
    """Return version information."""
    return {
        "version": __version__,
        "git_sha": __git_sha__,
    }

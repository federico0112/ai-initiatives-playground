"""Connector registry: pick a backend per kind of system by config, e.g. tickets=local|jira|salesforce."""

CONNECTORS: dict[tuple[str, str], type] = {}


def register_connector(kind: str, backend: str):
    def decorator(cls):
        CONNECTORS[(kind, backend)] = cls
        return cls

    return decorator


def get_connector(kind: str, backend: str, **kwargs):
    try:
        cls = CONNECTORS[(kind, backend)]
    except KeyError:
        known = sorted(b for k, b in CONNECTORS if k == kind)
        raise KeyError(f"no {kind!r} backend {backend!r}; known: {known}") from None
    return cls(**kwargs)

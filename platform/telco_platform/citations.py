"""Citation IDs: every record an adapter returns carries a stable `sim://` cite_id.

    sim://<system>/<object-type>/<id>[/<sub>...][?query][#fragment]

Real connectors keep the same IDs, so agent statements and ledger entries never change
when a mock is swapped for a real system.
"""

from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit

SCHEME = "sim"


@dataclass(frozen=True)
class Source:
    path: str
    line: int | None = None


@dataclass
class Record:
    cite_id: str
    data: dict
    source: Source | None = None


@dataclass(frozen=True)
class Cite:
    system: str
    object_type: str
    object_id: str
    query: dict = field(default_factory=dict)
    fragment: str | None = None


def make_cite(system: str, object_type: str, object_id: str, fragment: str | None = None, **query) -> str:
    cite = f"{SCHEME}://{system}/{object_type}/{object_id}"
    if query:
        cite += "?" + urlencode(query, safe=":")
    if fragment:
        cite += "#" + fragment
    return cite


def parse_cite(cite_id: str) -> Cite:
    parts = urlsplit(cite_id)
    if parts.scheme != SCHEME or not parts.netloc:
        raise ValueError(f"not a {SCHEME}:// cite: {cite_id}")
    object_type, _, object_id = parts.path.lstrip("/").partition("/")
    if not object_type or not object_id:
        raise ValueError(f"cite needs an object type and id: {cite_id}")
    return Cite(parts.netloc, object_type, object_id, dict(parse_qsl(parts.query)), parts.fragment or None)


class Resolver:
    """Maps each system to the adapter function that turns a cite_id back into its record."""

    def __init__(self):
        self._systems: dict[str, Callable[[Cite], Record]] = {}

    def register(self, system: str, fn: Callable[[Cite], Record]) -> None:
        self._systems[system] = fn

    def resolve(self, cite_id: str) -> Record:
        cite = parse_cite(cite_id)
        if cite.system not in self._systems:
            raise KeyError(f"no adapter registered for system {cite.system!r}")
        return self._systems[cite.system](cite)

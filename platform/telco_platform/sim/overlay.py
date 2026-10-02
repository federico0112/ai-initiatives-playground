"""Scenario data roots: a variant folder holds only the files that differ from base.

`overlay.json` in a variant: {"inherits": "<base folder>", "deleted": [relative paths]}.
To resolve a path, look in the variant, then fall back to the inherited root, unless the
path is listed in `deleted`. `inherits` may be a sibling folder name ("base") or a path
relative to the folder above the scenarios folder ("sim-data/base").
"""

import json
from pathlib import Path


class DataRoot:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.parent: DataRoot | None = None
        self.deleted: set[str] = set()
        overlay = self.root / "overlay.json"
        if overlay.exists():
            o = json.loads(overlay.read_text())
            self.deleted = set(o.get("deleted", []))
            self.parent = DataRoot(self._find_inherited(o["inherits"]))

    def _find_inherited(self, inherits: str) -> Path:
        for base in (self.root.parent, self.root.parent.parent):
            candidate = (base / inherits).resolve()
            if candidate.is_dir() and candidate != self.root.resolve():
                return candidate
        raise FileNotFoundError(f"{self.root}: inherited root {inherits!r} not found")

    def path(self, rel: str) -> Path | None:
        if rel in self.deleted:
            return None
        p = self.root / rel
        if p.exists():
            return p
        return self.parent.path(rel) if self.parent else None

    def require(self, rel: str) -> Path:
        p = self.path(rel)
        if p is None:
            raise FileNotFoundError(f"{rel} not in scenario {self.root.name}")
        return p

    def list(self, rel_dir: str) -> list[str]:
        """Relative paths of files directly under rel_dir, merged across the overlay chain."""
        names = set(self.parent.list(rel_dir)) if self.parent else set()
        d = self.root / rel_dir
        if d.is_dir():
            names |= {f"{rel_dir.rstrip('/')}/{p.name}" for p in d.iterdir()}
        return sorted(n for n in names if n not in self.deleted)

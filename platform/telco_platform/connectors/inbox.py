"""Read-only mailbox: carrier invoices, rate notices, maintenance notices, carrier emails.

A message is either a folder holding `envelope.json` plus attachments
(`invoices/inbox/<id>/envelope.json`) or a single JSON file (`rates/notices/<id>.json`).
Only messages received by `as_of` are visible.
"""

import json
from pathlib import Path

from ..citations import Record, Source, make_cite
from ..sim.overlay import DataRoot
from ..sim.visibility import is_visible
from .base import register_connector


@register_connector("inbox", "local")
class LocalInbox:
    def __init__(self, data_root: DataRoot, folder: str, system: str, object_type: str, time_field: str = "received_at"):
        self.data = data_root
        self.folder = folder.rstrip("/")
        self.system = system
        self.object_type = object_type
        self.time_field = time_field

    def _load(self, rel: str) -> Record | None:
        p = self.data.path(rel)
        if p is None:
            return None
        if p.is_dir():
            env = self.data.path(f"{rel}/envelope.json")
            if env is None:
                return None
            msg_id, src = p.name, env
        elif p.suffix == ".json":
            msg_id, src = p.stem, p
        else:
            return None
        return Record(make_cite(self.system, self.object_type, msg_id), json.loads(src.read_text()), Source(str(src)))

    def list(self, as_of, since=None) -> list[Record]:
        out = []
        for rel in self.data.list(self.folder):
            rec = self._load(rel)
            if rec is None or not is_visible(rec.data, self.time_field, as_of):
                continue
            if since is not None and is_visible(rec.data, self.time_field, since):
                continue
            out.append(rec)
        return sorted(out, key=lambda r: r.data[self.time_field])

    def get(self, message_id: str, as_of) -> Record:
        for rel in (f"{self.folder}/{message_id}", f"{self.folder}/{message_id}.json"):
            rec = self._load(rel)
            if rec is not None:
                if not is_visible(rec.data, self.time_field, as_of):
                    break
                return rec
        raise KeyError(f"{message_id} not in {self.folder} as of {as_of}")

    def attachment(self, message_id: str, name: str) -> Path | None:
        return self.data.path(f"{self.folder}/{message_id}/{name}")

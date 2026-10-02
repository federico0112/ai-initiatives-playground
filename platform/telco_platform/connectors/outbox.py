"""Internal outbox (notes to the account manager, routing or pricing team). Gated like any external send."""

import json
from pathlib import Path

from ..citations import Record, Source, make_cite
from ..gate import ApprovalGate
from .base import register_connector

ACTION = "outbox_send"


@register_connector("outbox", "local")
class LocalOutbox:
    def __init__(self, root: str | Path, gate: ApprovalGate):
        self.root = Path(root) / "outbox"
        self.gate = gate

    def send(self, case_id: str, message: dict, at: str) -> Record:
        """message: {to, subject, body}. Refuses unless an analyst approved this exact message."""
        self.gate.require(case_id, ACTION, message)
        d = self.root / case_id
        d.mkdir(parents=True, exist_ok=True)
        msg_id = f"{case_id}-MSG-{len(list(d.glob('*.json'))) + 1:03d}"
        record = {**message, "message_id": msg_id, "case_id": case_id, "sent_at": at}
        f = d / f"{msg_id}.json"
        f.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        return Record(make_cite("outbox", "message", msg_id), record, Source(str(f)))

    def list(self, case_id: str) -> list[dict]:
        d = self.root / case_id
        return [json.loads(f.read_text()) for f in sorted(d.glob("*.json"))] if d.exists() else []

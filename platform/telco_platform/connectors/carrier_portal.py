"""Carrier-side portal: our dispute claim (prototype one) or escalation ticket (prototype two).

Only an analyst-approved payload can be submitted. Replies are scripted by the scenario:
`submit` tells the runner (`on_submit`), and the runner's `portal_response` handler writes
the carrier's answer at its sim time. The payload schema belongs to each prototype.
"""

import json
from pathlib import Path
from typing import Callable

from ..citations import Record, Source, make_cite
from ..gate import ApprovalGate
from ..sim.clock import iso
from ..sim.visibility import is_visible
from .base import register_connector


@register_connector("carrier_portal", "sim")
class SimCarrierPortal:
    def __init__(self, root: str | Path, gate: ApprovalGate, on_submit: Callable[..., object] | None = None,
                 event_name: str = "portal_submit", system: str = "carrier-portal", id_prefix: str = "CLM"):
        self.root = Path(root) / system
        self.gate = gate
        self.on_submit = on_submit
        self.event_name = event_name
        self.system = system
        self.id_prefix = id_prefix

    def submit(self, case_id: str, payload: dict, at: str) -> Record:
        self.gate.require(case_id, self.event_name, payload)
        subs = self.root / "submissions"
        subs.mkdir(parents=True, exist_ok=True)
        claim_id = f"{self.id_prefix}-{len(list(subs.glob('*.json'))) + 1:05d}"
        record = {**payload, "claim_id": claim_id, "our_reference": case_id, "submitted_at": at}
        f = subs / f"{claim_id}.json"
        f.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        if self.on_submit:
            attrs = {k: v for k, v in payload.items() if isinstance(v, (str, int, float, bool))}
            self.on_submit(self.event_name, **{**attrs, "claim_id": claim_id, "case_id": case_id})
        return Record(make_cite(self.system, "submission", claim_id), record, Source(str(f)))

    def respond(self, claim_id: str, response: dict, at: str) -> Record:
        d = self.root / "responses" / claim_id
        d.mkdir(parents=True, exist_ok=True)
        n = len(list(d.glob("*.json"))) + 1
        record = {**response, "claim_id": claim_id, "responded_at": at}
        f = d / f"{n}.json"
        f.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        return Record(make_cite(self.system, "response", f"{claim_id}/{n}"), record, Source(str(f)))

    def responses(self, claim_id: str, as_of=None) -> list[Record]:
        d = self.root / "responses" / claim_id
        out = []
        for f in sorted(d.glob("*.json"), key=lambda p: int(p.stem)) if d.exists() else []:
            record = json.loads(f.read_text())
            if as_of is None or is_visible(record, "responded_at", as_of):
                out.append(Record(make_cite(self.system, "response", f"{claim_id}/{f.stem}"), record, Source(str(f))))
        return out


def portal_response_handler(portal: SimCarrierPortal):
    """Runner handler for `action: portal_response`: write the scripted reply to the triggering claim."""

    def handle(event: dict, runner) -> None:
        portal.respond(event["trigger"]["claim_id"], event["response"], iso(runner.now))

    return handle

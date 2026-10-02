"""Scenario runner: replays a scenario.yaml against the prototype on a simulated clock.

Event shapes (both prototypes):

    - at: 2026-10-05T09:00:00Z          # fires at a fixed sim time
      action: deliver_invoice
      invoice_id: NB-INV-2026-09-0412

    - on: portal_submit                 # fires only after the prototype emits this event
      to: SUP-NB                        # optional filter, matched against emit(...) attributes
      match: {item: cdr_detail}         # optional filter, same
      after: P2D                        # due = emit time + after
      at: 2026-09-22T19:40:00Z          # optional: due = max(at, emit time + after)
      action: portal_response
      response: {status: acknowledged}

The clock moves only when the prototype waits (`wait`), so a scenario replays in seconds.
Each action is handled by a function the prototype registers: handler(event, runner).
"""

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from .clock import SimClock, iso, parse_duration, parse_time

Handler = Callable[[dict, "Runner"], None]


def load_scenario(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


@dataclass
class _Pending:
    due: datetime
    seq: int
    event: dict


class Runner:
    def __init__(self, scenario: dict, handlers: dict[str, Handler]):
        self.scenario = scenario
        self.handlers = handlers
        self.clock = SimClock(scenario["sim_start"])
        self.history: list[dict] = []
        self._pending: list[_Pending] = []
        self._seq = 0
        events = scenario.get("events") or []
        unknown = sorted({e["action"] for e in events} - set(handlers))
        if unknown:
            raise ValueError(f"no handler for actions: {unknown}")
        self._on = [e for e in events if "on" in e]
        for e in events:
            if "on" not in e:
                self._schedule(parse_time(e["at"]), e)

    @property
    def now(self) -> datetime:
        return self.clock.now()

    @property
    def expected(self) -> dict:
        return self.scenario.get("expected") or {}

    def _schedule(self, due: datetime, event: dict) -> None:
        self._seq += 1
        self._pending.append(_Pending(due, self._seq, event))

    def emit(self, name: str, **attrs) -> list[dict]:
        """The prototype did something a scenario may react to (submitted, requested evidence...)."""
        for e in self._on:
            filters = dict(e.get("match") or {})
            if "to" in e:
                filters["to"] = e["to"]
            if e["on"] != name or any(attrs.get(k) != v for k, v in filters.items()):
                continue
            due = self.now + parse_duration(e["after"]) if "after" in e else self.now
            if "at" in e:
                due = max(due, parse_time(e["at"]))
            self._schedule(due, {**e, "trigger": {"on": name, **attrs}})
        return self.run_due()

    def run_due(self) -> list[dict]:
        fired = []
        while True:
            due = sorted((p for p in self._pending if p.due <= self.now), key=lambda p: (p.due, p.seq))
            if not due:
                return fired
            p = due[0]
            self._pending.remove(p)
            event = {**p.event, "fired_at": iso(self.now)}
            self.handlers[event["action"]](event, self)
            self.history.append(event)
            fired.append(event)

    def next_due(self) -> datetime | None:
        return min((p.due for p in self._pending), default=None)

    def wait(self, until: str | datetime | None = None) -> list[dict]:
        """Advance the clock to `until`, or to the next pending event, firing everything due on the way."""
        target = parse_time(until) if until is not None else self.next_due()
        if target is None:
            return []
        fired = []
        while (nxt := self.next_due()) is not None and nxt <= target:
            self.clock.advance_to(max(nxt, self.now))
            fired += self.run_due()
        self.clock.advance_to(max(target, self.now))
        return fired + self.run_due()


def deliver_file(variant_root: str | Path) -> Handler:
    """Handler for `action: deliver_file`: copy `from` (under the variant) to `path` (in the variant's data root)."""
    root = Path(variant_root)

    def handle(event: dict, runner: Runner) -> None:
        dest = root / event["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / event["from"], dest)

    return handle

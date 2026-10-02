"""As-of visibility: data files hold the whole horizon, adapters only show what existed at sim now.

A record is visible once its own time field has passed. Some fields arrive later than the
record (a delivery receipt after the message); those stay blank until their time passes.
"""

from datetime import datetime

from .clock import parse_time


def is_visible(record: dict, time_field: str, as_of: str | datetime) -> bool:
    value = record.get(time_field)
    return bool(value) and parse_time(value) <= parse_time(as_of)


def visible(records, time_field: str, as_of: str | datetime) -> list[dict]:
    return [r for r in records if is_visible(r, time_field, as_of)]


def mask_pending(record: dict, time_field: str, fields: list[str], as_of: str | datetime) -> dict:
    """Copy of record with `fields` (and time_field) blanked if time_field is still in the future."""
    if is_visible(record, time_field, as_of):
        return dict(record)
    out = dict(record)
    for f in [*fields, time_field]:
        out[f] = ""
    return out

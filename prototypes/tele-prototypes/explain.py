"""Plain-language readings of what step 4 found.

The engine writes each cause as a formal statement that cites its records — that text is the
evidence and must not change. This module reads the same structured fields and says the thing in
ordinary words, so the case can be followed by someone who does not work in carrier billing. The
formal statement stays on the page underneath.
"""
import re
from decimal import Decimal

# What the classification means for money, said as a decision rather than a label.
VERDICTS = {
    "supported": ("We claim this back", "The records prove the charge is wrong, so it goes in the dispute."),
    "carrier_favour": ("We pay this", "The charge is right. Our own records were short, not the carrier's."),
    "unresolved": ("We ask before claiming", "Neither side's records explain this yet, so we request evidence."),
}


def verdict(cause):
    return VERDICTS.get(cause.get("classification"), ("Reviewed", ""))


def explain(cause):
    """One or two plain sentences for a cause, or None when there is nothing better to say."""
    kind = cause.get("exception")
    how = cause.get("classification")
    d = cause.get("dispute") or {}
    ev = cause.get("evidence") or {}
    where = d.get("destination") or cause.get("group") or "this network"

    if kind == "RATE_BEFORE_EFFECTIVE":
        return (
            f"The carrier charged {d.get('billed_rate')} per segment for {where} from "
            f"{d.get('from_date')}. That higher price was announced, but under the contract's notice "
            f"period it does not start until after {d.get('through_date')} — until then the agreed "
            f"price was {d.get('correct_rate')}. Every segment sent in that window was billed at the "
            f"new price too early."
        )
    if kind == "RATE_MISMATCH":
        return (
            f"The carrier charged {d.get('billed_rate')} per segment for {where} between "
            f"{d.get('from_date')} and {d.get('through_date')}. The price list in force for that "
            f"period says {d.get('correct_rate')}."
        )
    if kind == "VOLUME" and how == "carrier_favour":
        binds = ", ".join(ev.get("binds") or []) or "one of our connections"
        return (
            f"The carrier billed {_n(ev.get('segments'))} segments for {where} that our own records do "
            f"not show. They were {_n(ev.get('rejects'))} messages our system failed to process on "
            f"{binds}, so they never reached our totals — but the carrier did carry and deliver them. "
            f"The bill is right and our figure was short."
        )
    if kind == "VOLUME" and how == "supported":
        return (
            f"The carrier billed more segments for {where} than we sent, and the carrier's own "
            f"message-by-message detail does not account for the difference."
        )
    if kind == "VOLUME":
        return (
            f"The carrier's volume for {where} does not match ours, and nothing on either side "
            f"explains the gap yet."
        )
    if kind == "UNMAPPED_LINE":
        return (
            f"This invoice line does not name a network we hold a price for, so none of it can be "
            f"checked against our records."
        )
    if kind == "ROUNDING":
        return f"The line total does not match segments times rate once the contract's rounding rule is applied."
    if kind == "UNBILLED":
        return f"We have traffic to {where} that the invoice does not bill at all."
    if kind == "DUPLICATE":
        return f"The same messages were billed more than once for {where}."
    return None


# Formula shapes worth unpacking. A claim is almost always a difference multiplied by something,
# and written on one line it reads as if money were being subtracted — so the difference is shown
# as its own step, with what it means.
RATE_DIFF = re.compile(r"^(\d+) segments x \(([\d.]+) - ([\d.]+)\)$")
VOLUME_DIFF = re.compile(r"^\((\d+) - (\d+)\) x ([\d.]+)$")
SIMPLE_MULT = re.compile(r"^(\d+) segments x ([\d.]+)$")


def formula_steps(formula, result, unit=""):
    """Breaks a calculation into steps, so the subtraction is obviously a per-unit difference.

    Returns a list of (expression, what it means), or None when the formula already reads plainly.
    """
    f = (formula or "").strip()
    end = f"{_money(result)} {unit}".strip()

    m = RATE_DIFF.match(f)
    if m:
        segs, billed, agreed = m.group(1), Decimal(m.group(2)), Decimal(m.group(3))
        over = billed - agreed
        return [
            (f"{billed} − {agreed} = {_plain(over)}",
             "what the carrier charged per segment, less what the contract says it should have been: "
             "the amount we were overcharged on each one."),
            (f"{_n(segs)} × {_plain(over)} = {end}",
             "that overcharge, on every segment sent while the old price still applied. The rest of "
             "the line was billed correctly and is not claimed."),
        ]

    m = VOLUME_DIFF.match(f)
    if m:
        billed, ours, rate = m.group(1), m.group(2), m.group(3)
        gap = int(billed) - int(ours)
        return [
            (f"{_n(billed)} − {_n(ours)} = {_n(gap)} segments",
             "what the carrier billed, less what our own records show: the segments in question."),
            (f"{_n(gap)} × {rate} = {end}", "those segments at the agreed price."),
        ]

    m = SIMPLE_MULT.match(f)
    if m:
        return [(f"{_n(m.group(1))} × {m.group(2)} = {end}", "segments at the agreed price.")]
    return None


def _plain(d):
    """A decimal without trailing zeros, so 0.0065 does not read as 0.00650000."""
    s = format(d.normalize(), "f")
    return s


def _money(v):
    try:
        return f"{Decimal(str(v)):,.2f}"
    except Exception:
        return str(v)


def _n(v):
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return v if v is not None else "some"

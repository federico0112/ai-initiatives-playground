"""Helpers for the seeded dataset generators: exact decimal rounding, exact splits, deterministic writers.

Writers are byte-stable for a given input (gzip mtime 0, fixed line endings), so a
generator re-run with the same seed produces identical files.
"""

import csv
import gzip
import io
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .clock import iso  # noqa: F401  (re-exported for generators)


def q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        f.write(text)


def csv_text(header, rows) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def write_csv(path: str | Path, header, rows) -> None:
    write_text(path, csv_text(header, rows))


def write_csv_gz(path: str | Path, header, rows) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(csv_text(header, rows).encode())


def write_json(path: str | Path, obj) -> None:
    write_text(path, json.dumps(obj, indent=2) + "\n")


def split_total(rng, total: int, n: int, unit: int = 1, weights=None) -> list[int]:
    """Split total into n parts, each a positive multiple of unit, summing exactly."""
    weights = weights or [rng.uniform(0.85, 1.15) for _ in range(n)]
    s = sum(weights)
    raw = [total * w / s for w in weights]
    parts = [int(r // unit) * unit for r in raw]
    rem = total - sum(parts)
    order = sorted(range(n), key=lambda i: raw[i] - parts[i], reverse=True)
    i = 0
    while rem > 0:
        parts[order[i % n]] += unit
        rem -= unit
        i += 1
    assert sum(parts) == total and min(parts) > 0
    return parts


def masked(number: str, keep: int = 4) -> str:
    """Mask the last `keep` digits of a phone number."""
    return number[:-keep] + "X" * keep

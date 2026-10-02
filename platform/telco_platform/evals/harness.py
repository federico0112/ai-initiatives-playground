"""Compare what a scenario run produced against the scenario's `expected` block or answer key.

Decimal-looking values compare to the cent ("1820.00" == "1820" == Decimal("1820.00")),
so figures written as strings in YAML and computed as Decimals line up.
"""

from decimal import Decimal, InvalidOperation


def _same(expected, actual) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    try:
        return Decimal(str(expected)) == Decimal(str(actual))
    except InvalidOperation:
        return expected == actual


def compare_expected(expected: dict, actual: dict) -> list[str]:
    """Return one line per key that is missing or differs; empty means the run matched."""
    problems = []
    for key, want in expected.items():
        if key not in actual:
            problems.append(f"{key}: expected {want!r}, missing from run")
        elif isinstance(want, dict) and isinstance(actual[key], dict):
            problems += [f"{key}.{p}" for p in compare_expected(want, actual[key])]
        elif not _same(want, actual[key]):
            problems.append(f"{key}: expected {want!r}, got {actual[key]!r}")
    return problems

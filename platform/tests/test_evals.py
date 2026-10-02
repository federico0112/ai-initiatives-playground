from decimal import Decimal

from telco_platform.evals.harness import compare_expected


def test_compare_expected_to_the_cent():
    expected = {"case_opened": True, "supported": "1820.00", "decision": "partial_dispute", "figures": {"unresolved": "0.00"}}
    actual = {"case_opened": True, "supported": Decimal("1820"), "decision": "partial_dispute", "figures": {"unresolved": "0"}}
    assert compare_expected(expected, actual) == []
    assert compare_expected(expected, {**actual, "supported": "1820.01", "case_opened": 1}) == [
        "case_opened: expected True, got 1", "supported: expected '1820.00', got '1820.01'",
        ]
    assert compare_expected({"x": 1}, {}) == ["x: expected 1, missing from run"]

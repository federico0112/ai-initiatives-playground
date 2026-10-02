import pytest

from telco_platform.citations import Record, Resolver, make_cite, parse_cite


def test_make_and_parse_roundtrip_with_fragment():
    cite = make_cite("contracts", "agreement", "AGR-NB-2025-014", fragment="clause-6.3")
    assert cite == "sim://contracts/agreement/AGR-NB-2025-014#clause-6.3"
    p = parse_cite(cite)
    assert (p.system, p.object_type, p.object_id, p.fragment) == ("contracts", "agreement", "AGR-NB-2025-014", "clause-6.3")


def test_parse_nested_id_and_query():
    p = parse_cite("sim://messages/window/MX-ALT/2026-09-22T16:05Z/2026-09-22T17:05Z?supplier=SUP-NB&as_of=2026-09-22T17:10Z")
    assert p.object_id == "MX-ALT/2026-09-22T16:05Z/2026-09-22T17:05Z"
    assert p.query == {"supplier": "SUP-NB", "as_of": "2026-09-22T17:10Z"}


def test_make_cite_with_query_keeps_times_readable():
    assert make_cite("messages", "window", "MX-ALT", as_of="2026-09-22T17:10Z").endswith("?as_of=2026-09-22T17:10Z")


@pytest.mark.parametrize("bad", ["http://x/y/z", "sim://contracts", "sim://contracts/agreement"])
def test_parse_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_cite(bad)


def test_resolver_dispatches_by_system():
    r = Resolver()
    r.register("cases", lambda c: Record(f"sim://cases/case/{c.object_id}", {"id": c.object_id}))
    assert r.resolve("sim://cases/case/CASE-0001").data == {"id": "CASE-0001"}
    with pytest.raises(KeyError):
        r.resolve("sim://rates/deck/X")

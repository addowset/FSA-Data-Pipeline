import datetime as dt
import json
from types import SimpleNamespace

from fsa_pipeline.companies_house import (
    EARLIEST_PLAUSIBLE_INCORPORATION_DATE,
    RecordParseError,
    compute_company_fingerprint,
    compute_date_slices,
    fetch_hits_count,
    hits_from_page_bytes,
    incorporated_date_window,
    items_from_page_bytes,
    parse_company_item,
)

# Shaped per the documented Advanced Search response schema (verified
# against Companies House's own developer docs -- see companies_house.py
# module docstring). No live API key was available to confirm this
# against a real response; this fixture is documentation-derived.
SAMPLE_ITEM_FULL = {
    "company_name": "THE COB KINGS LTD",
    "company_number": "12345678",
    "company_status": "active",
    "company_subtype": None,
    "company_type": "ltd",
    "date_of_creation": "2026-08-10",
    "date_of_cessation": None,
    "kind": "search-results#advanced-search",
    "links": {"company_profile": "/company/12345678"},
    "registered_office_address": {
        "address_line_1": "1 High Street",
        "address_line_2": "Sutton-in-Ashfield",
        "locality": "Nottingham",
        "region": "Nottinghamshire",
        "postal_code": "NG17 3GA",
        "country": "England",
    },
    "sic_codes": ["56101", "56302"],
}

# registered_office_address entirely absent -- documented as a real
# possibility, must not crash the parser.
SAMPLE_ITEM_SPARSE = {
    "company_name": "SPARSE VENTURES LTD",
    "company_number": "87654321",
    "company_status": "active",
    "company_type": "ltd",
    "date_of_creation": "2026-08-11",
    "sic_codes": ["56290"],
}


def test_parse_company_item_full():
    result = parse_company_item(SAMPLE_ITEM_FULL)
    assert result["company_number"] == "12345678"
    assert result["company_name"] == "THE COB KINGS LTD"
    assert result["postal_code"] == "NG17 3GA"
    assert json.loads(result["sic_codes"]) == ["56101", "56302"]


def test_parse_company_item_missing_address_does_not_crash():
    result = parse_company_item(SAMPLE_ITEM_SPARSE)
    assert result["company_number"] == "87654321"
    assert result["address_line_1"] is None
    assert result["postal_code"] is None


def test_parse_company_item_missing_company_number_raises():
    try:
        parse_company_item({"company_name": "No Number Ltd"})
        assert False, "expected RecordParseError"
    except RecordParseError:
        pass


def test_fingerprint_stable_for_identical_input():
    a = parse_company_item(SAMPLE_ITEM_FULL)
    b = dict(a)
    assert compute_company_fingerprint(a) == compute_company_fingerprint(b)


def test_fingerprint_changes_on_status_change():
    a = parse_company_item(SAMPLE_ITEM_FULL)
    b = dict(a, company_status="dissolved")
    assert compute_company_fingerprint(a) != compute_company_fingerprint(b)


def test_fingerprint_ignores_company_number():
    a = parse_company_item(SAMPLE_ITEM_FULL)
    b = dict(a, company_number="00000000")
    assert compute_company_fingerprint(a) == compute_company_fingerprint(b)


def test_hits_and_items_from_page_bytes():
    page = json.dumps({"hits": 42, "items": [SAMPLE_ITEM_FULL, SAMPLE_ITEM_SPARSE]}).encode("utf-8")
    assert hits_from_page_bytes(page) == 42
    assert len(items_from_page_bytes(page)) == 2


def test_hits_from_page_bytes_defaults_to_zero_when_absent():
    page = json.dumps({"items": []}).encode("utf-8")
    assert hits_from_page_bytes(page) == 0


def test_incorporated_date_window_is_trailing():
    config = SimpleNamespace(ch_incorporated_window_days=14)
    incorporated_from, incorporated_to = incorporated_date_window(config, dt.date(2026, 8, 24))
    assert incorporated_from == "2026-08-10"
    assert incorporated_to == "2026-08-24"


# --- deep-pagination workaround (2026-08-29 incident) ---

class _FakeSliceSession:
    """Simulates the hits count for any date range via a caller-supplied
    function, so the bisection algorithm can be tested deterministically
    without real network calls or waiting on real-world date spans."""

    def __init__(self, hits_fn):
        self.hits_fn = hits_fn
        self.calls = []

    def get(self, url, params, timeout):
        from_date = dt.date.fromisoformat(params["incorporated_from"])
        to_date = dt.date.fromisoformat(params["incorporated_to"])
        self.calls.append((from_date, to_date))
        hits = self.hits_fn(from_date, to_date)
        content = json.dumps({"hits": hits, "items": []}).encode("utf-8")
        return SimpleNamespace(content=content, raise_for_status=lambda: None)


def _config_for_slicing(sic_codes=("56101",), page_size=500, timeout=1.0):
    return SimpleNamespace(ch_sic_codes=list(sic_codes), ch_page_size=page_size, ch_timeout_seconds=timeout,
                            ch_advanced_search_url="https://example.invalid/advanced-search/companies")


def test_fetch_hits_count_parses_response():
    session = _FakeSliceSession(hits_fn=lambda f, t: 12345)
    hits = fetch_hits_count(session, _config_for_slicing(), "2026-01-01", "2026-01-31")
    assert hits == 12345


def test_compute_date_slices_single_slice_when_already_safe():
    session = _FakeSliceSession(hits_fn=lambda f, t: 500)
    slices = compute_date_slices(session, _config_for_slicing(), "2026-01-01", "2026-01-31")
    assert slices == [("2026-01-01", "2026-01-31", 500)]


def test_compute_date_slices_returns_empty_for_zero_hits():
    session = _FakeSliceSession(hits_fn=lambda f, t: 0)
    slices = compute_date_slices(session, _config_for_slicing(), "2026-01-01", "2026-01-31")
    assert slices == []


def test_compute_date_slices_bisects_until_safe():
    # Any multi-day range reports way over the ceiling; a single day is
    # safe -- forces recursion all the way down to daily slices.
    def hits_fn(f, t):
        return 100_000 if (t - f).days > 0 else 50

    session = _FakeSliceSession(hits_fn)
    slices = compute_date_slices(session, _config_for_slicing(), "2026-01-01", "2026-01-04")

    assert len(slices) == 4
    assert all(hits == 50 for _, _, hits in slices)
    assert sorted(f for f, _, _ in slices) == ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]


def test_compute_date_slices_covers_full_range_with_no_gaps_or_overlaps():
    def hits_fn(f, t):
        return 100_000 if (t - f).days > 1 else 50  # bisect down to 2-day slices

    session = _FakeSliceSession(hits_fn)
    slices = compute_date_slices(session, _config_for_slicing(), "2026-01-01", "2026-01-08")

    slices_sorted = sorted(slices, key=lambda s: s[0])
    # Each slice's day-after-"to" should be the next slice's "from".
    for (_, to_str, _), (next_from_str, _, _) in zip(slices_sorted, slices_sorted[1:]):
        to_date = dt.date.fromisoformat(to_str)
        next_from = dt.date.fromisoformat(next_from_str)
        assert next_from == to_date + dt.timedelta(days=1)

    assert slices_sorted[0][0] == "2026-01-01"
    assert slices_sorted[-1][1] == "2026-01-08"


def test_compute_date_slices_none_bounds_use_defaults():
    session = _FakeSliceSession(hits_fn=lambda f, t: 100)
    slices = compute_date_slices(session, _config_for_slicing(), None, None)

    assert len(slices) == 1
    from_str, to_str, hits = slices[0]
    assert from_str == EARLIEST_PLAUSIBLE_INCORPORATION_DATE.isoformat()
    assert to_str == dt.date.today().isoformat()
    assert hits == 100

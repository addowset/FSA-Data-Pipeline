import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import export_venue_feed as evf


def _csv_row(**overrides):
    row = {
        "fhrsid": "123", "authority_name": "Bristol", "business_name": "Cafe", "address": "1 High St",
        "postcode": "BS1 1AA", "address_completeness": "full", "business_type": "Restaurant/Cafe/Canteen",
        "rating_status": "Awaiting Inspection", "first_seen_date": "2026-10-01", "classification": "NEW_VENUE",
        "confidence": "HIGH", "reason": "r", "previous_business_name": "", "previous_last_seen": "",
        "match_status": "Yes", "company_name": "CAFE LTD", "company_number": "1", "company_status": "active",
        "incorporation_date": "2026-09-01", "sic_codes": "56102", "fhrs_url": "u", "google_maps_url": "g",
        "companies_house_url": "c",
    }
    row.update(overrides)
    return row


def test_row_mapping_types():
    feed = evf.csv_row_to_feed_row(_csv_row())
    assert feed["id"] == 123
    assert feed["ms"] is True
    assert evf.csv_row_to_feed_row(_csv_row(match_status="No"))["ms"] is False


def test_batch_flag_and_older_csvs_without_the_column():
    assert evf.csv_row_to_feed_row(_csv_row(batch_publication="Yes"))["bp"] is True
    assert evf.csv_row_to_feed_row(_csv_row(batch_publication="No"))["bp"] is False
    assert evf.csv_row_to_feed_row(_csv_row())["bp"] is False


def test_build_page_splices_and_escapes():
    template = f"<p>{evf.SOURCE_PLACEHOLDER}</p><script>const D = {evf.DATA_PLACEHOLDER}; const G = \"{evf.DATE_PLACEHOLDER}\";</script>"
    rows = [evf.csv_row_to_feed_row(_csv_row(business_name="</script><b>x"))]
    page = evf.build_page(template, rows, "2026-10-07", "venues_2026-10-07.csv")
    assert page.count("</script>") == 1
    assert "venues_2026-10-07.csv" in page and '"2026-10-07"' in page
    data = page.split("/*__DATA__*/")[1]
    assert json.loads(data)[0]["n"] == "</script><b>x"


def test_build_page_rejects_bad_template():
    with pytest.raises(ValueError):
        evf.build_page("no placeholders", [], "2026-10-07", "x.csv")


def test_real_template_has_each_placeholder_once():
    template = evf.TEMPLATE_PATH.read_text(encoding="utf-8")
    evf.build_page(template, [], "2026-10-07", "venues_2026-10-07.csv")


def test_find_latest_csv_uses_filename_date_and_falls_back(tmp_path):
    preferred, fallback = tmp_path / "Venue CSV", tmp_path / "exports"
    preferred.mkdir()
    fallback.mkdir()
    (fallback / "venues_2026-10-07.csv").write_text("")
    assert evf.find_latest_csv([preferred, fallback]).name == "venues_2026-10-07.csv"
    for name in ("venues_2026-10-01.csv", "venues_2026-09-15.csv", "venues_notes.csv"):
        (preferred / name).write_text("")
    assert evf.find_latest_csv([preferred, fallback]).name == "venues_2026-10-01.csv"

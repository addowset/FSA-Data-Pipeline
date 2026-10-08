import dataclasses
from pathlib import Path

import pytest

from fsa_pipeline.alerting import (
    MissingCredentials,
    build_status_email,
    get_smtp_credentials,
)
from fsa_pipeline.config import Config


def make_config(**overrides) -> Config:
    base = Config(
        authorities_url="https://example.invalid", raw_dir=Path("raw/fhrs"), log_dir=Path("logs"),
        request_delay_seconds=0.0, timeout_seconds=1.0, max_retries=1, backoff_factor=1.0,
        contact_email="test@example.invalid", db_path=Path("unused.db"),
        reupload_ratio_threshold=3.0, reupload_ratio_min_floor=10, reupload_absolute_threshold=150,
        reupload_min_history_days=5, median_window_days=28,
        reissue_overlap_ratio=0.5,
        ch_advanced_search_url="https://example.invalid/advanced-search/companies",
        ch_raw_dir=Path("raw/companies-house"), ch_sic_codes=["56101"], ch_incorporated_window_days=14,
        ch_page_size=500, ch_request_delay_seconds=0.0, ch_timeout_seconds=1.0, ch_max_retries=1,
        ch_backoff_factor=1.0, high_density_address_threshold=5, candidates_per_match=5,
        national_match_threshold=0.9,
        live_establishments_url="https://example.invalid/Establishments", live_raw_dir=Path("raw/fhrs-live"),
        live_page_size=5000, live_request_delay_seconds=0.0, live_timeout_seconds=1.0, live_max_retries=1,
        live_backoff_factor=1.0, postcode_recheck_after_days=30,
        new_venue_high_threshold=0.85, new_venue_medium_threshold=0.6,
        new_venue_max_incorporation_age_days=180, address_history_fallback_threshold=0.9,
        multi_venue_company_threshold=5,
        new_venue_late_incorporation_window_days=90, unknown_recheck_window_days=90,
        officer_churn_enabled=False, officer_churn_window_days=60, officer_churn_request_delay_seconds=0.0,
        operator_search_recheck_after_days=30,
        monitoring_staleness_ratio=3.0, monitoring_staleness_min_age_days=10,
        monitoring_staleness_fallback_absolute_days=30, monitoring_cadence_min_history=3,
        monitoring_record_count_deviation_ratio=0.5, monitoring_record_count_median_window_days=28,
        monitoring_record_count_min_history_days=5, monitoring_record_count_min_floor=10,
        monitoring_max_skipped_record_ratio=0.01,
        export_output_dir=Path("exports"),
        alerting_enabled=True, alerting_recipient_email="andy@example.invalid",
        alerting_smtp_host="smtp.example.invalid", alerting_smtp_port=587,
    )
    return dataclasses.replace(base, **overrides)


def test_subject_reports_not_started(tmp_path):
    config = make_config()
    message = build_status_email(config, "2026-10-01", started=False, completed=False, alerts_path=tmp_path / "_alerts_2026-10-01.log")

    assert "NOT STARTED" in message["Subject"]
    assert message["To"] == "andy@example.invalid"
    assert "Collection started: no" in message.get_content()


def test_subject_reports_started_not_complete(tmp_path):
    config = make_config()
    message = build_status_email(config, "2026-10-01", started=True, completed=False, alerts_path=tmp_path / "_alerts_2026-10-01.log")

    assert "not yet complete" in message["Subject"]
    assert "Collection started: yes" in message.get_content()
    assert "Run completed: no" in message.get_content()


def test_subject_reports_completed(tmp_path):
    config = make_config()
    message = build_status_email(config, "2026-10-01", started=True, completed=True, alerts_path=tmp_path / "_alerts_2026-10-01.log")

    assert message["Subject"] == "FSA pipeline 2026-10-01: completed"


def test_no_attachment_when_no_alerts_file(tmp_path):
    config = make_config()
    message = build_status_email(config, "2026-10-01", started=True, completed=True, alerts_path=tmp_path / "_alerts_2026-10-01.log")

    assert message.is_multipart() is False
    assert "No alerts logged today" in message.get_content()


def test_attaches_alerts_log_when_present(tmp_path):
    config = make_config()
    alerts_path = tmp_path / "_alerts_2026-10-01.log"
    alerts_path.write_text("--- run at x ---\nSTALE EXTRACT DATE: 551: something\nCANARY: something else\n", encoding="utf-8")

    message = build_status_email(config, "2026-10-01", started=True, completed=True, alerts_path=alerts_path)

    assert message.is_multipart() is True
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "_alerts_2026-10-01.log"
    assert "2 alert line(s)" in message.get_body(("plain",)).get_content()


def test_missing_credentials_raises_with_helpful_message(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)

    with pytest.raises(MissingCredentials, match="App Password"):
        get_smtp_credentials()


def test_credentials_read_from_environment(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "me@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "abcd efgh ijkl mnop")

    user, password = get_smtp_credentials()

    assert user == "me@gmail.com"
    assert password == "abcd efgh ijkl mnop"

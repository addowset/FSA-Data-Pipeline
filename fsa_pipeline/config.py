"""Loads config.toml and resolves paths relative to the project root."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"
DOTENV_PATH = PROJECT_ROOT / ".env"


def load_dotenv(path: Path = DOTENV_PATH) -> None:
    """Loads KEY=VALUE lines from .env into the environment, for secrets
    like COMPANIES_HOUSE_API_KEY. Never overwrites a variable already set
    in the real environment -- .env is a local convenience, not an
    override. Silently does nothing if the file doesn't exist (most
    developers running this project won't have one)."""
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    authorities_url: str
    raw_dir: Path
    log_dir: Path
    request_delay_seconds: float
    timeout_seconds: float
    max_retries: int
    backoff_factor: float
    contact_email: str
    db_path: Path
    reupload_ratio_threshold: float
    reupload_ratio_min_floor: int
    reupload_absolute_threshold: int
    reupload_min_history_days: int
    median_window_days: int
    ch_advanced_search_url: str
    ch_raw_dir: Path
    ch_sic_codes: list[str]
    ch_incorporated_window_days: int
    ch_page_size: int
    ch_request_delay_seconds: float
    ch_timeout_seconds: float
    ch_max_retries: int
    ch_backoff_factor: float
    high_density_address_threshold: int
    candidates_per_match: int
    national_match_threshold: float
    live_establishments_url: str
    live_raw_dir: Path
    live_page_size: int
    live_request_delay_seconds: float
    live_timeout_seconds: float
    live_max_retries: int
    live_backoff_factor: float
    postcode_recheck_after_days: int
    new_venue_high_threshold: float
    new_venue_medium_threshold: float
    new_venue_max_incorporation_age_days: int
    address_history_fallback_threshold: float
    multi_venue_company_threshold: int
    officer_churn_enabled: bool
    officer_churn_window_days: int
    officer_churn_request_delay_seconds: float
    operator_search_recheck_after_days: int

    @property
    def user_agent(self) -> str:
        return f"FSA-Data-Pipeline/0.1 (contact: {self.contact_email})"


def load_config(path: Path = CONFIG_PATH) -> Config:
    load_dotenv()

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    fhrs = raw["fhrs"]
    contact = raw["contact"]
    database = raw["database"]
    diffing = raw["diffing"]
    companies_house = raw["companies_house"]
    matching = raw["matching"]
    postcode_backfill = raw["postcode_backfill"]
    classification = raw["classification"]
    officer_churn = raw["officer_churn"]
    operator_search = raw["operator_search"]

    return Config(
        authorities_url=fhrs["authorities_url"],
        raw_dir=PROJECT_ROOT / fhrs["raw_dir"],
        log_dir=PROJECT_ROOT / fhrs["log_dir"],
        request_delay_seconds=float(fhrs["request_delay_seconds"]),
        timeout_seconds=float(fhrs["timeout_seconds"]),
        max_retries=int(fhrs["max_retries"]),
        backoff_factor=float(fhrs["backoff_factor"]),
        contact_email=contact["email"],
        db_path=PROJECT_ROOT / database["path"],
        reupload_ratio_threshold=float(diffing["reupload_ratio_threshold"]),
        reupload_ratio_min_floor=int(diffing["reupload_ratio_min_floor"]),
        reupload_absolute_threshold=int(diffing["reupload_absolute_threshold"]),
        reupload_min_history_days=int(diffing["reupload_min_history_days"]),
        median_window_days=int(diffing["median_window_days"]),
        ch_advanced_search_url=companies_house["advanced_search_url"],
        ch_raw_dir=PROJECT_ROOT / companies_house["raw_dir"],
        ch_sic_codes=list(companies_house["sic_codes"]),
        ch_incorporated_window_days=int(companies_house["incorporated_window_days"]),
        ch_page_size=int(companies_house["page_size"]),
        ch_request_delay_seconds=float(companies_house["request_delay_seconds"]),
        ch_timeout_seconds=float(companies_house["timeout_seconds"]),
        ch_max_retries=int(companies_house["max_retries"]),
        ch_backoff_factor=float(companies_house["backoff_factor"]),
        high_density_address_threshold=int(matching["high_density_address_threshold"]),
        candidates_per_match=int(matching["candidates_per_match"]),
        national_match_threshold=float(matching["national_match_threshold"]),
        live_establishments_url=postcode_backfill["establishments_url"],
        live_raw_dir=PROJECT_ROOT / postcode_backfill["raw_dir"],
        live_page_size=int(postcode_backfill["page_size"]),
        live_request_delay_seconds=float(postcode_backfill["request_delay_seconds"]),
        live_timeout_seconds=float(postcode_backfill["timeout_seconds"]),
        live_max_retries=int(postcode_backfill["max_retries"]),
        live_backoff_factor=float(postcode_backfill["backoff_factor"]),
        postcode_recheck_after_days=int(postcode_backfill["recheck_after_days"]),
        new_venue_high_threshold=float(classification["new_venue_high_threshold"]),
        new_venue_medium_threshold=float(classification["new_venue_medium_threshold"]),
        new_venue_max_incorporation_age_days=int(classification["new_venue_max_incorporation_age_days"]),
        address_history_fallback_threshold=float(classification["address_history_fallback_threshold"]),
        multi_venue_company_threshold=int(classification["multi_venue_company_threshold"]),
        officer_churn_enabled=bool(officer_churn["enabled"]),
        officer_churn_window_days=int(officer_churn["window_days"]),
        officer_churn_request_delay_seconds=float(officer_churn["request_delay_seconds"]),
        operator_search_recheck_after_days=int(operator_search["recheck_after_days"]),
    )

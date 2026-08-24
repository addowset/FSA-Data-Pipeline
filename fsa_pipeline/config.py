"""Loads config.toml and resolves paths relative to the project root."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.toml"


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

    @property
    def user_agent(self) -> str:
        return f"FSA-Data-Pipeline/0.1 (contact: {self.contact_email})"


def load_config(path: Path = CONFIG_PATH) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    fhrs = raw["fhrs"]
    contact = raw["contact"]
    database = raw["database"]

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
    )

"""Computes an aggregate "officer churn" signal for a company from its
Companies House officer list.

See config.toml's [officer_churn] section for why this exists: as built,
OPERATOR_CHANGE only detects venue turnover (a different FHRS record
previously existed at this address), not a genuine change of legal
control -- the two look identical without officer data, which the
brief's "no named individuals" rule otherwise keeps this project away
from entirely. This module is the one deliberate, narrow exception,
opt-in and disabled by default.

compute_officer_churn_signal is the privacy boundary of this whole
feature: it is the ONLY function permitted to read a raw officer list
(fsa_pipeline.companies_house.fetch_officers), and it reads only
appointed_on and resigned_on from each item -- never name, date_of_birth,
nationality, or address, even though those fields are present in the
input. Its return value (three numbers; no individual is identifiable
from them) is what scripts/check_officer_churn.py persists. The input
list itself must never be stored or logged by any caller -- see
fetch_officers's docstring.
"""

from __future__ import annotations

import datetime as dt


def compute_officer_churn_signal(officers: list[dict], event_date: str, window_days: int) -> dict:
    """officers: raw items from fetch_officers (only appointed_on/
    resigned_on are read). event_date: the OPERATOR_CHANGE event's
    first-seen date. Returns {officer_count, appointed_near_event,
    resigned_near_event} -- aggregate only, safe to persist."""
    event = dt.date.fromisoformat(event_date)
    appointed_near = False
    resigned_near = False

    for officer in officers:
        if _within_window(officer.get("appointed_on"), event, window_days):
            appointed_near = True
        if _within_window(officer.get("resigned_on"), event, window_days):
            resigned_near = True

    return {
        "officer_count": len(officers),
        "appointed_near_event": appointed_near,
        "resigned_near_event": resigned_near,
    }


def _within_window(date_str: str | None, event: dt.date, window_days: int) -> bool:
    if not date_str:
        return False
    try:
        d = dt.date.fromisoformat(date_str)
    except ValueError:
        return False
    return abs((event - d).days) <= window_days

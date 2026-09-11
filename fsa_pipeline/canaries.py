"""A handful of establishments that must always parse to known values,
per the brief's "Include a handful of canary FHRSIDs that must always
parse to known values."

Picked 2026-09-11: real FHRSIDs present since the archive's first day
(2026-08-20), spread across five different authorities (England and
Scotland) so a single authority's own data quirks can't produce a false
"everything's fine". Only stable identity fields are checked --
FHRSID, authority_code, business_name, local_authority_business_id --
never anything that legitimately changes over time (RatingValue,
RatingDate, address). A canary mismatch means the parser or the
establishments_current row shape broke, not that a real business
changed something.

If a canary genuinely closes or is re-registered under a new FHRSID in
real life, replace it here rather than deleting the check -- see
scripts/monitor_pipeline.py for how these are checked.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Canary:
    fhrsid: int
    authority_code: str
    expected_business_name: str
    expected_local_authority_business_id: str


CANARIES = [
    Canary(4, "118", "Hullbridge Sports Association", ".EH/00003422"),
    Canary(5, "136", "The Elvetham Hotel", "/04910/2005/2/000"),
    Canary(13, "778", "Spinnaker Hotel", "00/00001/COMM"),
    Canary(69, "127", "Salvation Army Citadel (Mon & Thurs Only)", "00/00073/FOOD"),
    Canary(552, "062", "Alfreton Cricket Club", "0000030/FH"),
]

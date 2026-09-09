#!/usr/bin/env python
"""Exports classifications + evidence + ground-truth cross-reference as
a compact JSON file, for the local audit tool (a published Artifact --
see the user's browser tool, no permanent file in this repo).

Reads only from the database and, optionally, a ground-truth CSV (the
REAL_MATCH_FOUND(y/n) / REAL_COMPANY_NUMBER / NOTES columns the user
fills in by hand while spot-checking -- see ground_truth_sample_v4.csv
and its predecessors). Never writes to the database.

Field names are deliberately short (single/double letters) since this
gets embedded directly into the audit tool's HTML and multiplied by
several thousand rows -- see the tool for the full key.

Usage:
    python scripts/export_audit_data.py [--ground-truth PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline.config import load_config


def load_ground_truth(path: Path) -> dict[int, dict]:
    """Every FHRSID in the ground-truth CSV is flagged (gt: {...}), even
    with rmf/rcn/notes still blank -- the tool needs to filter to "is
    this a ground-truth row", not just "has a human filled it in"."""
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    result = {}
    for r in rows:
        fhrsid = int(r["fhrsid"])
        result[fhrsid] = {
            "rmf": (r.get("REAL_MATCH_FOUND(y/n)") or "").strip(),
            "rcn": (r.get("REAL_COMPANY_NUMBER") or "").strip(),
            "notes": (r.get("NOTES") or "").strip(),
        }
    return result


def run(ground_truth_path: Path, output_path: Path) -> int:
    config = load_config()
    conn = sqlite3.connect(config.db_path)

    ground_truth = load_ground_truth(ground_truth_path)

    authorities = dict(conn.execute("SELECT code, name FROM authorities").fetchall())

    classifications = conn.execute(
        """
        SELECT c.fhrsid, c.authority_code, c.classification, c.confidence, c.reason, c.recently_incorporated,
               e.business_name, e.address_line_1, COALESCE(e.post_code, e.postcode_from_live_api),
               e.first_seen_date
        FROM classifications c
        JOIN establishments_current e ON e.fhrsid = c.fhrsid
        """
    ).fetchall()

    # "d"/"n"/"op" -- this used to be a 2-way n/d split, predating
    # match_strategy="operator-prefix" (2026-09-09), which silently
    # collapsed into "d" (district) until fixed here 2026-09-10.
    strategy_codes = {"district": "d", "national": "n", "operator-prefix": "op"}

    candidates_by_fhrsid: dict[int, list] = {}
    for fhrsid, rank, company_name, company_number, score, strategy, high_density, date_of_creation, address_match in conn.execute(
        """
        SELECT fhrsid, rank, company_name, company_number, name_similarity_score,
               match_strategy, is_high_density_address, date_of_creation, address_matches_establishment
        FROM company_match_candidates
        WHERE rank <= 3
        ORDER BY fhrsid, rank
        """
    ).fetchall():
        candidates_by_fhrsid.setdefault(fhrsid, []).append({
            "n": company_name, "no": company_number, "s": score,
            "st": strategy_codes.get(strategy, "d"),
            "hd": bool(high_density), "am": bool(address_match),
            "dc": date_of_creation,
        })

    rows = []
    for fhrsid, authority_code, classification, confidence, reason, recently_incorporated, business_name, address_line_1, postcode, first_seen_date in classifications:
        row = {
            "id": fhrsid,
            "n": business_name,
            "a": address_line_1,
            "pc": postcode,
            "au": authorities.get(authority_code, authority_code),
            "fs": first_seen_date,
            "cl": classification,
            "cf": confidence,
            "rs": reason,
            "ri": None if recently_incorporated is None else bool(recently_incorporated),
            "cand": candidates_by_fhrsid.get(fhrsid, []),
        }
        if fhrsid in ground_truth:
            row["gt"] = ground_truth[fhrsid]
        rows.append(row)

    output_path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {len(rows)} rows ({output_path.stat().st_size / 1_048_576:.2f} MB) to {output_path}")
    print(f"  {len(ground_truth)} ground-truth rows with notes/verification cross-referenced")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", default="ground_truth_sample_v4.csv", help="Ground-truth CSV to cross-reference")
    parser.add_argument("--output", default="audit_data.json", help="Output JSON path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.exit(run(Path(args.ground_truth), Path(args.output)))


if __name__ == "__main__":
    main()

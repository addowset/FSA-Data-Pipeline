#!/usr/bin/env python
"""Re-evaluates every currently-quarantined diff run under the re-issue
test (fsa_pipeline/diff_engine.py's compute_reissue_overlap and
classify_flagged_run), added 2026-10-08. A run that is a genuine re-issue
stays quarantined; one that isn't becomes a batch_publication and its
INSERT events are un-quarantined, so the CSV export and metrics pick them
up. Reads each run's INSERT/DELETE fhrsids from diff_events, so it needs
no raw files or network.

Dry-run by default: prints what would change. --apply writes it.
Idempotent: a second --apply finds nothing left to change.

Does not touch classifications -- quarantined events were already matched
and classified (the matcher and classifier never filtered on quarantine),
so un-quarantining is enough to surface them.

Usage:
    python scripts/reevaluate_quarantines.py [--apply]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db
from fsa_pipeline.config import load_config
from fsa_pipeline.diff_engine import apply_run_flags, classify_flagged_run, compute_reissue_overlap
from fsa_pipeline.logging_utils import setup_logger


def run(apply: bool) -> int:
    config = load_config()
    logger = setup_logger("reevaluate_quarantines", config.log_dir / "reevaluate_quarantines.log")
    conn = db.connect(config.db_path)

    runs = conn.execute(
        "SELECT d.authority_code, a.name, d.collection_date, d.insert_count, d.delete_count, d.quarantine_reason "
        "FROM diff_runs d JOIN authorities a ON a.code = d.authority_code "
        "WHERE d.quarantined = 1 ORDER BY d.collection_date, d.authority_code"
    ).fetchall()
    logger.info("%d quarantined run(s) to re-evaluate (%s)", len(runs), "APPLY" if apply else "dry-run")

    changed = 0
    for authority_code, name, date, inserts, deletes, old_reason in runs:
        insert_ids = [r[0] for r in conn.execute(
            "SELECT fhrsid FROM diff_events WHERE authority_code = ? AND collection_date = ? AND event_type = 'INSERT'",
            (authority_code, date))]
        delete_ids = [r[0] for r in conn.execute(
            "SELECT fhrsid FROM diff_events WHERE authority_code = ? AND collection_date = ? AND event_type = 'DELETE'",
            (authority_code, date))]

        overlap = compute_reissue_overlap(conn, authority_code, insert_ids, delete_ids)
        # The old reason is the size trigger's own text ("insert_count=N exceeds ...").
        quarantined, batch, reason = classify_flagged_run(overlap, old_reason.split(";")[0], config)
        verdict = "stays QUARANTINED" if quarantined else "-> BATCH PUBLICATION (un-quarantined)"
        logger.info("%s %s %s: %d inserts / %d deletes, overlap %.1f%% %s",
                    authority_code, name, date, inserts, deletes, 100 * overlap, verdict)

        if not quarantined:
            changed += 1
        if apply:  # also records the measured overlap on runs whose verdict is unchanged
            apply_run_flags(conn, authority_code, date, quarantined, batch, reason, overlap)

    logger.info("%d run(s) %s", changed, "un-quarantined as batch publication" if apply else "would be un-quarantined (re-run with --apply)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write the changes (default is a dry run)")
    return parser.parse_args()


def main() -> None:
    sys.exit(run(parse_args().apply))


if __name__ == "__main__":
    main()

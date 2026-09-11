#!/usr/bin/env python
"""Stage 6 of the brief: reports metrics and runs the monitoring checks.

Reads only from the database, plus that day's raw/fhrs/<date>/_manifest.json
if present (to tell a genuine fetch failure apart from an authority that
was simply never reached -- see fsa_pipeline/monitoring.py's module
docstring). Never touches the network.

Writes two logs:
- logs/metrics_<date>.log -- the brief's "Metrics I need reported",
  overwritten each run (not a log to tail, a report to read).
- logs/_alerts_<date>.log -- only written to when at least one check
  actually fires, appended across multiple runs on the same day, same
  format as classify_insertions.py's _review_queue_<date>.log.

Always exits 0. An alert firing means something is worth a look, not
that this script failed -- same treatment as the review queue. Run last
in the daily pipeline (after classify_insertions.py) so metrics reflect
the day's fully-classified state.

Usage:
    python scripts/monitor_pipeline.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import db, monitoring
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger

MANIFEST_FILENAME = "_manifest.json"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_manifest(config, date_str: str) -> dict | None:
    path = config.raw_dir / date_str / MANIFEST_FILENAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_metrics(config, conn, date_str: str) -> Path:
    week_start = (dt.date.fromisoformat(date_str) - dt.timedelta(days=6)).isoformat()
    lines = [f"--- metrics as of {date_str} (trailing 7 days: {week_start} to {date_str}) ---", ""]

    lines.append("New records per authority:")
    new_records = monitoring.new_records_per_authority(conn, week_start, date_str)
    for code, count in sorted(new_records.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {code}: {count}")
    lines.append(f"  TOTAL: {sum(new_records.values())}")
    lines.append("")

    lines.append("Classification ratio (classification/confidence -> count):")
    ratio = monitoring.classification_ratio(conn, week_start, date_str)
    for key, count in sorted(ratio.items()):
        lines.append(f"  {key}: {count}")
    lines.append("")

    lines.append("ExtractDate age, oldest 15 authorities:")
    ages = monitoring.extract_date_ages(conn, date_str)
    known = sorted((t for t in ages if t[2] is not None), key=lambda t: -t[2])
    for code, name, age in known[:15]:
        lines.append(f"  {code} ({name}): {age}d")
    never_collected = [f"{code} ({name})" for code, name, age in ages if age is None]
    if never_collected:
        lines.append(f"  never successfully collected: {', '.join(never_collected)}")
    lines.append("")

    failed, attempted = monitoring.parse_failure_rate(conn, date_str)
    rate = failed / attempted if attempted else 0.0
    lines.append(f"Parse failure rate today ({date_str}): {failed}/{attempted} ({rate:.1%})")

    metrics_path = config.log_dir / f"metrics_{date_str}.log"
    metrics_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return metrics_path


def collect_alerts(config, conn, date_str: str) -> list[str]:
    alerts: list[str] = []

    manifest = load_manifest(config, date_str)
    for code, name, reason in monitoring.check_missing_authorities(conn, date_str, manifest):
        alerts.append(f"MISSING COLLECTION: {code} ({name}): {reason}")

    # Both staleness and record-count deviation are scoped to authorities
    # actually collected on date_str, not every known authority. An
    # authority not yet collected today (still pending later in the day,
    # or this script invoked mid-run) is already covered by the missing-
    # collection check above -- re-deriving "staleness" against today
    # before today's own collection has happened would just re-report
    # the same gap as a flood of false positives (confirmed: an early
    # test run against a day whose collection hadn't started yet fired
    # on ~40 authorities that were completely fine once collected).
    todays_authorities = conn.execute(
        "SELECT a.code, a.name, c.item_count FROM authorities a "
        "JOIN collection_runs c ON c.authority_code = a.code AND c.collection_date = ? AND c.status = 'ok'",
        (date_str,),
    ).fetchall()
    for code, name, item_count in todays_authorities:
        stale, reason = monitoring.check_extract_date_staleness(conn, code, date_str, config)
        if stale:
            alerts.append(f"STALE EXTRACT DATE: {code} ({name}): {reason}")

        deviated, reason = monitoring.check_record_count_deviation_for_authority(
            conn, code, date_str, item_count, config,
        )
        if deviated:
            alerts.append(f"RECORD COUNT DEVIATION: {code}: {reason}")

    for reason in monitoring.check_parse_failures(conn, date_str, config):
        alerts.append(f"PARSE FAILURE: {reason}")

    ch_reason = monitoring.check_companies_house_run(conn, date_str)
    if ch_reason:
        alerts.append(f"COMPANIES HOUSE: {ch_reason}")

    for reason in monitoring.check_canaries(conn):
        alerts.append(f"CANARY: {reason}")

    return alerts


def write_alerts(config, date_str: str, alerts: list[str]) -> Path:
    alerts_path = config.log_dir / f"_alerts_{date_str}.log"
    with open(alerts_path, "a", encoding="utf-8") as f:
        f.write(f"--- run at {now_iso()} ---\n")
        for alert in alerts:
            f.write(f"{alert}\n")
    return alerts_path


def run(date_str: str) -> int:
    config = load_config()
    logger = setup_logger("monitor_pipeline", config.log_dir / f"monitor_pipeline_{date_str}.log")

    conn = db.connect(config.db_path)

    metrics_path = write_metrics(config, conn, date_str)
    logger.info("metrics written to %s", metrics_path)

    alerts = collect_alerts(config, conn, date_str)
    if alerts:
        alerts_path = write_alerts(config, date_str, alerts)
        logger.warning("%d alert(s) appended to %s", len(alerts), alerts_path)
        for alert in alerts:
            logger.warning("  %s", alert)
    else:
        logger.info("no alerts")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to report/monitor, YYYY-MM-DD (default: today)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)
    sys.exit(run(date_str))


if __name__ == "__main__":
    main()

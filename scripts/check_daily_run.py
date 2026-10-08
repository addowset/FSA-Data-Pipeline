#!/usr/bin/env python
"""Stage 6 addendum: verifies today's scheduled run actually happened.

See fsa_pipeline/monitoring.py's module docstring (check_daily_run_completed)
for why this has to be a separate script and a separate scheduled task
("FSA Data Pipeline Run Check", daily at 13:15 -- comfortably after
run_daily.ps1's own 2-hour ExecutionTimeLimit from its nominal 11:00
trigger, so a run still legitimately in progress doesn't get flagged as
missing) rather than another check inside monitor_pipeline.py.

Usage:
    python scripts/check_daily_run.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline import monitoring
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger


def run(date_str: str) -> int:
    config = load_config()
    logger = setup_logger("check_daily_run", config.log_dir / f"check_daily_run_{date_str}.log")

    problem, reason = monitoring.check_daily_run_completed(config.log_dir, date_str)
    if not problem:
        logger.info("today's run completed (monitor_pipeline_%s.log present)", date_str)
        return 0

    alert = f"SCHEDULED RUN: {reason}"
    alerts_path = monitoring.append_alerts(config.log_dir, date_str, [alert])
    logger.warning("1 alert(s) appended to %s", alerts_path)
    logger.warning("  %s", alert)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to check, YYYY-MM-DD (default: today)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)
    sys.exit(run(date_str))


if __name__ == "__main__":
    main()

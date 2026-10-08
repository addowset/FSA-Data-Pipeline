#!/usr/bin/env python
"""Sends the daily status email: whether today's run has started/completed,
with that day's logs/_alerts_<date>.log attached if one exists. Run right
after check_daily_run.py, as the second step of the "FSA Data Pipeline Run
Check" scheduled task (13:15 daily) -- see scripts/run_check.ps1.

Requires SMTP_USER and SMTP_PASSWORD in the environment (.env) -- see
fsa_pipeline/alerting.py's get_smtp_credentials for how to generate a Gmail
app password. If config.toml's [alerting] enabled = false, or credentials
aren't set, this logs why and exits 0 rather than failing the scheduled
task -- a missing/disabled email is a configuration state, not a crash.
An actual SMTP send failure (bad credentials, network down, etc.) exits 1,
since that's a real failure of this script's one job.

Usage:
    python scripts/send_run_status_email.py [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsa_pipeline.alerting import MissingCredentials, build_status_email, send_email
from fsa_pipeline.config import load_config
from fsa_pipeline.logging_utils import setup_logger


def run(date_str: str) -> int:
    config = load_config()
    logger = setup_logger("send_run_status_email", config.log_dir / f"send_run_status_email_{date_str}.log")

    if not config.alerting_enabled:
        logger.info("alerting.enabled is false in config.toml -- not sending")
        return 0

    started = (config.log_dir / f"fhrs_collect_{date_str}.log").exists()
    completed = (config.log_dir / f"monitor_pipeline_{date_str}.log").exists()
    alerts_path = config.log_dir / f"_alerts_{date_str}.log"

    message = build_status_email(config, date_str, started, completed, alerts_path)

    try:
        send_email(config, message)
    except MissingCredentials as e:
        logger.warning("not sending: %s", e)
        return 0
    except Exception as e:
        logger.error("send failed: %s", e)
        return 1

    logger.info("sent status email to %s (started=%s completed=%s, alerts_path exists=%s)",
                config.alerting_recipient_email, started, completed, alerts_path.exists())
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="Date to report, YYYY-MM-DD (default: today)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    date_str = args.date or dt.date.today().isoformat()
    dt.date.fromisoformat(date_str)
    sys.exit(run(date_str))


if __name__ == "__main__":
    main()

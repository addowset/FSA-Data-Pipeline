"""Daily status email -- see config.toml's [alerting] section for why this
exists despite the 2026-09-11 decision to use a log file instead (see
[monitoring]'s comment there). Sent by scripts/send_run_status_email.py.

Credentials (SMTP_USER, SMTP_PASSWORD -- a Gmail app password, not a real
account password) come from the environment via .env, same convention as
fsa_pipeline/companies_house.py's COMPANIES_HOUSE_API_KEY. Never commit
them, never log them.

build_status_email is a pure function (no network) so it's unit-testable;
send_email is the one function that actually opens an SMTP connection and
is deliberately not unit-tested, same as every other network-touching
function in this codebase -- verify it for real once credentials exist.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from fsa_pipeline.config import Config

SMTP_USER_ENV_VAR = "SMTP_USER"
SMTP_PASSWORD_ENV_VAR = "SMTP_PASSWORD"


class MissingCredentials(Exception):
    pass


def get_smtp_credentials() -> tuple[str, str]:
    user = os.environ.get(SMTP_USER_ENV_VAR)
    password = os.environ.get(SMTP_PASSWORD_ENV_VAR)
    if not user or not password:
        raise MissingCredentials(
            f"{SMTP_USER_ENV_VAR} and {SMTP_PASSWORD_ENV_VAR} must both be set. For Gmail: "
            "enable 2-Step Verification, then generate an App Password at "
            "myaccount.google.com/apppasswords and use that (not your real password) "
            "as SMTP_PASSWORD. Set both in .env, never commit it."
        )
    return user, password


def build_status_email(
    config: Config, date_str: str, started: bool, completed: bool, alerts_path: Path,
) -> EmailMessage:
    if completed:
        status = "completed"
    elif started:
        status = "started, not yet complete"
    else:
        status = "NOT STARTED"

    message = EmailMessage()
    message["Subject"] = f"FSA pipeline {date_str}: {status}"
    message["To"] = config.alerting_recipient_email

    has_alerts = alerts_path.exists()
    lines = [
        f"Date: {date_str}",
        f"Collection started: {'yes' if started else 'no'}",
        f"Run completed: {'yes' if completed else 'no'}",
        "",
    ]
    if has_alerts:
        alert_count = sum(1 for line in alerts_path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("---"))
        lines.append(f"{alert_count} alert line(s) today -- see attached {alerts_path.name}.")
    else:
        lines.append("No alerts logged today.")
    message.set_content("\n".join(lines))

    if has_alerts:
        message.add_attachment(
            alerts_path.read_bytes(),
            maintype="text",
            subtype="plain",
            filename=alerts_path.name,
        )

    return message


def send_email(config: Config, message: EmailMessage) -> None:
    user, password = get_smtp_credentials()
    message["From"] = user
    with smtplib.SMTP(config.alerting_smtp_host, config.alerting_smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(message)

#!/usr/bin/env python3
"""Batch Job Monitor.

Reads job run history from a SQLite table, compares it with the expectations in
config.json, and sends an alert when something looks wrong:

  FAILED        the latest run of the job failed
  LONG_RUNNING  the job is still running past its max runtime
  MISSED_START  the job has not started by its expected time plus grace
  SLA_BREACH    the job has not succeeded by its SLA deadline

Alerts are de-duplicated (you are told once, not every minute) and a RECOVERED
message is sent when the problem clears. Standard library only.
"""
import argparse
import json
import logging
import os
import smtplib
import sqlite3
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

TS = "%Y-%m-%d %H:%M:%S"
ICONS = {"critical": "🔴", "warning": "🟠", "recovered": "🟢"}
log = logging.getLogger("batch-monitor")


@dataclass
class Alert:
    job: str
    kind: str
    severity: str
    message: str

    def key(self, day: str) -> str:
        return f"{self.job}|{self.kind}|{day}"

    def text(self) -> str:
        return f"{ICONS.get(self.severity, '⚪')} [{self.kind}] {self.job}: {self.message}"


# ---------------------------------------------------------------- checks ----

def time_today(now: datetime, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def latest_run(conn, job: str, now: datetime):
    """Most recent run today that had already started at `now`."""
    return conn.execute(
        "SELECT status, start_time, end_time, exit_code, message FROM job_runs "
        "WHERE job_name = ? AND date(start_time) = ? AND start_time <= ? "
        "ORDER BY start_time DESC LIMIT 1",
        (job, now.strftime("%Y-%m-%d"), now.strftime(TS))).fetchone()


def check_job(conn, job: dict, now: datetime) -> list[Alert]:
    name = job["name"]
    severity = job.get("severity", "warning")
    run = latest_run(conn, name, now)
    alerts: list[Alert] = []

    if run is None:
        expected = time_today(now, job["expected_start"])
        grace = timedelta(minutes=job.get("start_grace_minutes", 15))
        if now > expected + grace:
            alerts.append(Alert(name, "MISSED_START", severity,
                                f"No run found today. Expected start {job['expected_start']}."))
    else:
        status, start, _end, exit_code, message = run
        if status == "FAILED":
            alerts.append(Alert(name, "FAILED", severity,
                                f"Exit code {exit_code}. {message or 'No message.'}"))
        elif status == "RUNNING":
            elapsed = (now - datetime.strptime(start, TS)).total_seconds() / 60
            limit = job.get("max_runtime_minutes")
            if limit and elapsed > limit:
                alerts.append(Alert(name, "LONG_RUNNING", severity,
                                    f"Running for {elapsed:.0f} min (limit {limit} min)."))

    deadline = job.get("sla_deadline")
    if deadline and now > time_today(now, deadline) and (run is None or run[0] != "SUCCESS"):
        alerts.append(Alert(name, "SLA_BREACH", "critical",
                            f"Not successful by SLA deadline {deadline}."))
    return alerts


# --------------------------------------------------------------- senders ----

def send_slack(cfg: dict, text: str):
    url = os.environ.get(cfg["webhook_env"])
    if not url:
        log.warning("Slack enabled but env var %s is not set", cfg["webhook_env"])
        return
    req = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def send_email(cfg: dict, subject: str, body: str):
    user = os.environ.get(cfg["username_env"])
    password = os.environ.get(cfg["password_env"])
    if not user or not password:
        log.warning("Email enabled but SMTP credentials are not set in the environment")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["from"], ", ".join(cfg["to"])
    msg.set_content(body)
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=15) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


def notify(alert_cfg: dict, subject: str, text: str, dry_run: bool):
    if alert_cfg.get("console", True):
        print(text)
    if dry_run:
        return
    for name, sender in (("slack", lambda c: send_slack(c, text)),
                         ("email", lambda c: send_email(c, subject, text))):
        channel = alert_cfg.get(name, {})
        if channel.get("enabled"):
            try:
                sender(channel)
            except Exception as exc:  # one broken channel must not stop the others
                log.error("%s notification failed: %s", name, exc)


# ----------------------------------------------------------------- state ----

def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"active": {}}


def save_state(path: Path, state: dict):
    path.write_text(json.dumps(state, indent=2))


# ------------------------------------------------------------------ main ----

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--at", metavar="HH:MM",
                        help="Pretend it is this time today (handy for demos and tests)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print alerts only: no Slack/email, no state saved")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = json.loads(Path(args.config).read_text())

    now = datetime.now()
    if args.at:
        now = time_today(now, args.at)
    day = now.strftime("%Y-%m-%d")
    print(f"--- Batch Job Monitor | {now:%Y-%m-%d %H:%M} ---")

    with sqlite3.connect(config["database"]) as conn:
        current = [a for job in config["jobs"] for a in check_job(conn, job, now)]

    state_path = Path(config.get("state_file", "alert_state.json"))
    state = load_state(state_path)
    active = state["active"]
    current_keys = {a.key(day): a for a in current}

    # Forget keys from previous days quietly, to avoid a flood of "recovered" at midnight.
    active = {k: v for k, v in active.items() if k.endswith(f"|{day}")}

    new = [a for k, a in current_keys.items() if k not in active]
    recovered = [k for k in active if k not in current_keys]

    for alert in new:
        notify(config["alerts"], f"[{alert.severity.upper()}] {alert.job} {alert.kind}",
               alert.text(), args.dry_run)
        active[alert.key(day)] = now.strftime(TS)

    for key in recovered:
        job, kind, _ = key.split("|")
        notify(config["alerts"], f"[RECOVERED] {job} {kind}",
               f"{ICONS['recovered']} [RECOVERED] {job}: {kind} cleared.", args.dry_run)
        del active[key]

    if not new and not recovered:
        print("No new alerts." if current else "All jobs healthy.")

    if not args.dry_run:
        save_state(state_path, {"active": active})

    return 2 if current else 0  # non-zero exit lets cron/CI notice problems


if __name__ == "__main__":
    sys.exit(main())

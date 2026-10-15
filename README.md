# Batch Job Monitor

A small, dependency-free Python tool that watches batch job runs (Control-M style)
and alerts on failures, stuck jobs, missed starts and SLA breaches.

Built from real L2/L3 support experience: the four things that wake people up at
night in batch support are exactly the four things this tool checks.

## What it detects

| Alert          | Meaning                                              | Severity     |
|----------------|------------------------------------------------------|--------------|
| `FAILED`       | Latest run of the job ended in failure               | per job      |
| `LONG_RUNNING` | Job is still running past its `max_runtime_minutes`  | per job      |
| `MISSED_START` | No run by `expected_start` + `start_grace_minutes`   | per job      |
| `SLA_BREACH`   | Job has not succeeded by its `sla_deadline`          | critical     |

Extras: alerts are **de-duplicated** (one message per problem per day) and a
**RECOVERED** message is sent when a problem clears.

## How it works

```
 scheduler / jobs  -->  job_runs table (SQLite)  -->  monitor.py  -->  Console / Slack / Email
                                                          ^
                                                      config.json
                                                (what "normal" looks like)
```

In production you would point `monitor.py` at your scheduler's history
(Control-M, Airflow, etc.). Here a simulator creates realistic data so anyone can
run the demo in 30 seconds.

## Requirements

- Python 3.9+ (nothing to install)

## Quick demo

```bash
python simulate_jobs.py          # creates jobs.db with 4 jobs in different states
python monitor.py --at 17:30     # pretend it is 17:30  -> all healthy
python monitor.py --at 18:30     # EOD_RISK_LOAD failed
python monitor.py --at 21:30     # stuck job, missed start, SLA breaches
python monitor.py --at 21:35     # nothing new: alerts are de-duplicated
```

Handy flags: `--at HH:MM` (simulate a time), `--dry-run` (no Slack/email, no state saved).

## Configuration (`config.json`)

Each job has:

| Field                 | Meaning                                   |
|-----------------------|-------------------------------------------|
| `name`                | Job name, must match `job_name` in the DB |
| `expected_start`      | Usual start time, `HH:MM`                 |
| `start_grace_minutes` | How late a start is tolerated             |
| `max_runtime_minutes` | Longest acceptable run time               |
| `sla_deadline`        | Must have succeeded by this time          |
| `severity`            | `warning` or `critical`                   |

## Turning on Slack / email alerts

Secrets live in environment variables, never in the repo.

```bash
# Slack: create an Incoming Webhook, then set "slack.enabled": true in config.json
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/XXX/YYY/ZZZ"

# Email: set "email.enabled": true and fill in smtp_host/from/to
export SMTP_USER="you@example.com"
export SMTP_PASSWORD="app-password"
```

## Run it automatically (cron)

```cron
*/5 * * * * cd /path/to/batch-job-monitor && /usr/bin/python3 monitor.py >> monitor.log 2>&1
```

The script exits with code `2` when problems exist and `0` when all is healthy,
so it also plugs into CI or other monitoring.

## Ideas to extend

- Read from a real source (Control-M REST API, Airflow API, Oracle table)
- Add a Dockerfile and a GitHub Actions workflow that runs the demo as a test
- Expose metrics for Prometheus and build a Grafana dashboard
- Add unit tests with `pytest` for each check

#!/usr/bin/env python3
"""Create a demo SQLite database that mimics a batch scheduler's job history.

Each row in `job_runs` is one execution of a job, like a Control-M run record.
Four jobs are created, each in a different situation, so every alert type
in monitor.py can be demonstrated.
"""
import argparse
import sqlite3
from datetime import datetime

TS = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
DROP TABLE IF EXISTS job_runs;
CREATE TABLE job_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name   TEXT NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    start_time TEXT NOT NULL,
    end_time   TEXT,
    exit_code  INTEGER,
    message    TEXT
);
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="jobs.db", help="SQLite file to create")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="Date for the demo runs (YYYY-MM-DD, default: today)")
    args = parser.parse_args()
    d = args.date

    runs = [
        # Healthy job
        ("DAILY_FILE_INGEST", "SUCCESS", f"{d} 17:02:00", f"{d} 17:20:00", 0, "Loaded 12 files"),
        # Failed job
        ("EOD_RISK_LOAD", "FAILED", f"{d} 18:01:00", f"{d} 18:10:00", 1,
         "ORA-01555: snapshot too old"),
        # Stuck job: started but never finished
        ("REPORT_GENERATION", "RUNNING", f"{d} 19:00:00", None, None, "Generating reports"),
        # ARCHIVE_PURGE has no row at all: it never started.
    ]

    with sqlite3.connect(args.db) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO job_runs (job_name, status, start_time, end_time, exit_code, message) "
            "VALUES (?, ?, ?, ?, ?, ?)", runs)
    print(f"Created {args.db} with {len(runs)} demo runs for {d}")


if __name__ == "__main__":
    main()

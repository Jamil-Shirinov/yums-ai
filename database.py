"""
All the SQLite bits: creating the tables, plus the handful of queries the
web app needs.

This uses plain sqlite3 rather than an ORM so every query is visible right
here. SQLite is a single file on disk (yums.db), which is plenty for this.
"""

import json
import os
import sqlite3
from datetime import datetime

from models import EmailSummary

DB_FILE = os.getenv("YUMS_DB_FILE", "yums.db")

# --------------------

def get_connection() -> sqlite3.Connection:
    """Open a connection to the database file.

    row_factory makes rows behave like dicts, so we can write row["email"]
    instead of remembering that email is column number 1.
    """

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# --------------------

def init_db() -> None:
    """Create the tables if they don't exist yet. Safe to call every startup."""

    conn = get_connection()

    # One row per account. gmail_token holds the OAuth token JSON we get back
    # from Google, and stays NULL until the user connects their Gmail.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            email          TEXT NOT NULL UNIQUE,
            password_hash  TEXT NOT NULL,
            plan           TEXT NOT NULL,
            gmail_token    TEXT,
            gmail_address  TEXT,
            gmail_picture  TEXT,
            created_at     TEXT NOT NULL
        )
        """
    )

    # CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    # so databases made before avatars were added need the new column bolting
    # on by hand.
    existing_columns = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]
    if "gmail_picture" not in existing_columns:
        conn.execute("ALTER TABLE users ADD COLUMN gmail_picture TEXT")

    # One row per "Analyze my inbox" click, so the dashboard can show history
    # and so refreshing the results page doesn't re-run (and re-bill) anything.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            created_at    TEXT NOT NULL,
            results_json  TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )

    conn.commit()
    conn.close()

# --------------------
# Users
# --------------------

def create_user(email: str, password_hash: str, plan: str) -> int:
    """Insert a new account and return its id."""

    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO users (email, password_hash, plan, created_at) VALUES (?, ?, ?, ?)",
        (email, password_hash, plan, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def get_user_by_email(email: str):
    """Find an account by email address, or None if there isn't one."""

    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row


def get_user_by_id(user_id: int):
    """Find an account by id, or None if it's been deleted."""

    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def update_plan(user_id: int, plan: str) -> None:
    """Move an account onto a different plan.

    Because plans are simulated, this takes effect the moment it's called.
    Once real billing exists, this should only run after a successful payment.
    """

    conn = get_connection()
    conn.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))
    conn.commit()
    conn.close()


def save_gmail_token(
    user_id: int,
    token_json: str,
    gmail_address: str = None,
    gmail_picture: str = None,
) -> None:
    """Store (or update) the Gmail OAuth token for an account.

    gmail_address is optional because we also call this after a silent token
    refresh, when the address and picture haven't changed and we don't want
    to wipe them.
    """

    conn = get_connection()
    if gmail_address is None:
        conn.execute("UPDATE users SET gmail_token = ? WHERE id = ?", (token_json, user_id))
    else:
        conn.execute(
            "UPDATE users SET gmail_token = ?, gmail_address = ?, gmail_picture = ? "
            "WHERE id = ?",
            (token_json, gmail_address, gmail_picture or "", user_id),
        )
    conn.commit()
    conn.close()


def clear_gmail_token(user_id: int) -> None:
    """Forget an account's Gmail connection (the "Disconnect" button).

    The avatar goes too - it came from the account we're disconnecting.
    """

    conn = get_connection()
    conn.execute(
        "UPDATE users SET gmail_token = NULL, gmail_address = NULL, "
        "gmail_picture = NULL WHERE id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()

# --------------------
# Analysis runs
# --------------------

def _summary_to_dict(summary: EmailSummary) -> dict:
    """Turn one EmailSummary into something json can store.

    Note what's missing: the email *body*. We only need the sender and subject
    to draw a report, so there's no good reason to keep a copy of somebody's
    email text sitting in our database afterwards.
    """

    return {
        "category": summary.category,
        "summary": summary.summary,
        "reason": summary.reason,
        "email": {
            "id": summary.email.id,
            "sender": summary.email.sender,
            "subject": summary.email.subject,
            "date": summary.email.date,
        },
    }


def create_run(user_id: int, summaries: list[EmailSummary]) -> int:
    """Save the results of one analysis and return the new run's id."""

    results_json = json.dumps([_summary_to_dict(s) for s in summaries])

    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO runs (user_id, created_at, results_json) VALUES (?, ?, ?)",
        (user_id, datetime.now().isoformat(timespec="seconds"), results_json),
    )
    conn.commit()
    run_id = cursor.lastrowid
    conn.close()
    return run_id


def get_run(run_id: int, user_id: int):
    """Fetch one run's results as a list of dicts.

    The user_id is part of the WHERE clause on purpose: without it, anyone
    could read anyone else's report just by typing a different number into
    the URL.
    """

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM runs WHERE id = ? AND user_id = ?", (run_id, user_id)
    ).fetchone()
    conn.close()

    if row is None:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "results": json.loads(row["results_json"]),
    }


def list_runs(user_id: int, limit: int = 5) -> list[dict]:
    """Fetch the most recent runs for an account, newest first, for the
    little history list on the dashboard."""

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, created_at, results_json FROM runs WHERE user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()

    history = []
    for row in rows:
        results = json.loads(row["results_json"])
        history.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "total": len(results),
                "actions": len([r for r in results if r["category"] == "action"]),
            }
        )
    return history

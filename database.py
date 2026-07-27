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

import encryption
from models import EmailSummary

DB_FILE = os.getenv("YUMS_DB_FILE", "yums.db")

# --------------------

def get_connection() -> sqlite3.Connection:
    """Open a connection to the database file.

    row_factory makes rows behave like dicts, so we can write row["email"]
    instead of remembering that email is column number 1.

    The timeout matters now that analyses run on background threads: if a
    worker is mid-write while someone refreshes their progress page, the
    reader waits its turn instead of failing with "database is locked".
    Each caller opens and closes its own connection, so nothing is ever
    shared across threads.
    """

    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

# --------------------

def init_db() -> None:
    """Create the tables if they don't exist yet. Safe to call every startup."""

    conn = get_connection()

    # One row per account. gmail_token holds the OAuth token JSON we get back
    # from Google, and stays NULL until the user connects their Gmail. The
    # verification_* columns hold the signup confirmation code while it's
    # pending, and are cleared once the address is confirmed.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            email                    TEXT NOT NULL UNIQUE,
            password_hash            TEXT NOT NULL,
            plan                     TEXT NOT NULL,
            gmail_token              TEXT,
            gmail_address            TEXT,
            verified                 INTEGER NOT NULL DEFAULT 0,
            verification_code        TEXT,
            verification_expires_at  TEXT,
            verification_attempts    INTEGER NOT NULL DEFAULT 0,
            stripe_customer_id       TEXT,
            stripe_subscription_id   TEXT,
            pending_plan             TEXT,
            created_at               TEXT NOT NULL
        )
        """
    )

    # CREATE TABLE IF NOT EXISTS does nothing to a table that already exists,
    # so a database made before email confirmation needs these adding by hand.
    # (The column names below are literals from this file, not user input.)
    existing_columns = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]

    if "verified" not in existing_columns:
        conn.execute("ALTER TABLE users ADD COLUMN verified INTEGER NOT NULL DEFAULT 0")
        # Accounts that predate confirmation were never asked for a code.
        # Marking them confirmed keeps their owners from being locked out of
        # accounts they already use.
        conn.execute("UPDATE users SET verified = 1")

    for column in ("verification_code", "verification_expires_at"):
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")

    if "verification_attempts" not in existing_columns:
        conn.execute(
            "ALTER TABLE users ADD COLUMN verification_attempts INTEGER NOT NULL DEFAULT 0"
        )

    # Billing. stripe_customer_id sticks around once set, so a returning
    # customer keeps one payment history. pending_plan remembers what someone
    # picked at signup while they haven't paid for it yet - their plan column
    # only ever reflects what they actually have.
    for column in ("stripe_customer_id", "stripe_subscription_id", "pending_plan"):
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT")

    # Tokens stored before encryption existed are raw JSON sitting in the
    # file. Scramble them in place so nothing readable is left behind. The
    # LIKE picks out exactly those - encrypted values are base64 and never
    # start with a brace.
    plaintext_tokens = conn.execute(
        "SELECT id, gmail_token FROM users "
        "WHERE gmail_token IS NOT NULL AND gmail_token LIKE '{%'"
    ).fetchall()

    for row in plaintext_tokens:
        conn.execute(
            "UPDATE users SET gmail_token = ? WHERE id = ?",
            (encryption.encrypt(row["gmail_token"]), row["id"]),
        )

    if plaintext_tokens:
        print(f"[database] encrypted {len(plaintext_tokens)} stored Gmail token(s)")

    # One row per "Analyze my inbox" click, so the dashboard can show history
    # and so refreshing the results page doesn't re-run (and re-bill) anything.
    #
    # The analysis happens on a background thread, so a row appears here the
    # moment the button is pressed and is filled in as the work progresses:
    #   status 'running' -> 'done' (results_json populated) or 'failed' (error set)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL,
            created_at        TEXT NOT NULL,
            results_json      TEXT NOT NULL,
            status            TEXT NOT NULL DEFAULT 'done',
            error             TEXT,
            total_emails      INTEGER NOT NULL DEFAULT 0,
            processed_emails  INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )

    # Runs recorded before analyses moved to the background all finished, so
    # the DEFAULT 'done' above is exactly right for them.
    run_columns = [row["name"] for row in conn.execute("PRAGMA table_info(runs)")]

    if "status" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN status TEXT NOT NULL DEFAULT 'done'")
    if "error" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN error TEXT")
    if "total_emails" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN total_emails INTEGER NOT NULL DEFAULT 0")
    if "processed_emails" not in run_columns:
        conn.execute("ALTER TABLE runs ADD COLUMN processed_emails INTEGER NOT NULL DEFAULT 0")

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


def save_verification_code(user_id: int, code: str, expires_at: str) -> None:
    """Store a fresh confirmation code, wiping any previous one.

    The attempt counter resets too, so asking for a new code gives the user
    a clean slate rather than leaving them locked out by earlier typos.
    """

    conn = get_connection()
    conn.execute(
        "UPDATE users SET verification_code = ?, verification_expires_at = ?, "
        "verification_attempts = 0 WHERE id = ?",
        (code, expires_at, user_id),
    )
    conn.commit()
    conn.close()


def record_failed_attempt(user_id: int) -> None:
    """Count one wrong guess, so we can stop someone trying all million codes."""

    conn = get_connection()
    conn.execute(
        "UPDATE users SET verification_attempts = verification_attempts + 1 WHERE id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def mark_verified(user_id: int) -> None:
    """Confirm the account and throw the used code away."""

    conn = get_connection()
    conn.execute(
        "UPDATE users SET verified = 1, verification_code = NULL, "
        "verification_expires_at = NULL, verification_attempts = 0 WHERE id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def delete_account(user_id: int) -> None:
    """Erase an account and everything belonging to it.

    Reports go first so nothing is left orphaned pointing at a user row that
    no longer exists. This is the end of the line - there's no undo, and
    nothing is kept back for "just in case".
    """

    conn = get_connection()
    conn.execute("DELETE FROM runs WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_user_by_stripe_customer(customer_id: str):
    """Find an account from a Stripe customer id.

    Webhooks arrive with no browser session attached, so this is how an
    event about a subscription gets matched back to an account.
    """

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE stripe_customer_id = ?", (customer_id,)
    ).fetchone()
    conn.close()
    return row


def save_stripe_customer(user_id: int, customer_id: str) -> None:
    """Remember which Stripe customer an account is, first time they pay."""

    conn = get_connection()
    conn.execute(
        "UPDATE users SET stripe_customer_id = ? WHERE id = ?", (customer_id, user_id)
    )
    conn.commit()
    conn.close()


def activate_subscription(user_id: int, plan: str, subscription_id: str) -> None:
    """Put an account on the plan it has just paid for.

    Clears pending_plan at the same time: whatever they were part-way through
    buying, this is now settled.
    """

    conn = get_connection()
    conn.execute(
        "UPDATE users SET plan = ?, stripe_subscription_id = ?, pending_plan = NULL "
        "WHERE id = ?",
        (plan, subscription_id, user_id),
    )
    conn.commit()
    conn.close()


def end_subscription(user_id: int) -> None:
    """Drop an account back to Free when its subscription ends.

    Without this a cancelled customer would keep their paid limits forever.
    """

    conn = get_connection()
    conn.execute(
        "UPDATE users SET plan = 'free', stripe_subscription_id = NULL WHERE id = ?",
        (user_id,),
    )
    conn.commit()
    conn.close()


def set_pending_plan(user_id: int, plan) -> None:
    """Remember a plan someone chose but hasn't paid for yet (or clear it
    by passing None)."""

    conn = get_connection()
    conn.execute("UPDATE users SET pending_plan = ? WHERE id = ?", (plan, user_id))
    conn.commit()
    conn.close()


def update_plan(user_id: int, plan: str) -> None:
    """Move an account onto a different plan.

    This is the no-payment path: either Stripe isn't configured, or the plan
    is one nobody has to pay for. Anything bought through Stripe goes via
    activate_subscription() instead.

    Clearing pending_plan matters here too - once someone settles on a plan,
    whatever they half-chose at signup is no longer outstanding.
    """

    conn = get_connection()
    conn.execute(
        "UPDATE users SET plan = ?, pending_plan = NULL WHERE id = ?", (plan, user_id)
    )
    conn.commit()
    conn.close()


def save_gmail_token(user_id: int, token_json: str, gmail_address: str = None) -> None:
    """Store (or update) the Gmail OAuth token for an account.

    gmail_address is optional because we also call this after a silent token
    refresh, when the address hasn't changed and we don't want to clear it.

    The token is encrypted on the way in - it never hits the disk readable.
    """

    encrypted_token = encryption.encrypt(token_json)

    conn = get_connection()
    if gmail_address is None:
        conn.execute("UPDATE users SET gmail_token = ? WHERE id = ?", (encrypted_token, user_id))
    else:
        conn.execute(
            "UPDATE users SET gmail_token = ?, gmail_address = ? WHERE id = ?",
            (encrypted_token, gmail_address, user_id),
        )
    conn.commit()
    conn.close()


def get_gmail_token(user_id: int):
    """Read an account's Gmail token back, decrypted and ready to use.

    Returns None if there's no connection. Everywhere else in the app checks
    user["gmail_token"] purely for truthiness ("is Gmail connected?"), which
    still works on the encrypted value - this is the only place that needs
    the real thing.
    """

    conn = get_connection()
    row = conn.execute("SELECT gmail_token FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()

    if row is None or not row["gmail_token"]:
        return None
    return encryption.decrypt(row["gmail_token"])


def clear_gmail_token(user_id: int) -> None:
    """Forget an account's Gmail connection (the "Disconnect" button)."""

    conn = get_connection()
    conn.execute(
        "UPDATE users SET gmail_token = NULL, gmail_address = NULL WHERE id = ?",
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


def create_pending_run(user_id: int) -> int:
    """Open a new run in the 'running' state and return its id.

    Called the instant the button is pressed, before any work happens, so the
    browser has somewhere to go while the background thread gets going.
    """

    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO runs (user_id, created_at, results_json, status) VALUES (?, ?, ?, 'running')",
        (user_id, datetime.now().isoformat(timespec="seconds"), "[]"),
    )
    conn.commit()
    run_id = cursor.lastrowid
    conn.close()
    return run_id


def set_run_total(run_id: int, total: int) -> None:
    """Record how many emails this run is going to work through, once we've
    fetched them and know."""

    conn = get_connection()
    conn.execute("UPDATE runs SET total_emails = ? WHERE id = ?", (total, run_id))
    conn.commit()
    conn.close()


def record_run_progress(run_id: int, processed: int) -> None:
    """Tick the counter the progress page reads."""

    conn = get_connection()
    conn.execute("UPDATE runs SET processed_emails = ? WHERE id = ?", (processed, run_id))
    conn.commit()
    conn.close()


def complete_run(run_id: int, summaries: list[EmailSummary]) -> None:
    """Store the finished results and mark the run done."""

    results_json = json.dumps([_summary_to_dict(s) for s in summaries])

    conn = get_connection()
    conn.execute(
        "UPDATE runs SET results_json = ?, status = 'done', processed_emails = ? WHERE id = ?",
        (results_json, len(summaries), run_id),
    )
    conn.commit()
    conn.close()


def get_run_status(run_id: int):
    """Just the status, for the worker to poll cheaply between emails."""

    conn = get_connection()
    row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return row["status"] if row else None


def request_cancel(run_id: int, user_id: int) -> bool:
    """Ask a running analysis to stop.

    A thread can't be killed from outside, so this only raises a flag: the
    worker notices it before the next email and stops there. Returns whether
    a run was actually flagged - False means it had already finished, or
    belongs to somebody else.
    """

    conn = get_connection()
    cursor = conn.execute(
        "UPDATE runs SET status = 'cancelling' "
        "WHERE id = ? AND user_id = ? AND status = 'running'",
        (run_id, user_id),
    )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def cancel_run(run_id: int, summaries: list[EmailSummary]) -> None:
    """Close off a stopped run, keeping whatever it managed to classify.

    The partial results are worth keeping - somebody who stops a 100-email
    run after 30 still gets a report on those 30.
    """

    results_json = json.dumps([_summary_to_dict(s) for s in summaries])

    conn = get_connection()
    conn.execute(
        "UPDATE runs SET results_json = ?, status = 'cancelled', processed_emails = ? "
        "WHERE id = ?",
        (results_json, len(summaries), run_id),
    )
    conn.commit()
    conn.close()


def fail_run(run_id: int, message: str) -> None:
    """Mark a run as failed, with something the user can act on."""

    conn = get_connection()
    conn.execute(
        "UPDATE runs SET status = 'failed', error = ? WHERE id = ?", (message, run_id)
    )
    conn.commit()
    conn.close()


def create_completed_run(user_id: int, summaries: list[EmailSummary]) -> int:
    """Record an already-finished run in one go.

    Only used for seeding demo data and tests - real analyses go through
    create_pending_run() and complete_run().
    """

    run_id = create_pending_run(user_id)
    set_run_total(run_id, len(summaries))
    complete_run(run_id, summaries)
    return run_id


def delete_run(run_id: int, user_id: int) -> bool:
    """Delete one report for good.

    The user_id is in the WHERE clause for the same reason it is in
    get_run(): without it, anyone could delete anyone else's report by
    changing the number in the URL.
    """

    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM runs WHERE id = ? AND user_id = ?", (run_id, user_id)
    )
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def get_active_run(user_id: int):
    """Return the id of this account's in-flight run, if there is one.

    Used to stop somebody kicking off a second analysis - and a second
    OpenAI bill - while the first is still going. A run that's been asked to
    stop still counts: its thread is busy until it notices.
    """

    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM runs WHERE user_id = ? AND status IN ('running', 'cancelling') "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["id"] if row else None


def fail_interrupted_runs() -> int:
    """Clean up after a restart.

    Background threads die with the process, so anything still marked
    'running' when the app starts is never going to finish. Without this it
    would sit there claiming to be in progress forever.
    """

    conn = get_connection()
    cursor = conn.execute(
        "UPDATE runs SET status = 'failed', error = ? "
        "WHERE status IN ('running', 'cancelling')",
        ("This analysis was interrupted when the server restarted. Please run it again.",),
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


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
        "status": row["status"],
        "error": row["error"],
        "total_emails": row["total_emails"],
        "processed_emails": row["processed_emails"],
    }


def list_runs(user_id: int, limit: int = 5) -> list[dict]:
    """Fetch the most recent runs for an account, newest first, for the
    little history list on the dashboard."""

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, created_at, results_json, status FROM runs WHERE user_id = ? "
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
                "status": row["status"],
                "total": len(results),
                "actions": len([r for r in results if r["category"] == "action"]),
            }
        )
    return history

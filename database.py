"""
database.py — SQLite persistence layer.

Tables:
  - achievements   : per-account achievement state
  - audit_log      : every action taken (no secrets stored)
  - action_budget  : daily/hourly usage counters
"""

import sqlite3
import json
from datetime import datetime, date
from contextlib import contextmanager
from config import DATABASE_PATH


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS achievements (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL,
    name          TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'UNKNOWN',
    progress      TEXT,           -- JSON: {"current": 1, "required": 2}
    tier          TEXT,
    last_checked  TEXT,
    UNIQUE(account_id, name)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp            TEXT    NOT NULL,
    account_id           INTEGER NOT NULL,
    action               TEXT    NOT NULL,
    repository           TEXT,
    api_endpoint         TEXT,
    result               TEXT,
    achievement          TEXT,
    rate_limit_remaining INTEGER,
    risk_status          TEXT,
    dry_run              INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS action_budget (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL,
    budget_date     TEXT    NOT NULL,
    budget_hour     INTEGER NOT NULL DEFAULT 0,
    mutations_used  INTEGER NOT NULL DEFAULT 0,
    issues_used     INTEGER NOT NULL DEFAULT 0,
    prs_used        INTEGER NOT NULL DEFAULT 0,
    UNIQUE(account_id, budget_date, budget_hour)
);

CREATE TABLE IF NOT EXISTS automation_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Achievement state
# ---------------------------------------------------------------------------

def upsert_achievement(account_id: int, name: str, status: str,
                       progress: dict | None = None, tier: str | None = None):
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO achievements (account_id, name, status, progress, tier, last_checked)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, name) DO UPDATE SET
                status       = excluded.status,
                progress     = excluded.progress,
                tier         = excluded.tier,
                last_checked = excluded.last_checked
        """, (
            account_id,
            name,
            status,
            json.dumps(progress) if progress else None,
            tier,
            datetime.utcnow().isoformat(),
        ))


def get_achievements(account_id: int) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM achievements WHERE account_id = ? ORDER BY name",
            (account_id,)
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("progress"):
            try:
                d["progress"] = json.loads(d["progress"])
            except (ValueError, TypeError):
                pass
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def log_action(
    account_id: int,
    action: str,
    result: str,
    achievement: str | None = None,
    repository: str | None = None,
    api_endpoint: str | None = None,
    rate_limit_remaining: int | None = None,
    risk_status: str = "LOW",
    dry_run: bool = True,
):
    """Write an entry to the audit log. Never logs tokens or secrets."""
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO audit_log
              (timestamp, account_id, action, repository, api_endpoint,
               result, achievement, rate_limit_remaining, risk_status, dry_run)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            account_id,
            action,
            repository,
            api_endpoint,
            result,
            achievement,
            rate_limit_remaining,
            risk_status,
            1 if dry_run else 0,
        ))


def get_audit_log(limit: int = 200, offset: int = 0) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Action budget
# ---------------------------------------------------------------------------

def _budget_key(account_id: int) -> tuple[str, int]:
    now = datetime.utcnow()
    return now.strftime("%Y-%m-%d"), now.hour


def _ensure_budget_row(cur: sqlite3.Cursor, account_id: int, today: str, hour: int):
    cur.execute("""
        INSERT OR IGNORE INTO action_budget
          (account_id, budget_date, budget_hour, mutations_used, issues_used, prs_used)
        VALUES (?, ?, ?, 0, 0, 0)
    """, (account_id, today, hour))


def get_hourly_mutations(account_id: int) -> int:
    today, hour = _budget_key(account_id)
    with db_cursor() as cur:
        _ensure_budget_row(cur, account_id, today, hour)
        cur.execute(
            "SELECT mutations_used FROM action_budget WHERE account_id=? AND budget_date=? AND budget_hour=?",
            (account_id, today, hour)
        )
        row = cur.fetchone()
        return row["mutations_used"] if row else 0


def get_daily_issues(account_id: int) -> int:
    today, _ = _budget_key(account_id)
    with db_cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(issues_used), 0) as total FROM action_budget WHERE account_id=? AND budget_date=?",
            (account_id, today)
        )
        row = cur.fetchone()
        return row["total"] if row else 0


def get_daily_prs(account_id: int) -> int:
    today, _ = _budget_key(account_id)
    with db_cursor() as cur:
        cur.execute(
            "SELECT COALESCE(SUM(prs_used), 0) as total FROM action_budget WHERE account_id=? AND budget_date=?",
            (account_id, today)
        )
        row = cur.fetchone()
        return row["total"] if row else 0


def increment_mutations(account_id: int, count: int = 1):
    today, hour = _budget_key(account_id)
    with db_cursor() as cur:
        _ensure_budget_row(cur, account_id, today, hour)
        cur.execute(
            "UPDATE action_budget SET mutations_used = mutations_used + ? WHERE account_id=? AND budget_date=? AND budget_hour=?",
            (count, account_id, today, hour)
        )


def increment_issues(account_id: int, count: int = 1):
    today, hour = _budget_key(account_id)
    with db_cursor() as cur:
        _ensure_budget_row(cur, account_id, today, hour)
        cur.execute(
            "UPDATE action_budget SET issues_used = issues_used + ? WHERE account_id=? AND budget_date=? AND budget_hour=?",
            (count, account_id, today, hour)
        )


def increment_prs(account_id: int, count: int = 1):
    today, hour = _budget_key(account_id)
    with db_cursor() as cur:
        _ensure_budget_row(cur, account_id, today, hour)
        cur.execute(
            "UPDATE action_budget SET prs_used = prs_used + ? WHERE account_id=? AND budget_date=? AND budget_hour=?",
            (count, account_id, today, hour)
        )


# ---------------------------------------------------------------------------
# Automation state (global stop flag etc.)
# ---------------------------------------------------------------------------

def set_state(key: str, value: str):
    with db_cursor() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO automation_state (key, value) VALUES (?, ?)",
            (key, value)
        )


def get_state(key: str, default: str = "") -> str:
    with db_cursor() as cur:
        cur.execute("SELECT value FROM automation_state WHERE key=?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

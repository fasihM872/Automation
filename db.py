"""Small database layer for persistent campaign leads and send memory."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config


SQLITE_PATH = Path(os.getenv("SQLITE_PATH", config.DATA_DIR / "automation.db"))


def _database_url():
    return os.getenv("DATABASE_URL", "").strip()


def _is_postgres():
    return _database_url().startswith(("postgres://", "postgresql://"))


def _placeholder():
    return "%s" if _is_postgres() else "?"


@contextmanager
def connect():
    if _is_postgres():
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Install psycopg to use DATABASE_URL: pip install 'psycopg[binary]'") from exc
        conn = psycopg.connect(_database_url())
    else:
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _execute(conn, sql, params=()):
    if _is_postgres():
        sql = sql.replace("?", "%s")
    return conn.execute(sql, params)


def init_db():
    with connect() as conn:
        if _is_postgres():
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id SERIAL PRIMARY KEY,
                    niche TEXT NOT NULL,
                    source_name TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    sent_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            _execute(conn, "CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_unique ON leads (niche, email, phone)")
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS sent_emails (
                    id SERIAL PRIMARY KEY,
                    sent_at TEXT NOT NULL,
                    niche TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    template_name TEXT NOT NULL DEFAULT '',
                    template_url TEXT NOT NULL DEFAULT '',
                    email_status TEXT NOT NULL DEFAULT '',
                    whatsapp_status TEXT NOT NULL DEFAULT ''
                )
                """,
            )
        else:
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    niche TEXT NOT NULL,
                    source_name TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    sent_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(niche, email, phone)
                )
                """,
            )
            _execute(
                conn,
                """
                CREATE TABLE IF NOT EXISTS sent_emails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sent_at TEXT NOT NULL,
                    niche TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    template_name TEXT NOT NULL DEFAULT '',
                    template_url TEXT NOT NULL DEFAULT '',
                    email_status TEXT NOT NULL DEFAULT '',
                    whatsapp_status TEXT NOT NULL DEFAULT ''
                )
                """,
            )


def _rows(cursor):
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def has_leads(niche):
    init_db()
    with connect() as conn:
        cursor = _execute(conn, "SELECT COUNT(*) AS count FROM leads WHERE niche = ?", (niche,))
        row = cursor.fetchone()
        return (row["count"] if not _is_postgres() else row[0]) > 0


def import_leads(niche, source_name, leads):
    init_db()
    added = 0
    updated = 0
    with connect() as conn:
        for lead in leads:
            cursor = _execute(
                conn,
                "SELECT id, status FROM leads WHERE niche = ? AND email = ? AND phone = ?",
                (niche, lead.email, lead.phone),
            )
            existing = cursor.fetchone()
            if existing:
                lead_id = existing["id"] if not _is_postgres() else existing[0]
                _execute(
                    conn,
                    """
                    UPDATE leads
                    SET source_name = ?, name = ?, address = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (source_name, lead.name, lead.address, lead_id),
                )
                updated += 1
            else:
                _execute(
                    conn,
                    """
                    INSERT INTO leads (niche, source_name, name, email, phone, address)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (niche, source_name, lead.name, lead.email, lead.phone, lead.address),
                )
                added += 1
    return {"added": added, "updated": updated}


def get_leads(niche):
    init_db()
    with connect() as conn:
        cursor = _execute(conn, "SELECT * FROM leads WHERE niche = ? ORDER BY id", (niche,))
        return _rows(cursor)


def get_pending_leads(niche, limit=None):
    init_db()
    sql = "SELECT * FROM leads WHERE niche = ? AND status != 'sent' ORDER BY id"
    params = [niche]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    with connect() as conn:
        cursor = _execute(conn, sql, tuple(params))
        return _rows(cursor)


def sent_today_count(niche, today):
    init_db()
    with connect() as conn:
        cursor = _execute(
            conn,
            "SELECT COUNT(*) AS count FROM sent_emails WHERE niche = ? AND email_status = 'sent' AND sent_at LIKE ?",
            (niche, f"{today}%"),
        )
        row = cursor.fetchone()
        return row["count"] if not _is_postgres() else row[0]


def get_sent_rows():
    init_db()
    with connect() as conn:
        cursor = _execute(conn, "SELECT * FROM sent_emails ORDER BY id", ())
        return _rows(cursor)


def delete_sent_email(sent_id):
    init_db()
    with connect() as conn:
        cursor = _execute(conn, "SELECT * FROM sent_emails WHERE id = ?", (sent_id,))
        row = cursor.fetchone()
        if not row:
            return None
        removed = dict(row) if not _is_postgres() else dict(zip([column[0] for column in cursor.description], row))
        _execute(conn, "DELETE FROM sent_emails WHERE id = ?", (sent_id,))
        return removed


def mark_sent(niche, lead, template_name, template_url, email_status="sent", whatsapp_status="disabled", sent_at=""):
    init_db()
    with connect() as conn:
        _execute(
            conn,
            """
            UPDATE leads
            SET status = 'sent', sent_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE niche = ? AND email = ? AND phone = ?
            """,
            (sent_at, niche, lead.email, lead.phone),
        )
        _execute(
            conn,
            """
            INSERT INTO sent_emails (
                sent_at, niche, name, email, phone, template_name, template_url, email_status, whatsapp_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sent_at,
                niche,
                lead.name,
                lead.email,
                lead.phone,
                template_name,
                template_url,
                email_status,
                whatsapp_status,
            ),
        )

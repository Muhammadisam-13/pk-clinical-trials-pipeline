"""Small database helpers: open a SQLite connection and create the schema."""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


def connect(db_path):
    """Open the SQLite file (creating its folder if needed) with foreign keys enforced."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite ignores REFERENCES unless this is on
    return conn


def init_db(conn):
    """Create all tables and indexes if they don't exist yet. Safe to call on every run."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()

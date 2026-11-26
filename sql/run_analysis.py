"""Run every query in sql/analysis.sql against the pipeline's SQLite DB and print the results."""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "ctgov_pk.db"
SQL_PATH = ROOT / "sql" / "analysis.sql"

sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252; city names contain e.g. "ā"
conn = sqlite3.connect(DB_PATH)
for statement in SQL_PATH.read_text(encoding="utf-8").split(";"):
    lines = [l for l in statement.strip().splitlines() if not l.startswith("--")]
    comments = [l for l in statement.strip().splitlines() if l.startswith("-- ") and l[3:4].isdigit()]
    if not lines:
        continue
    cursor = conn.execute("\n".join(lines))
    print("\n" + (comments[0] if comments else "--"))
    print(" | ".join(col[0] for col in cursor.description))
    for row in cursor.fetchall():
        print(" | ".join("" if v is None else str(v) for v in row))
conn.close()

"""
Load a snapshot produced by export_data.py into the local database.

By default this MERGES (INSERT OR REPLACE by primary key) — re-running it with
the same file is safe and idempotent, and it won't delete rows that only exist
locally. Pass --replace to wipe each table first for an exact restore.

Usage:
    python src/import_data.py                          # merge data/kassel_export.json
    python src/import_data.py /path/to/backup.json     # merge a custom file
    python src/import_data.py /path/to/backup.json --replace   # exact restore
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from database import get_conn, init_db

# Insert parents before children so foreign keys resolve.
TABLES = ["listings", "applications", "action_queue", "email_events", "scrape_runs", "sync_state"]


def import_data(in_path: str, replace: bool = False) -> None:
    init_db()
    snapshot = json.loads(Path(in_path).read_text(encoding="utf-8"))

    counts = {}
    with get_conn() as conn:
        if replace:
            # Delete children first to respect foreign keys.
            for table in reversed(TABLES):
                conn.execute(f"DELETE FROM {table}")

        for table in TABLES:
            rows = snapshot.get(table, [])
            for row in rows:
                cols = list(row.keys())
                collist = ", ".join(cols)
                placeholders = ", ".join("?" for _ in cols)
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
            counts[table] = len(rows)

    print(f"Imported from {in_path} ({'replace' if replace else 'merge'} mode)")
    for table, n in counts.items():
        print(f"  {table:15} {n}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--replace"]
    replace = "--replace" in sys.argv
    in_path = args[0] if args else str(
        Path(__file__).parent.parent / "data" / "kassel_export.json"
    )
    if not Path(in_path).exists():
        print(f"File not found: {in_path}")
        sys.exit(1)
    import_data(in_path, replace=replace)

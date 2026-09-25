"""
Export your whole dashboard state (captured listings, applications, action
queue, emails, sync state) to a single portable JSON file. Carry that file to
another machine and load it with import_data.py.

Usage:
    python src/export_data.py                      # -> data/kassel_export.json
    python src/export_data.py /path/to/backup.json # custom path
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from database import get_conn, init_db

# Order matters for a clean re-import: listings before the rows that reference them.
TABLES = ["listings", "applications", "action_queue", "email_events", "scrape_runs", "sync_state"]


def export_data(out_path: str) -> None:
    init_db()
    snapshot = {
        "_meta": {
            "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "format_version": 1,
        }
    }
    counts = {}
    with get_conn() as conn:
        for table in TABLES:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
            snapshot[table] = rows
            counts[table] = len(rows)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Exported to {out}")
    for table, n in counts.items():
        print(f"  {table:15} {n}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).parent.parent / "data" / "kassel_export.json"
    )
    export_data(out)

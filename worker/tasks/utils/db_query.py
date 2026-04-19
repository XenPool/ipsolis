"""Lightweight DB query helper for PowerShell / Bash script modules.

Usage from PowerShell:
    # Simple query (no parameters):
    $rows = python /app/tasks/utils/db_query.py "SELECT id, name FROM asset_pool" | ConvertFrom-Json

    # Parameterized query (extra args become positional $1, $2, ...):
    $rows = python /app/tasks/utils/db_query.py "SELECT id, name FROM asset_pool WHERE status::text = %s" "Reinstall" | ConvertFrom-Json

Returns a JSON array of objects (one per row, keys = column names).
Uses DATABASE_URL from the environment — no credentials needed in scripts.
Exit code 0 on success, 1 on error (error JSON on stdout).
"""

import json
import os
import sys

import psycopg2
import psycopg2.extras


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "Usage: db_query.py <SQL> [param1] [param2] ..."}))
        sys.exit(1)

    sql = sys.argv[1]
    params = tuple(sys.argv[2:]) if len(sys.argv) > 2 else None

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print(json.dumps({"success": False, "error": "DATABASE_URL not set"}))
        sys.exit(1)

    # Strip SQLAlchemy driver prefix if present (e.g. postgresql+psycopg2://)
    dsn = db_url.split("+")[0] + "://" + db_url.split("://", 1)[1] if "+" in db_url else db_url

    try:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        conn.close()

        # RealDictRow → plain dict for JSON serialization
        print(json.dumps([dict(r) for r in rows], default=str))
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()

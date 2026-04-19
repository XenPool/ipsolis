"""Lightweight DB write helper for PowerShell / Bash script modules.

Usage (from PowerShell):
    $sql = "UPDATE asset_pool SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{mac_address}', to_jsonb(%s::text)) WHERE name = %s"
    $res = python /app/tasks/utils/db_execute.py $sql $mac $VMName | ConvertFrom-Json

Executes one INSERT / UPDATE / DELETE statement, commits, prints a JSON
summary on stdout:
    {"success": true,  "rowcount": 1}
    {"success": false, "error": "..."}

Uses DATABASE_URL from the environment. Exit code 0 on success, 1 on error.
"""

import json
import os
import sys

import psycopg2


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"success": False,
                          "error": "Usage: db_execute.py <SQL> [param1] [param2] ..."}))
        sys.exit(1)

    sql = sys.argv[1]
    params = tuple(sys.argv[2:]) if len(sys.argv) > 2 else None

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        print(json.dumps({"success": False, "error": "DATABASE_URL not set"}))
        sys.exit(1)

    # Strip SQLAlchemy driver prefix if present (e.g. postgresql+psycopg2://)
    if "+" in db_url.split("://", 1)[0]:
        scheme, rest = db_url.split("://", 1)
        dsn = scheme.split("+", 1)[0] + "://" + rest
    else:
        dsn = db_url

    try:
        conn = psycopg2.connect(dsn)
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            rc = cur.rowcount
            conn.commit()
            cur.close()
        finally:
            conn.close()
        print(json.dumps({"success": True, "rowcount": rc}))
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()

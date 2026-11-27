"""Read-only probe: which migration is the live store on?

The deployment gates of the pool refactor are stated as 'migration 018
and the new binary go live together'.  This probe reads the migration
bookkeeping of the live store so the statement is checked against the
store instead of against the plan.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    db_path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else 'flashloan/db/local_store_4663.sqlite3'
    ).resolve()
    conn = sqlite3.connect(f'file:{db_path.as_posix()}?mode=ro', uri=True, timeout=60)
    conn.execute('PRAGMA query_only=ON')
    conn.row_factory = sqlite3.Row
    tables = [r[0] for r in conn.execute('SELECT name FROM sqlite_master WHERE type=\'table\'')]
    print('=== migration tables ===')
    print(json.dumps([t for t in tables if 'migrat' in t.lower() or 'schema_version' in t.lower()]))
    for name in tables:
        if 'migrat' not in name.lower():
            continue
        try:
            rows = [dict(r) for r in conn.execute(f'SELECT * FROM {name} ORDER BY rowid DESC LIMIT 6')]
        except sqlite3.Error as error:
            rows = [{'error': str(error)}]
        print(f'=== {name} ===')
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, default=str))
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

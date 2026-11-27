'''Read-only probe: the live status of the maintenance worker slots.

Every slot keeps its own cursor in the initialization store, so the status
of the liquidity slot (and of the broadcast slot) is read from there.
'''

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB = Path('flashloan/db/local_store_4663.sqlite3').resolve()


def main() -> int:
    connection = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=60)
    connection.execute('PRAGMA query_only=ON')
    connection.row_factory = sqlite3.Row
    tables = [str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    print('tables with initial/cursor/chain: ',
          [name for name in tables if any(k in name for k in ('initial', 'cursor', 'chain_biz'))])
    for name in tables:
        if 'initial' not in name:
            continue
        columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info({name})')]
        print(f'--- {name} columns: {columns}')
        try:
            rows = connection.execute(f'SELECT * FROM {name} ORDER BY rowid DESC LIMIT 40').fetchall()
        except sqlite3.Error as error:
            print('   read error:', error)
            continue
        for row in rows:
            item = dict(row)
            blob = ' '.join(str(value) for value in item.values())
            if 'v4_' in blob:
                trimmed = {key: (str(value)[:260] if value is not None else None)
                           for key, value in item.items()}
                print('  ' + json.dumps(trimmed, ensure_ascii=False, default=str))
    connection.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

'''Read-only probe: does every closed loop produce its own review rows?

The user rule is that every closed loop keeps its own review history, rows
accumulate over time, and a loop that stops writing must report an error
instead of going silent.  This probe prints the loop coverage of the review
table against the closed loop table, the rows per loop and how many loops
carry a zero address in their token path.
'''

from __future__ import annotations

import sqlite3
from pathlib import Path


DB = Path(__file__).resolve().parents[2].parent / 'flashloan' / 'db' / 'local_store_4663.sqlite3'
ZERO = '0x0000000000000000000000000000000000000000'


def main() -> int:
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')

    tables = [r[0] for r in conn.execute(
        "select name from sqlite_master where type='table' and name like '%closed_loop%'"
    )]
    print('== closed loop tables ==', tables)
    for t in tables:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({t})')]
        print(' ', t, 'rows =', conn.execute(f'select count(*) from {t}').fetchone()[0])
        print('    columns:', cols)

    print('== review loop coverage ==')
    print('  rows            =', conn.execute('select count(*) from manager_v4_reviews').fetchone()[0])
    print('  distinct loops  =', conn.execute(
        'select count(distinct loop_key) from manager_v4_reviews').fetchone()[0])
    print('  rows per loop:')
    for row in conn.execute(
        'select n, count(*) from (select loop_key, count(*) n from manager_v4_reviews'
        ' group by loop_key) group by n order by n'
    ):
        print('   ', row[0], 'rows ->', row[1], 'loops')

    print('== zero address in token path ==')
    print('  loops with zero address =', conn.execute(
        'select count(distinct loop_key) from manager_v4_reviews where loop_key like ?',
        (f'%{ZERO}%',)).fetchone()[0])
    for row in conn.execute(
        'select review_status, count(*) from manager_v4_reviews where loop_key like ?'
        ' group by review_status', (f'%{ZERO}%',)
    ):
        print('   ', row[0], row[1])

    print('== latest review per loop (stale detection) ==')
    for row in conn.execute(
        'select loop_key, max(reviewed_at), count(*) from manager_v4_reviews'
        ' group by loop_key order by 2 asc limit 10'
    ):
        print('  ', row[1], 'rows =', row[2], row[0][:110])

    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

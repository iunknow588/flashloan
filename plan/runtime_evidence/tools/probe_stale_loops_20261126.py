'''Read-only probe: which closed loops stopped writing reviews?

The user rule is that every closed loop keeps producing its own review
output and a loop that stops writing must report an error instead of going
silent.  This probe compares the loops whose newest review is old with the
ones that are still fresh, using the closed loop runtime state table for
the reason, and checks the JSONL archive for the accumulate rule.
'''

from __future__ import annotations

import sqlite3
from pathlib import Path


DB = Path(__file__).resolve().parents[2].parent / 'flashloan' / 'db' / 'local_store_4663.sqlite3'
JSONL_ROOT = Path(__file__).resolve().parents[2].parent / 'flashloan' / 'db' / 'review_record_jsonl'
STALE_BEFORE = '2026-11-26T04:00:00'


def main() -> int:
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')

    print('== fresh vs stale per scope ==')
    for row in conn.execute(
        "select scope_key, case when reviewed_at < ? then 'stale' else 'fresh' end k, count(*)"
        ' from manager_v4_reviews group by scope_key, k order by scope_key, k',
        (STALE_BEFORE,),
    ):
        print(' ', row[0], row[1], row[2])

    print('== stale loops with runtime state ==')
    for row in conn.execute(
        "select r.loop_key, r.reviewed_at, r.review_status, r.scope_key,"
        ' s.runtime_status, s.status_reason, s.review_required, s.review_generation,'
        ' s.last_rebuilt_at, s.updated_at'
        ' from manager_v4_reviews r'
        ' left join manager_v4_closed_loop_runtime_state s'
        '   on s.loop_key = r.loop_key and s.scope_key = r.scope_key'
        ' where r.reviewed_at < ? order by r.reviewed_at',
        (STALE_BEFORE,),
    ):
        print(' ', row[1], row[2], '|', row[3])
        print('      loop   =', row[0])
        print('      runtime=', row[4], '|', row[5])
        print('      required =', row[6], 'gen =', row[7], 'rebuilt =', row[8], 'upd =', row[9])

    print('== runtime state summary (all loops) ==')
    for row in conn.execute(
        'select runtime_status, status_reason, count(*) from manager_v4_closed_loop_runtime_state'
        ' group by runtime_status, status_reason order by 3 desc'
    ):
        print(' ', row[0], '|', row[1], '|', row[2])

    print('== review_required flag ==')
    for row in conn.execute(
        'select review_required, count(*) from manager_v4_closed_loop_runtime_state group by 1'
    ):
        print(' ', row[0], row[1])

    print('== JSONL archive (accumulate check) ==')
    if not JSONL_ROOT.exists():
        print('  root missing:', JSONL_ROOT)
    else:
        files = sorted(p for p in JSONL_ROOT.rglob('*.jsonl'))
        print('  jsonl files =', len(files))
        sizes = [(p.stat().st_size, p) for p in files]
        for size, path in sorted(sizes, reverse=True)[:8]:
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            good = [ln for ln in lines if ln.strip()]
            print(f'   {size:>8} B  lines={len(good):>4}  {path.relative_to(JSONL_ROOT)}')
            if good:
                print('      last:', good[-1][:220])

    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

'''Read-only probe: the deployment gates that the restart made checkable.

The runtime was replaced on 2026-11-26 13:59:28, so several boxes that
were waiting for "a restart" can now be judged.  This probe reads the
store and answers each of them with a number.

Checks:
  1. the review verdicts left `data_incomplete` (T-核.04);
  2. `completed` is no longer constantly zero (T-池复.01);
  3. the published broadcast requests are more than zero (T-复核.01);
  4. the closed-loop hops resolve against the **directional** pool table
     (T-闭.03, target 522 / 522);
  5. the liquidity slot left the `error` status (T-核.05).
'''

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB = Path('flashloan/db/local_store_4663.sqlite3').resolve()


def table_names(connection: sqlite3.Connection) -> list[str]:
    return [str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]


def main() -> int:
    connection = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=60)
    connection.execute('PRAGMA query_only=ON')
    connection.row_factory = sqlite3.Row
    out: dict[str, object] = {}
    tables = table_names(connection)
    out['tables_of_interest'] = [name for name in tables if any(
        key in name for key in ('closed_loop', 'directional', 'maintenance', 'worker', 'liquidity'))]

    # 1 + 2: the review verdicts.
    out['review_verdicts'] = {
        str(row['review_status']): int(row['n'])
        for row in connection.execute(
            'SELECT review_status, COUNT(*) AS n FROM manager_v4_reviews GROUP BY review_status')
    }
    out['review_newest_reviewed_at'] = connection.execute(
        'SELECT MAX(reviewed_at) FROM manager_v4_reviews').fetchone()[0]

    # 3: the published broadcast requests.
    out['broadcast_requests'] = int(connection.execute(
        'SELECT COUNT(*) FROM manager_v4_review_broadcast_requests').fetchone()[0])
    out['execution_attempts'] = int(connection.execute(
        'SELECT COUNT(*) FROM manager_cow_execution_attempts').fetchone()[0])

    # 4: the closed-loop hops against the directional pool table.
    hop_tables = [name for name in tables if 'closed_loop' in name and 'hop' in name]
    out['hop_tables'] = hop_tables
    if hop_tables:
        hop_table = hop_tables[0]
        columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info({hop_table})')]
        out['hop_columns'] = columns
        out['hop_count'] = int(connection.execute(f'SELECT COUNT(*) FROM {hop_table}').fetchone()[0])
        directional_columns = [name for name in columns if 'directional' in name]
        out['directional_columns'] = directional_columns
        if directional_columns:
            scope = 'directional_scope_key'
            pool = 'directional_pool_id'
            if scope not in columns or pool not in columns:
                scope = 'directional_scope_key' if 'directional_scope_key' in columns else None
                pool = 'directional_pool_id' if 'directional_pool_id' in columns else None
            if scope and pool:
                directional_table = next(
                    (name for name in tables
                     if 'directional' in name and 'exchange_pool' in name), None)
                out['directional_table_used'] = directional_table
                if directional_table:
                    dcolumns = [str(row[1]) for row in connection.execute(
                        f'PRAGMA table_info({directional_table})')]
                    out['directional_table_columns'] = dcolumns
                    scope_column = 'scope_key' if 'scope_key' in dcolumns else 'directional_scope_key'
                    if scope_column in dcolumns and pool in dcolumns:
                        resolved = int(connection.execute(
                            f'SELECT COUNT(*) FROM {hop_table} h JOIN {directional_table} d '
                            f'ON d.{scope_column} = h.{scope} '
                            f'AND d.directional_pool_id = h.{pool}').fetchone()[0])
                        out['hops_resolved_by_scope_and_pool'] = resolved
                        if 'direction' in dcolumns and 'hop_direction' in columns:
                            resolved_full = int(connection.execute(
                                f'SELECT COUNT(*) FROM {hop_table} h JOIN {directional_table} d '
                                f'ON d.{scope_column} = h.{scope} '
                                f'AND d.directional_pool_id = h.{pool} '
                                f'AND d.direction = h.hop_direction').fetchone()[0])
                            out['hops_resolved_including_direction'] = resolved_full
                        out['hops_total'] = out['hop_count']
                        out['hops_unresolved'] = int(out['hop_count']) - resolved

    # 5: the maintenance slots.  Every table that carries a slot name and a
    # status is a candidate; the liquidity one is picked out by its name.
    out['slot_tables'] = []
    for name in tables:
        columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info({name})')]
        if 'status' not in columns:
            continue
        label_columns = [key for key in ('slot', 'slot_name', 'name', 'worker', 'worker_name',
                                        'key', 'slot_key') if key in columns]
        if not label_columns:
            continue
        out['slot_tables'].append({'table': name, 'label_columns': label_columns})
        try:
            rows = connection.execute(f'SELECT * FROM {name} ORDER BY rowid DESC LIMIT 60').fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            item = dict(row)
            label = ' '.join(str(item.get(key)) for key in label_columns)
            if 'liquidity' in label.lower() and 'liquidity_slot' not in out:
                out['liquidity_slot'] = {
                    'table': name,
                    'label': label[:120],
                    'status': item.get('status'),
                    'mode': item.get('mode'),
                    'updated_at': item.get('updated_at') or item.get('refreshed_at'),
                    'error': str(item.get('error'))[:300] if item.get('error') else None,
                }

    connection.close()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

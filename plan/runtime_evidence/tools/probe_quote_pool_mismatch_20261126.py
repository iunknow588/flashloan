'''Read-only probe: why does a profitable review not get broadcast?

Compares, for every review row that carries a positive signed pnl:
  * the directional pools of the closed loop and their base pools,
  * the pools the contract quote (run_arbitrage) actually priced,
  * the review status and the reason.

The probe never writes and never opens the database in write mode.
'''

import json
import os
import sqlite3
import sys


DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    'flashloan', 'db', 'local_store_4663.sqlite3',
)


def connect():
    conn = sqlite3.connect('file:' + DB.replace('\\', '/') + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def jload(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def quote_pools(payload):
    '''Every pool id the contract quote names, in order of appearance.'''
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and 'pool' in key.lower() and isinstance(value, str):
                    if value.startswith('0x') and len(value) >= 10:
                        found.append((key, value.lower()))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    seen = []
    for key, value in found:
        if value not in [v for _, v in seen]:
            seen.append((key, value))
    return seen


def main():
    conn = connect()
    cur = conn.cursor()

    print('== directional pool -> base pool map ==')
    try:
        rows = cur.execute(
            '''SELECT directional_pool_id, base_pool_id
                 FROM manager_v4_directional_exchange_pools'''
        ).fetchall()
    except Exception as exc:
        print('directional table unreadable:', exc)
        rows = []
    base_of = {}
    for pool_id, base_pool_id in rows:
        if base_pool_id:
            base_of[(pool_id or '').lower()] = (base_pool_id or '').lower()
    print('directional pools:', len(rows), ' with base:', len(base_of))

    print()
    print('== reviews with positive signed pnl ==')
    cur.execute(
        '''SELECT network, chain_id, loop_key, review_status, review_reason,
                  signed_pnl_raw, single_tick_capacity_anchor_raw,
                  pool_ids_json, run_arbitrage_json, static_preview_json,
                  hop_sqrt_price_limits_json
             FROM manager_v4_reviews
            WHERE signed_pnl_raw IS NOT NULL AND signed_pnl_raw <> '0'
              AND signed_pnl_raw NOT LIKE '-%'
            ORDER BY reviewed_at DESC'''
    )
    reviews = cur.fetchall()
    print('rows with positive signed pnl:', len(reviews))

    for row in reviews:
        (network, chain_id, loop_key, status, reason, pnl, anchor,
         pool_ids_json, run_json, preview_json, hop_json) = row
        run = jload(run_json)
        pools = quote_pools(run)
        print()
        print('---- review', network, chain_id, 'loop', loop_key)
        print('  status      :', status)
        print('  reason      :', reason)
        print('  signed_pnl  :', pnl, ' capacity anchor:', anchor)
        print('  loop pools  :', jload(pool_ids_json))
        print('  loop bases  :', [base_of.get((p or '').lower(), '?') for p in (jload(pool_ids_json) or [])])
        print('  quote pools :')
        for key, value in pools:
            print('      ', key, '=', value)
        quote_ids = [v for _, v in pools]
        loop_bases = [base_of.get((p or '').lower(), '') for p in (jload(pool_ids_json) or [])]
        missing = [p for p in quote_ids if p not in loop_bases]
        extra = [p for p in loop_bases if p not in quote_ids]
        if missing or extra:
            print('  MISMATCH    : quote-only =', missing, ' loop-only =', extra)
        elif quote_ids:
            print('  match       : quote pools == loop base pools')
        hop_states = jload(hop_json)
        if isinstance(hop_states, list):
            print('  hop limits  :', len(hop_states), 'entries')
            for item in hop_states[:8]:
                if isinstance(item, dict):
                    print('      ', {k: item.get(k) for k in
                                     ('hop_index', 'pool_id', 'base_pool_id',
                                      'directional_pool_id', 'direction')
                                     if k in item})
        preview = jload(preview_json)
        if preview:
            calc = preview.get('bestAmountCalculation') or {}
            print('  ladder      :', calc.get('bestAmountLadderStatus'),
                  'probeCount =', calc.get('probeCount'),
                  'measured =', calc.get('bestAmountMeasured'))

    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

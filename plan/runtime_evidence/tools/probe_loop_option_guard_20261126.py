'''Read-only probe: is every pool the review priced a published option?

The closed loop publishes one main pool per hop and keeps the rest as
backups.  The review may only confirm a pool the loop published for that
hop, otherwise the review would confirm a pool the loop never offered.
This probe checks the rule against the routes the contract priced, so the
guard is known before it is enforced.
'''

import json
import os
import sqlite3
import sys


DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    'flashloan', 'db', 'local_store_4663.sqlite3',
)

HOP_KEYS = ['first', 'middle', 'third']


def connect():
    conn = sqlite3.connect('file:' + DB.replace('\\', '/') + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def jload(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def main():
    conn = connect()
    cur = conn.cursor()

    # directional pool id -> base pool id
    base_of = {}
    for dpool, base in cur.execute(
        'SELECT directional_pool_id, base_pool_id FROM manager_v4_directional_exchange_pools'
    ):
        base_of[dpool.lower()] = (base or '').lower()

    checked = 0
    violations = 0
    substitutions = 0
    for loop_key, directionals, backups, run_json in cur.execute(
        '''SELECT r.loop_key, l.directional_pool_ids_json,
                  l.backup_directional_pool_ids_json, r.run_arbitrage_json
             FROM manager_v4_reviews r
             JOIN manager_v4_closed_loops l ON l.loop_key = r.loop_key'''
    ):
        run = jload(run_json)
        if not run or not run.get('ok') or not run.get('executable'):
            continue
        quote = run.get('quote') or {}
        pools = quote.get('candidate_pools') or quote.get('candidatePools') or []
        if not pools:
            continue
        main_pools = jload(directionals) or []
        backup_map = jload(backups) or {}
        checked += 1
        for index, pool in enumerate(pools):
            priced = (pool.get('poolId') or '').lower()
            options = set()
            if index < len(main_pools):
                options.add(base_of.get(main_pools[index].lower(), ''))
            for backup in backup_map.get(HOP_KEYS[index], []) if index < 3 else []:
                options.add(base_of.get(backup.lower(), ''))
            options.discard('')
            if priced not in options:
                violations += 1
                print('VIOLATION loop', loop_key[:40], 'hop', index + 1)
                print('    priced  :', priced)
                print('    options :', options)
            elif index < len(main_pools) and priced != base_of.get(
                main_pools[index].lower(), ''
            ):
                substitutions += 1
                print('SUBSTITUTION loop', loop_key[:40], 'hop', index + 1)
                print('    main    :', base_of.get(main_pools[index].lower(), ''))
                print('    priced  :', priced)
                print('    backups :', [
                    base_of.get(b.lower(), '') for b in backup_map.get(HOP_KEYS[index], [])
                ])

    print()
    print('routes checked      :', checked)
    print('pool substitutions  :', substitutions)
    print('rule violations     :', violations)

    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

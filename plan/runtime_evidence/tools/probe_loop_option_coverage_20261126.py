'''Read-only probe: does every hop of every closed loop publish an option?

The review guard confirms a pool only when the closed loop published it
for that hop: the main pool or one of the backups.  A hop whose option set
is **empty** cannot be checked at all, so the guard has to know how many
such hops exist before it is enforced: an empty set that is treated as a
pass would let a route trade a pool the loop never offered.

This probe measures the coverage of the option sets over the whole closed
loop catalog, so the fail-open / fail-closed choice is made against the
real data and not against a guess.
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

    base_of = {}
    for dpool, base in cur.execute(
        'SELECT directional_pool_id, base_pool_id FROM manager_v4_directional_exchange_pools'
    ):
        base_of[dpool.lower()] = (base or '').lower()

    loops = 0
    hops = 0
    empty_hops = 0
    missing_main = 0
    missing_backup = 0
    empty_examples = []
    for loop_key, directionals, backups in cur.execute(
        '''SELECT loop_key, directional_pool_ids_json,
                  backup_directional_pool_ids_json
             FROM manager_v4_closed_loops'''
    ):
        loops += 1
        main_pools = jload(directionals) or []
        backup_map = jload(backups) or {}
        for index in range(3):
            hops += 1
            options = set()
            main_base = ''
            if index < len(main_pools):
                main_base = base_of.get(main_pools[index].lower(), '')
                if main_base:
                    options.add(main_base)
                else:
                    missing_main += 1
            for backup in backup_map.get(HOP_KEYS[index], []):
                base = base_of.get(backup.lower(), '')
                if base:
                    options.add(base)
                else:
                    missing_backup += 1
            if not options:
                empty_hops += 1
                if len(empty_examples) < 8:
                    empty_examples.append((loop_key[:44], index + 1, len(main_pools)))

    print('closed loops                :', loops)
    print('hops examined               :', hops)
    print('hops with an empty option   :', empty_hops)
    print('hops whose main pool is unresolvable :', missing_main)
    print('hops whose backup is unresolvable     :', missing_backup)
    for example in empty_examples:
        print('    empty hop:', example)

    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

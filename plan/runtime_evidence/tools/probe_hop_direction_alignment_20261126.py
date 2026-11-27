'''Read-only probe: does every pool of a hop carry the hop's direction?

The review confirms a pool by matching the priced base pool against the
**options the closed loop published for that hop**, and the confirmation
also requires the option's direction to be the direction the hop walked
in.  If a hop's backups were published under another direction, the
confirmation would fail for every route that priced one of them.

This probe measures that alignment over the whole catalog.
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


def direction_of(directional_pool_id):
    tail = (directional_pool_id or '').rsplit(':', 1)[-1]
    return tail if tail in ('0', '1') else None


def main():
    conn = connect()
    cur = conn.cursor()

    # The direction every closed loop published for every hop.
    hop_direction = {}
    for loop_key, hop_index, direction in cur.execute(
        '''SELECT loop_key, hop_index, hop_direction
             FROM manager_v4_closed_loop_hops'''
    ):
        hop_direction[(loop_key, int(hop_index))] = direction

    loops = 0
    main_checked = 0
    main_mismatch = 0
    backup_checked = 0
    backup_mismatch = 0
    missing_hop_row = 0
    examples = []
    for loop_key, directionals, backups in cur.execute(
        '''SELECT loop_key, directional_pool_ids_json,
                  backup_directional_pool_ids_json
             FROM manager_v4_closed_loops'''
    ):
        loops += 1
        main_pools = jload(directionals) or []
        backup_map = jload(backups) or {}
        for index in range(3):
            stored = hop_direction.get((loop_key, index + 1))
            if stored is None:
                missing_hop_row += 1
                continue
            if index < len(main_pools):
                main_checked += 1
                if direction_of(main_pools[index]) != str(stored):
                    main_mismatch += 1
                    if len(examples) < 8:
                        examples.append(('main', loop_key[:38], index + 1,
                                         direction_of(main_pools[index]), stored))
            for backup in backup_map.get(HOP_KEYS[index], []):
                backup_checked += 1
                if direction_of(backup) != str(stored):
                    backup_mismatch += 1
                    if len(examples) < 8:
                        examples.append(('backup', loop_key[:38], index + 1,
                                         direction_of(backup), stored))

    print('closed loops                       :', loops)
    print('hops without a hop row             :', missing_hop_row)
    print('main pools checked                 :', main_checked)
    print('main pools with another direction  :', main_mismatch)
    print('backups checked                    :', backup_checked)
    print('backups with another direction     :', backup_mismatch)
    for example in examples:
        print('    mismatch:', example)

    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

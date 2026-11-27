'''Read-only probe: which fee tiers are actually in the pool objects?

Answers the question "is a stale fee ceiling still applied anywhere":
  * the fee distribution of the directional pool layer (the system's pool
    objects, which carry the fee of the base pool),
  * the fee distribution of the raw V4 pool index,
  * how many pools sit above the shared ceiling of 30000,
  * how many of the pools the closed loops actually walk are above it.
'''

import os
import sqlite3
import sys


DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    'flashloan', 'db', 'local_store_4663.sqlite3',
)

CEILING = 30000


def connect():
    conn = sqlite3.connect('file:' + DB.replace('\\', '/') + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def bucket(fee):
    if fee is None:
        return 'null'
    if fee <= 2000:
        return '<=2000 (0.20%)'
    if fee <= 3000:
        return '2001-3000'
    if fee <= 30000:
        return '3001-30000'
    if fee <= 100000:
        return '30001-100000'
    return '>100000'


def main():
    conn = connect()
    cur = conn.cursor()

    print('== directional pool layer (fee of the base pool) ==')
    rows = cur.execute(
        'SELECT fee, COUNT(*) FROM manager_v4_directional_exchange_pools GROUP BY fee ORDER BY fee'
    ).fetchall()
    for fee, count in rows:
        flag = '  <== ABOVE CEILING' if fee and fee > CEILING else ''
        print('   fee {:>10}  count {:>4}  [{}]{}'.format(
            fee, count, bucket(fee), flag))
    above = cur.execute(
        'SELECT COUNT(*) FROM manager_v4_directional_exchange_pools WHERE fee > ?',
        (CEILING,),
    ).fetchone()[0]
    print('   pools above the shared ceiling:', above)

    print()
    print('== raw V4 pool index ==')
    for label, count in cur.execute(
        '''SELECT CASE
                  WHEN fee <= 2000 THEN '1) <=2000'
                  WHEN fee <= 3000 THEN '2) 2001-3000'
                  WHEN fee <= 30000 THEN '3) 3001-30000'
                  WHEN fee <= 100000 THEN '4) 30001-100000'
                  ELSE '5) >100000'
                END b, COUNT(*)
           FROM v4_exchange_pools GROUP BY b ORDER BY b'''
    ):
        print('   ', label, count)
    dynamic = cur.execute(
        'SELECT COUNT(*) FROM v4_exchange_pools WHERE fee & 8388608 != 0'
    ).fetchone()[0]
    print('    dynamic-fee flagged:', dynamic)

    print()
    print('== pools the closed loops actually walk ==')
    walked = cur.execute(
        '''SELECT d.fee, COUNT(*) FROM manager_v4_closed_loops c
             JOIN manager_v4_directional_exchange_pools d
               ON d.directional_pool_id IN (
                   SELECT value FROM json_each(c.directional_pool_ids_json))
            GROUP BY d.fee ORDER BY d.fee'''
    ).fetchall()
    for fee, count in walked:
        print('   fee', fee, 'hops', count)

    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

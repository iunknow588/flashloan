'''Read-only probe: does the live arb contract still cap the fee at 2000?

The decisive evidence is the routes the contract itself priced: every pool
in the quote of a successful ``runArbitrage`` was accepted and priced by
the deployed contract.  A pool with a fee above 2000 in such a quote is a
pool the contract priced, so the contract does not cap the fee at 2000.
'''

import collections
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


def main():
    conn = connect()
    cur = conn.cursor()

    print('== every pool the deployed contract priced, by fee ==')
    fees = collections.Counter()
    above = collections.Counter()
    priced_routes = 0
    for (run_json,) in cur.execute('SELECT run_arbitrage_json FROM manager_v4_reviews'):
        try:
            run = json.loads(run_json) if run_json else {}
        except Exception:
            continue
        if not run.get('ok') or not run.get('executable'):
            continue
        quote = run.get('quote') or {}
        pools = quote.get('candidate_pools') or quote.get('candidatePools') or []
        if not pools:
            continue
        priced_routes += 1
        for pool in pools:
            fee = pool.get('fee')
            if fee is None:
                continue
            fee = int(fee)
            fees[fee] += 1
            if fee > 2000:
                above[fee] += 1

    print('   routes priced by the contract:', priced_routes)
    print('   fee -> hop count:', dict(sorted(fees.items())))
    print('   pools priced with fee ABOVE 2000:', dict(sorted(above.items())))
    total_above = sum(above.values())
    total = sum(fees.values())
    if total:
        print('   share above 2000: {}/{} = {:.1%}'.format(
            total_above, total, total_above / total))

    print()
    print('== the same, for the profitable routes only ==')
    for loop_key, status, reason, run_json, sp in cur.execute(
        '''SELECT loop_key, review_status, review_reason, run_arbitrage_json,
                  static_preview_json
             FROM manager_v4_reviews'''
    ):
        try:
            preview = json.loads(sp) if sp else {}
        except Exception:
            preview = {}
        calculation = preview.get('bestAmountCalculation') or {}
        if calculation.get('bestAmountLadderStatus') != 'profitable_amount_found':
            continue
        try:
            run = json.loads(run_json) if run_json else {}
        except Exception:
            run = {}
        quote = run.get('quote') or {}
        pools = quote.get('candidate_pools') or quote.get('candidatePools') or []
        hop_fees = [p.get('fee') for p in pools]
        print('   ', status, '|', reason)
        print('      hop fees priced by the contract:', hop_fees)
        print('      pool ids:', [p.get('poolId') for p in pools])

    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())

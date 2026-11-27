'''Read-only probe: the closed loop whose capacity folded down to dust.

One review was published as a positive, broadcast ready verdict although its
ladder never measured a size (status single_tick_capacity_below_min_probe,
probe count 0).  The probe prints that loop, its three directional pools and
a healthy loop of the same anchor for comparison.
'''

from __future__ import annotations

import sqlite3
from pathlib import Path


DB = Path(__file__).resolve().parents[2].parent / 'flashloan' / 'db' / 'local_store_4663.sqlite3'


def open_ro():
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def dump_loop(conn, loop_key, label):
    print()
    print('=' * 70)
    print(label, loop_key)
    row = conn.execute(
        'select anchor_symbol, single_tick_capacity_anchor_raw,'
        ' single_tick_capacity_binding_directional_pool_id, borrow_token_usdg_value_raw,'
        ' borrow_token_decimals, cross_tick_review_threshold_usdg_raw,'
        ' cross_tick_capacity_anchor_raw, path_symbols_json'
        ' from manager_v4_closed_loops where loop_key = ?',
        (loop_key,),
    ).fetchone()
    if row is None:
        print('  closed loop row not found')
        return
    print('  anchor =', row[0], '| path =', row[7])
    print('  single_tick_capacity_anchor_raw =', row[1])
    print('  binding pool =', row[2])
    print('  borrow_usdg_value_raw =', row[3], '| decimals =', row[4])
    print('  cross_tick_threshold =', row[5], '| cross_tick_capacity =', row[6])
    for hop in conn.execute(
        'select hop_index, hop_name, directional_pool_id, hop_direction,'
        ' directional_pool_rank, input_address, output_address'
        ' from manager_v4_closed_loop_hops where loop_key = ? order by hop_index',
        (loop_key,),
    ):
        print()
        print('  hop', hop[0], hop[1], '| direction', hop[3], '| rank', hop[4])
        print('    in ', hop[5], '-> out', hop[6])
        pool = conn.execute(
            'select token0, token1, fee, tick, sqrt_price_x96, price, liquidity,'
            ' depth_status, input_capacity_raw, input_capacity_measured,'
            ' directional_output, directional_output_measured, pool_status,'
            ' pool_status_reason, zero_for_one'
            ' from manager_v4_directional_exchange_pools where directional_pool_id = ?',
            (hop[2],),
        ).fetchone()
        if pool is None:
            print('    pool row not found:', hop[2])
            continue
        print('    pool    =', hop[2])
        print('    tokens  =', pool[0], '->', pool[1], '| zero_for_one =', pool[14])
        print('    fee     =', pool[2], '| tick =', pool[3])
        print('    price   =', pool[5], '| sqrt_price_x96 =', pool[4])
        print('    liq     =', pool[6], '| depth_status =', pool[7])
        print('    in_cap  =', pool[8], 'measured =', pool[9])
        print('    out     =', pool[10], 'measured =', pool[11])
        print('    status  =', pool[12], '|', pool[13])


def main() -> int:
    conn = open_ro()
    dust = conn.execute(
        'select loop_key, anchor_symbol, single_tick_capacity_anchor_raw, review_status'
        ' from manager_v4_reviews'
        ' where single_tick_capacity_anchor_raw is not null'
        ' and single_tick_capacity_anchor_raw < 1000000'
        ' order by single_tick_capacity_anchor_raw'
    ).fetchall()
    print('== 容量锚为尘埃量级的复核 ==')
    for row in dust:
        print('  ', row[3], '|', row[1], '| capacity =', row[2], '|', row[0][:110])

    if dust:
        dump_loop(conn, dust[0][0], '尘埃闭环')

    healthy = conn.execute(
        'select r.loop_key, r.anchor_symbol, r.single_tick_capacity_anchor_raw'
        ' from manager_v4_reviews r where r.single_tick_capacity_anchor_raw > 1000000'
        ' and r.anchor_symbol = ? limit 1',
        (dust[0][1] if dust else 'USDG',),
    ).fetchone()
    if healthy:
        dump_loop(conn, healthy[0], '同锚点的正常闭环')
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

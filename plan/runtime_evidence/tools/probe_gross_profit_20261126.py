'''Read-only probe: the gross (fee ignored) profit of every reviewed loop.

The strategy of the system is price first and the fee is ignored, so the
net loss of the contract has to be compared with the fee of the three hops
of the loop.  A loop whose gross profit is positive is a candidate under
that strategy even when the contract refused it because of the fee.
'''

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB = Path(__file__).resolve().parents[2].parent / 'flashloan' / 'db' / 'local_store_4663.sqlite3'


def to_int(value):
    if not value:
        return None
    text = value.strip()
    try:
        if text.startswith('-0x') or text.startswith('0x'):
            return int(text, 16)
        return int(text)
    except ValueError:
        return None


def main() -> int:
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')

    fees = {}
    for pool_id, fee, hooks in conn.execute(
        'select directional_pool_id, fee, hooks from manager_v4_directional_exchange_pools'
    ):
        fees[pool_id] = (fee, hooks)

    hop_fee = {}
    hop_pools = {}
    for loop_key, hop_index, pool_id in conn.execute(
        'select loop_key, hop_index, directional_pool_id from manager_v4_closed_loop_hops'
    ):
        fee, hooks = fees.get(pool_id, (None, None))
        hop_fee.setdefault(loop_key, []).append(fee)
        hop_pools.setdefault(loop_key, []).append((pool_id, hooks))

    print('== 方向池费率分布 ==')
    seen = {}
    for pool_id, (fee, hooks) in fees.items():
        seen[fee] = seen.get(fee, 0) + 1
    for fee, count in sorted(seen.items(), key=lambda kv: (kv[0] is None, kv[0])):
        rate = 'n/a' if fee is None else '%.4f%%' % (fee / 1e6 * 100)
        print('  fee=%-12s %-10s %d 个池' % (fee, rate, count))
    hooked = sum(1 for _, hooks in fees.values() if hooks)
    print('  带 hook 的池 =', hooked)

    rows = []
    query = (
        'select loop_key, review_status, signed_pnl_raw, run_arbitrage_json,'
        ' static_preview_json, anchor_symbol, reviewed_at from manager_v4_reviews'
    )
    for loop_key, status, pnl_raw, run_json, static_json, anchor, at in conn.execute(query):
        run = json.loads(run_json) if run_json else {}
        static = json.loads(static_json) if static_json else {}
        best = static.get('bestAmountCalculation') or {}
        notional = to_int(run.get('initialAmountRaw')) or to_int(best.get('initialAmountRaw'))
        pnl = to_int(pnl_raw)
        if notional is None or pnl is None or notional == 0:
            continue
        net = pnl / notional
        hops = hop_fee.get(loop_key, [])
        total_fee = sum(f for f in hops if f is not None) / 1e6 if hops else None
        gross = net + total_fee if total_fee is not None else None
        rows.append((gross, net, total_fee, status, anchor, at, loop_key, len(hops)))

    print()
    print('== 逐跳费率完整性 ==')
    print('  有费率读数的复核 =', sum(1 for r in rows if r[2] is not None))
    print('  缺费率的复核     =', sum(1 for r in rows if r[2] is None))
    print('  跳数分布         =', sorted({r[7] for r in rows}))

    print()
    print('== 忽略费率后的毛收益（正 = 价格有优势） ==')
    positive = [r for r in rows if r[0] is not None and r[0] > 0]
    positive.sort(reverse=True)
    print('  毛收益为正的复核 =', len(positive), '/', len(rows))
    for gross, net, fee, status, anchor, at, loop_key, hops in positive[:12]:
        print('  毛 %+.4f%%  净 %+.4f%%  费率 %.4f%%  %-16s %-6s %s'
              % (gross * 100, net * 100, (fee or 0) * 100, status, anchor, at))

    print()
    print('== 净收益为正但被判不可执行的复核 ==')
    for gross, net, fee, status, anchor, at, loop_key, hops in sorted(
        rows, key=lambda r: -r[1]
    )[:12]:
        if net <= 0:
            break
        print('  净 %+.4f%%  毛 %+.4f%%  费率 %.4f%%  %-16s %-6s'
              % (net * 100, (gross or 0) * 100, (fee or 0) * 100, status, anchor))

    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

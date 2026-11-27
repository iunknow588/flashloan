'''Read-only probe: is the negative profit a price loss or the swap fee?

The strategy of the system is price first, ignore the fee.  Every
not_executable review carries a negative signed pnl.  If that loss is a
constant fraction of the borrowed amount, the loss is the pool fee and the
price itself is flat; if it is spread out, the routes really lose money.
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


def ratio_bucket(pct):
    if pct < -1:
        return '<-1%'
    if pct < -0.5:
        return '-1%..-0.5%'
    if pct < -0.2:
        return '-0.5%..-0.2%'
    if pct < -0.1:
        return '-0.2%..-0.1%'
    if pct < 0:
        return '-0.1%..0%'
    return '>=0%'


def main() -> int:
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')

    buckets = {}
    rows = []
    query = (
        'select review_status, signed_pnl_raw, run_arbitrage_json,'
        ' static_preview_json, anchor_symbol from manager_v4_reviews'
        ' order by reviewed_at desc'
    )
    for status, pnl_raw, run_json, static_json, anchor in conn.execute(query):
        run = json.loads(run_json) if run_json else {}
        static = json.loads(static_json) if static_json else {}
        best = static.get('bestAmountCalculation') or {}
        notional = to_int(run.get('initialAmountRaw')) or to_int(best.get('initialAmountRaw'))
        pnl = to_int(pnl_raw)
        if notional is None or pnl is None or notional == 0:
            continue
        ratio = pnl / notional
        rows.append((ratio, status, anchor, run.get('revertSelector'), run.get('executable')))
        if status == 'not_executable':
            key = ratio_bucket(ratio * 100)
            buckets[key] = buckets.get(key, 0) + 1

    print('== not_executable 净损益 / 借款本金 的分布 ==')
    for key in ('>=0%', '-0.1%..0%', '-0.2%..-0.1%', '-0.5%..-0.2%', '-1%..-0.5%', '<-1%'):
        if key in buckets:
            print('  %-14s %4d' % (key, buckets[key]))

    print()
    print('== 全部复核的比例统计 ==')
    ratios = sorted(r[0] for r in rows)
    if ratios:
        print('  n =', len(ratios))
        print('  min  = %.4f%%' % (ratios[0] * 100))
        print('  p25  = %.4f%%' % (ratios[len(ratios) // 4] * 100))
        print('  mid  = %.4f%%' % (ratios[len(ratios) // 2] * 100))
        print('  p75  = %.4f%%' % (ratios[len(ratios) * 3 // 4] * 100))
        print('  max  = %.4f%%' % (ratios[-1] * 100))

    print()
    print('== 比例最接近 0 的 10 条 ==')
    for ratio, status, anchor, selector, executable in sorted(rows, key=lambda r: -r[0])[:10]:
        print('  %+.4f%%  %-18s %-6s exec=%s sel=%s' % (
            ratio * 100, status, anchor, executable, selector))

    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

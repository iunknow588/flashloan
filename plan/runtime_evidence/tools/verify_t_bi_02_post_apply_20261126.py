'''Read-only probe: the post-apply re-read that T-闭.02 asks for.

`D-150` left two boxes open because the apply had not happened yet: the
loop count after the apply, and a **row by row** re-read of the readings
the A/B stage predicted.  The apply has happened (migration 018) and the
closed-loop layer has been rebuilt on top of it, so the re-read can be done
against the live store.

The prediction lives in `valuation_snapshot_ab_20261126.json`:
`valueUsdgRawAfter` is what the store was predicted to hold after the
valuation source moves to the market snapshot.
'''

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB = Path('flashloan/db/local_store_4663.sqlite3').resolve()
AB = Path('plan/runtime_evidence/valuation_snapshot_ab_20261126.json').resolve()


def main() -> int:
    prediction = json.loads(AB.read_text(encoding='utf-8'))
    predicted = {row['loopKey']: row for row in prediction.get('loops') or []}

    connection = sqlite3.connect(f'file:{DB.as_posix()}?mode=ro', uri=True, timeout=60)
    connection.execute('PRAGMA query_only=ON')
    connection.row_factory = sqlite3.Row
    live = {str(row['loop_key']): dict(row) for row in connection.execute(
        'SELECT loop_key, borrow_token_usdg_value_raw, single_tick_capacity_anchor_raw, '
        'single_tick_capacity_binding_directional_pool_id, directional_pool_ids_json, '
        'cross_tick_review_threshold_usdg_raw, borrow_token_decimals '
        'FROM manager_v4_closed_loops')}
    connection.close()

    out: dict[str, object] = {
        'predicted_loops': len(predicted),
        'live_loops': len(live),
        'count_unchanged': len(predicted) == len(live),
    }
    missing = sorted(set(predicted) - set(live))
    extra = sorted(set(live) - set(predicted))
    out['missing_in_live'] = len(missing)
    out['extra_in_live'] = len(extra)

    matched = 0
    mismatched: list[dict] = []
    changed_predicted = 0
    changed_live = 0
    for key, row in predicted.items():
        before = str(row.get('valueUsdgRawBefore'))
        after = str(row.get('valueUsdgRawAfter'))
        if before != after:
            changed_predicted += 1
        current = live.get(key)
        if current is None:
            continue
        actual = str(current.get('borrow_token_usdg_value_raw'))
        if before != after:
            changed_live += 1
        if actual == after:
            matched += 1
        else:
            mismatched.append({
                'loopKey': key,
                'predictedAfter': after,
                'live': actual,
                'predictedBefore': before,
                'liveEqualsBefore': actual == before,
            })
    out['value_matches_prediction'] = matched
    out['value_mismatches'] = len(mismatched)
    out['loops_predicted_to_change'] = changed_predicted
    out['loops_whose_prediction_was_a_change'] = changed_live
    # The prediction was made against a point-in-time market snapshot; the
    # live rebuild used a fresher one.  What the prediction can still be
    # held to is the **direction** of the move and the fact that the
    # unchanged rows stayed unchanged.
    same_direction = 0
    opposite_direction = 0
    opposite_rows: list[dict] = []
    unchanged_rows = 0
    unchanged_rows_still_unchanged = 0
    for key, row in predicted.items():
        current = live.get(key)
        if current is None:
            continue
        before = int(row.get('valueUsdgRawBefore') or 0)
        after = int(row.get('valueUsdgRawAfter') or 0)
        actual = int(current.get('borrow_token_usdg_value_raw') or 0)
        if before == after:
            unchanged_rows += 1
            if actual == before:
                unchanged_rows_still_unchanged += 1
            continue
        if (after - before) == 0 or (actual - before) == 0:
            continue
        if (after - before > 0) == (actual - before > 0):
            same_direction += 1
        else:
            opposite_direction += 1
            opposite_rows.append({
                'loopKey': key,
                'before': before,
                'predictedAfter': after,
                'live': actual,
                'liveEqualsBefore': actual == before,
                'predictedDelta': after - before,
                'liveDelta': actual - before,
            })
    out['changed_rows_moving_the_same_direction'] = same_direction
    out['changed_rows_moving_the_opposite_direction'] = opposite_direction
    out['opposite_direction_rows'] = opposite_rows
    out['unchanged_predicted_rows'] = unchanged_rows
    out['unchanged_predicted_rows_still_unchanged'] = unchanged_rows_still_unchanged
    out['ab_probe_metadata'] = {
        key: prediction.get(key) for key in ('probe', 'store', 'valuationChange', 'coverage')
    }
    out['mismatch_sample'] = mismatched[:10]
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

'''Watch the live review generation and report the D-197 readouts.

The D-197 deployment changed what a review row carries: the pools the
review **confirmed** (the pools it priced) instead of the pools the
catalog published, and the path key that names them.  This watcher polls
the live store until a **new** generation lands and then reports every
readout the deployment gate asks for.

Read-only: the store is opened with ``mode=ro`` and ``query_only``.
'''

import json
import os
import sqlite3
import sys
import time


REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
DB = os.path.join(REPO, 'flashloan', 'db', 'local_store_4663.sqlite3')
OUT = os.path.join(REPO, 'plan', 'runtime_evidence', 'd197_confirmed_pool_readout.json')


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


def readout(conn):
    cur = conn.cursor()
    latest = cur.execute('SELECT MAX(reviewed_at) FROM manager_v4_reviews').fetchone()[0]
    status = dict(cur.execute(
        'SELECT review_status, COUNT(*) FROM manager_v4_reviews GROUP BY 1').fetchall())
    reasons = {}
    for reason, count in cur.execute(
            '''SELECT COALESCE(review_reason, '<none>'), COUNT(*)
                 FROM manager_v4_reviews GROUP BY 1'''):
        reasons[reason[:96]] = count

    # The path key of a row written by the new binary names the loop and
    # then the **confirmed** pools; the old one named the catalog's path.
    new_shape = 0
    old_shape = 0
    for loop_key, path_key in cur.execute(
            'SELECT loop_key, pool_path_key FROM manager_v4_reviews'):
        if path_key and path_key.startswith(loop_key + ':' + loop_key.split(':')[0]):
            new_shape += 1
        elif path_key:
            old_shape += 1

    # The pools the review confirmed: are they the catalog's main pools,
    # or a candidate the loop published for that hop?
    substitutions = []
    for loop_key, pool_ids, directionals, backups in cur.execute(
            '''SELECT r.loop_key, r.pool_ids_json, l.directional_pool_ids_json,
                      l.backup_directional_pool_ids_json
                 FROM manager_v4_reviews r
                 JOIN manager_v4_closed_loops l ON l.loop_key = r.loop_key'''):
        confirmed = jload(pool_ids) or []
        main = jload(directionals) or []
        backup_map = jload(backups) or {}
        for index, pool in enumerate(confirmed):
            if index < len(main) and pool != main[index]:
                options = list(main) + [p for values in backup_map.values()
                                        for p in values]
                substitutions.append({
                    'loop': loop_key[:44],
                    'hop': index + 1,
                    'confirmed': pool,
                    'catalog_main': main[index] if index < len(main) else None,
                    'in_options': pool in options,
                })

    transition_reasons = {key: value for key, value in reasons.items()
                          if 'state_transition_gate_failed' in key}
    pool_state_unavailable = sum(
        value for key, value in reasons.items()
        if 'expected_pool_state_unavailable' in key)
    ladder_blocked = sum(value for key, value in reasons.items()
                         if key.startswith('ladder_not_measured'))
    outside_options = sum(value for key, value in reasons.items()
                          if key.startswith('pool_not_in_loop_options'))

    requests = cur.execute(
        '''SELECT COUNT(*), SUM(CASE WHEN request_status = 'generated' THEN 1 ELSE 0 END)
             FROM manager_v4_review_broadcast_requests''').fetchone()
    attempts = cur.execute('SELECT COUNT(*) FROM manager_cow_execution_attempts').fetchone()[0]

    candidate_diagnostics = cur.execute(
        '''SELECT COUNT(*) FROM manager_v4_review_diagnostics
            WHERE diagnostic_code = 'CandidateSubstitution' ''').fetchone()[0]

    return {
        'observed_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'latest_reviewed_at': latest,
        'status_counts': status,
        'new_path_key_rows': new_shape,
        'old_path_key_rows': old_shape,
        'confirmed_pool_substitutions': substitutions,
        'transition_gate_reasons': transition_reasons,
        'expected_pool_state_unavailable': pool_state_unavailable,
        'ladder_not_measured_rows': ladder_blocked,
        'pool_not_in_loop_options_rows': outside_options,
        'candidate_substitution_diagnostics': candidate_diagnostics,
        'broadcast_requests_total': requests[0] or 0,
        'broadcast_requests_generated': requests[1] or 0,
        'execution_attempts': attempts,
        'reasons': reasons,
    }


def main():
    baseline = None
    deadline = time.time() + 60 * 60 * 3
    while time.time() < deadline:
        try:
            conn = connect()
            current = readout(conn)
            conn.close()
        except Exception as error:  # pragma: no cover - read-only probe
            current = {'error': str(error)}
        if baseline is None:
            baseline = current.get('latest_reviewed_at')
        if current.get('latest_reviewed_at') and current['latest_reviewed_at'] != baseline:
            current['baseline_reviewed_at'] = baseline
            current['new_generation'] = True
            with open(OUT, 'w', encoding='utf-8') as handle:
                json.dump(current, handle, ensure_ascii=False, indent=2)
            print(json.dumps(current, ensure_ascii=False, indent=2), flush=True)
            return 0
        print('waiting: latest_reviewed_at=' + str(current.get('latest_reviewed_at')), flush=True)
        time.sleep(45)
    print('timeout: no new review generation landed', flush=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())

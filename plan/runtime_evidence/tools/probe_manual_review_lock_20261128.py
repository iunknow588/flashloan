import json, sys, time, sqlite3, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8')
DB = 'file:flashloan/db/local_store_4663.sqlite3?mode=ro'
SQL = ('SELECT loop_key, runtime_status, status_reason, last_reviewed_at, next_review_at'
       ' FROM manager_v4_closed_loop_runtime_state'
       " WHERE network='robinhood' AND chain_id=4663 ORDER BY last_reviewed_at")


def rows():
    con = sqlite3.connect(DB, uri=True)
    con.execute('PRAGMA query_only=ON')
    out = con.execute(SQL).fetchall()
    con.close()
    return out


def post(path, body, base, request_id):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json',
                                          'X-Request-Id': request_id,
                                          'Idempotency-Key': request_id},
                                 method='POST')
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.status, response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode('utf-8', 'replace')


def main():
    before = rows()
    target = next((row for row in before if row[1] == 'cooldown'), None)
    if target is None:
        print('no loop is in cooldown; nothing to probe')
        return
    print('target loop:', target[0])
    print('  before:', target[1], '|', target[2], '| last=', target[3], '| next=', target[4])
    request_id = 'probe-manual-review-' + str(int(time.time()))
    status, body = post('/v1/closed-loops/review-request?chainId=4663',
                        {'chainId': 4663, 'dryRun': False, 'waitSeconds': 0, 'loopKey': target[0]},
                        'http://127.0.0.1:8091', request_id)
    print('  manager-api review-request ->', status, body[:260])
    time.sleep(4)
    now = {row[0]: row for row in rows()}.get(target[0])
    print('  after :', now[1], '|', now[2], '| last=', now[3], '| next=', now[4])
    print('  => the review start moved:', now[3] != target[3])


if __name__ == '__main__':
    main()

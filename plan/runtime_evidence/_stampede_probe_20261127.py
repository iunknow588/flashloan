import sqlite3, time, threading
p = r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3"
QS = [
 "SELECT directional_pool_id, base_pool_id, direction, scope_key, pool_manager, pool_key_json, token0, token1, fee, tick_spacing, hooks, pair_key_id, input_token_key_id, output_token_key_id, zero_for_one, pair_direction_rank, pool_status FROM manager_v4_directional_exchange_pools WHERE network='robinhood' AND chain_id=4663 AND protocol='uniswap_v4' ORDER BY scope_key, pair_key_id, direction, pair_direction_rank, base_pool_id",
 "SELECT pool_id, has_liquidity FROM v4_exchange_pool_liquidity_snapshots WHERE network='robinhood' AND chain_id=4663 AND protocol='uniswap_v4' ORDER BY pool_id",
 "SELECT token_address FROM chain_tokens WHERE network='robinhood' AND chain_id=4663 ORDER BY token_address",
 "SELECT * FROM manager_v4_closed_loops WHERE network='robinhood' AND chain_id=4663 AND protocol='uniswap_v4' AND scope_key='core4'",
]
def worker(n, out):
    t = time.time()
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    c.execute("PRAGMA query_only=ON")
    total = 0
    for q in QS:
        total += len(c.execute(q).fetchall())
    out[n] = (time.time()-t, total)
    c.close()
for workers in (1, 32, 176):
    out = {}
    ths = [threading.Thread(target=worker, args=(i, out)) for i in range(workers)]
    t0 = time.time()
    for t in ths: t.start()
    for t in ths: t.join()
    wall = time.time() - t0
    per = sum(v[0] for v in out.values())/workers
    print(f"workers={workers:4d}  wall={wall:7.2f}s  per_worker_avg={per:7.2f}s  rows_total={sum(v[1] for v in out.values()):9d}", flush=True)

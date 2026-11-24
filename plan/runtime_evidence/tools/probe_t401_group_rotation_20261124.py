import json, sqlite3, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
NETWORK="robinhood"; CHAIN_ID=4663; PROTOCOL="uniswap_v4"; SCOPE="core4"
store="flashloan/db/local_store_4663.sqlite3"
base="http://127.0.0.1:5000"
def gj(u):
    with urllib.request.urlopen(u, timeout=180) as h:
        return json.loads(h.read().decode("utf-8","replace"))
def comp(sym):
    p=[str(x or "").strip().lower() for x in (sym or []) if str(x or "").strip()]
    if len(p)==4 and p[0]==p[-1]: p=p[:3]
    if len(p)!=3 or len(set(p))!=3: return None
    return "|".join(p)
snap=gj(base+"/api/dex/opportunity-scanner/snapshot")
routes=((snap.get("snapshot") or {}).get("payload") or {}).get("routes") or []
scanner={}
for r in routes:
    k=comp(r.get("routeSymbols") if isinstance(r.get("routeSymbols"),list) else r.get("symbols"))
    if k: scanner.setdefault(k,[]).append(r)
con=sqlite3.connect("file:"+store.replace("\\","/")+"?mode=ro", uri=True); con.row_factory=sqlite3.Row
rows=list(con.execute("SELECT loop_key, path_symbols_json, pool_path_key FROM manager_v4_closed_loops WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?",(NETWORK,CHAIN_ID,PROTOCOL,SCOPE)))
con.close()
catalog={}; cat_pool={}
for row in rows:
    try: sym=json.loads(row["path_symbols_json"] or "[]")
    except Exception: sym=[]
    k=comp(sym)
    if k: catalog.setdefault(k,[]).append(row["loop_key"])
    cat_pool[row["pool_path_key"]]=row["loop_key"]
extra=sorted(set(scanner)-set(catalog))
print("scanner-only groups:", len(extra))
for g in extra:
    toks=set(g.split("|"))
    hits=[k for k in catalog if set(k.split("|"))==toks]
    print(" ", g, "-> same-token-triple catalog groups:", hits)
# pool path key overlap
scanner_pool=set()
for k,v in scanner.items():
    for r in v:
        pk=r.get("poolPathKey") or "|".join(str(x) for x in (r.get("poolIds") or []))
        if pk: scanner_pool.add(pk)
cat_pools=set(cat_pool)
print("scanner poolPathKeys:", len(scanner_pool), "catalog poolPathKeys:", len(cat_pools))
print("scanner pool keys not in catalog:", len(scanner_pool-cat_pools))
print("catalog pool keys not in scanner:", len(cat_pools-scanner_pool))

import json, sqlite3, sys, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
NETWORK="robinhood"; CHAIN_ID=4663; PROTOCOL="uniswap_v4"; SCOPE="core4"
store="flashloan/db/local_store_4663.sqlite3"
base="http://127.0.0.1:5000"
def gj(u):
    with urllib.request.urlopen(u, timeout=180) as h:
        return json.loads(h.read().decode("utf-8","replace"))
def phys(addr):
    p=[str(x or "").strip().lower() for x in (addr or []) if str(x or "").strip()]
    if len(p)==4 and p[0]==p[-1]: p=p[:3]
    if len(p)!=3: return None
    return ":".join(p)
snap=gj(base+"/api/dex/opportunity-scanner/snapshot")
routes=((snap.get("snapshot") or {}).get("payload") or {}).get("routes") or []
scanner_phys={}
for r in routes:
    a=r.get("routeAddresses") if isinstance(r.get("routeAddresses"),list) else None
    k=phys(a)
    if k: scanner_phys.setdefault(k,0); scanner_phys[k]+=1
con=sqlite3.connect("file:"+store.replace("\\","/")+"?mode=ro", uri=True); con.row_factory=sqlite3.Row
cols=[c[1] for c in con.execute("PRAGMA table_info(manager_v4_closed_loops)")]
print("cols:", [c for c in cols if "path" in c or "pool" in c])
rows=list(con.execute("SELECT loop_key, path_addresses_json, pool_path_key FROM manager_v4_closed_loops WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?",(NETWORK,CHAIN_ID,PROTOCOL,SCOPE)))
con.close()
cat_phys={}
for row in rows:
    try: a=json.loads(row["path_addresses_json"] or "[]")
    except Exception: a=[]
    k=phys(a)
    if k: cat_phys.setdefault(k,[]).append(row["loop_key"])
print("scanner physical keys:", len(scanner_phys))
print("catalog physical keys:", len(cat_phys))
inter=set(scanner_phys)&set(cat_phys)
print("intersection:", len(inter))
print("scanner-only phys:", len(set(scanner_phys)-set(cat_phys)))
print("catalog-only phys:", len(set(cat_phys)-set(scanner_phys)))
for k in sorted(set(scanner_phys)-set(cat_phys))[:3]:
    print("  scanner-only sample:", k)
for k in sorted(set(cat_phys)-set(scanner_phys))[:3]:
    print("  catalog-only sample:", k)
for k in sorted(inter)[:3]:
    print("  shared sample:", k, "->", cat_phys[k])

import sqlite3, sys, json
sys.stdout.reconfigure(encoding="utf-8")
db = "flashloan/db/local_store_4663.sqlite3"
c = sqlite3.connect("file:" + db.replace("\\", "/") + "?mode=ro", uri=True)
c.row_factory = sqlite3.Row

for tbl in ("manager_review_library_state", "manager_v4_closed_loop_library_state"):
    print("=" * 20, tbl)
    try:
        cols = [r[1] for r in c.execute("PRAGMA table_info(%s)" % tbl)]
        print("cols:", cols)
        for r in c.execute("SELECT * FROM %s" % tbl):
            print(json.dumps({k: r[k] for k in r.keys()}, ensure_ascii=False, default=str))
    except Exception as e:
        print("ERR", e)

print("=" * 20, "closed loop catalog")
for r in c.execute("SELECT network, chain_id, protocol, scope_key, COUNT(*) n FROM manager_v4_closed_loops GROUP BY 1,2,3,4"):
    print(dict(r))
c.close()

import sqlite3, time, datetime
from pathlib import Path
DB = Path(r"E:\2026OPC大赛\flashLoan\flashloan\db\local_store_4663.sqlite3")
con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=60)
con.execute("PRAGMA query_only=ON")
cur = con.cursor()
keys = [r[0] for r in cur.execute("SELECT loop_key FROM manager_v4_reviews ORDER BY reviewed_at DESC LIMIT 3")]
print("watching:", keys, flush=True)
for i in range(6):
    print("---", datetime.datetime.now().strftime("%H:%M:%S"), flush=True)
    for k in keys:
        row = cur.execute("SELECT review_status, reviewed_at, updated_at, created_at FROM manager_v4_reviews WHERE loop_key=?", (k,)).fetchone()
        print("   ", k[:40], row, flush=True)
    time.sleep(25)
con.close()

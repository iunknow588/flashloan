import os, sys, time, json
sys.path.insert(0, r"E:\2026OPC大赛\flashLoan\flashloan")
sys.path.insert(1, r"E:\2026OPC大赛\flashLoan\flashloan\src")
os.environ.pop("FLASHLOAN_KV_PATH", None)
from core.rs_bridge import rust_available, rust_load_error
print("rust_available=", rust_available(), "err=", rust_load_error())
from core.db.storage_business_schema import migrate_business_direction_schema
from core.db.database_path import resolve_database_path
p = str(resolve_database_path(None))
print("resolved=", p)
t0=time.time()
try:
    out = migrate_business_direction_schema(p)
    print("migrate ok", out, "secs=", round(time.time()-t0,3))
except Exception as exc:
    print("migrate EXC", type(exc).__name__, exc, "secs=", round(time.time()-t0,3))
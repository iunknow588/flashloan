#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T-复编.02 live-state probe (read-only).

Sub-commands
    coverage  -- closed-loop identity coverage + published review generation
    quality   -- completeness of the live review rows (ladder, amount state)
    identity  -- I-15 directional-layer composite identity recomputation
    items     -- print the acceptance checklist of a collector run
    monitor   -- keep writing the coverage reading until the generation publishes
"""

import collections
import hashlib
import json
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = r"E:\2026OPC大赛\flashLoan"
DB = ROOT + r"\flashloan\db\local_store_4663.sqlite3"
SCOPE = ("robinhood", 4663, "uniswap_v4", "core4")
# The runtime was restarted at 17:35:11 on 2026-11-24; only the rows written
# after that restart belong to the run that carries the route fix.
FIX_RESTART_UTC = "2026-11-24T09:35:11"


def connect():
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def catalog(con):
    return set(r[0].strip().lower() for r in con.execute(
        "SELECT loop_key FROM manager_v4_closed_loops WHERE network=? AND chain_id=?"
        " AND protocol=? AND scope_key=?", SCOPE))


def live_rows(con, since=FIX_RESTART_UTC):
    for r in con.execute("SELECT pair, payload_json, created_at FROM cow_execution_reviews"
                         " WHERE created_at >= ? ORDER BY rowid", (since,)):
        try:
            d = json.loads(r["payload_json"] or "{}")
        except Exception:
            d = {}
        yield r, d


def cmd_coverage():
    con = connect()
    cat = catalog(con)
    seen = set()
    rows = 0
    for _r, d in live_rows(con):
        rows += 1
        lk = (d.get("loopKey") or "").strip().lower()
        if lk:
            seen.add(lk)
    state = con.execute("SELECT review_version, closed_loop_version, loop_count,"
                        " completed_count, data_incomplete_count, not_executable_count,"
                        " generated_at FROM manager_review_library_state").fetchone()
    con.close()
    print(json.dumps({
        "catalog": len(cat),
        "covered": len(seen & cat),
        "extra": len(seen - cat),
        "liveRows": rows,
        "missing": len(cat - seen),
        "publish": dict(zip(["review_version", "closed_loop_version", "loop_count",
                              "completed_count", "data_incomplete_count",
                              "not_executable_count", "generated_at"], state)),
    }, ensure_ascii=False, indent=1))


def cmd_quality():
    con = connect()
    cat = catalog(con)
    last = {}
    noloop = collections.Counter()
    for _r, d in live_rows(con):
        lk = (d.get("loopKey") or "").strip().lower()
        if lk and lk in cat:
            last[lk] = d
        elif not lk:
            noloop[(d.get("auto_execute") or {}).get("reason")] += 1
    calc = [((d.get("bestAmountCalculation") or {})) for d in last.values()]
    print("in-catalog loops with a live review:", len(last))
    print("rows without loopKey by reason:", dict(noloop))
    for field in ("bestAmount", "bestAmountMeasured", "bestAmountConfirmed",
                  "bestAmountMissing", "bestAmountZeroReason"):
        present = sum(1 for c in calc if c.get(field) is not None)
        print("  %-24s present %d / %d" % (field, present, len(calc)))
    ladder = [len(c.get("rows") or c.get("probeRecords") or []) for c in calc]
    print("  ladder rows: min=%s max=%s empty=%d"
          % (min(ladder) if ladder else 0, max(ladder) if ladder else 0,
             sum(1 for n in ladder if n == 0)))
    zero_no_reason = sum(1 for c in calc
                         if c.get("bestAmount") == 0 and not c.get("bestAmountZeroReason"))
    print("  measured zero without a zero reason:", zero_no_reason)
    con.close()


def cmd_recent():
    """How many NEW closed loops were reviewed in each minute."""
    con = connect()
    cat = catalog(con)
    seen = set()
    per_minute = collections.OrderedDict()
    rows_per_minute = collections.Counter()
    for _r, d in live_rows(con):
        minute = (d.get("created_at") or "")[11:16] or (d.get("observed_at") or "")[11:16]
        rows_per_minute[minute] += 1
        lk = (d.get("loopKey") or "").strip().lower()
        if lk and lk not in seen:
            seen.add(lk)
            per_minute[minute] = per_minute.get(minute, 0) + 1
    con.close()
    print("distinct loopKeys:", len(seen & cat), "/", len(cat))
    print("minute | new loops | rows")
    for minute in sorted(set(list(per_minute) + list(rows_per_minute)))[-18:]:
        print("  %s | %3d | %3d" % (minute, per_minute.get(minute, 0), rows_per_minute.get(minute, 0)))
    con = connect()
    mx = con.execute("SELECT MAX(created_at) FROM cow_execution_reviews").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM cow_execution_reviews").fetchone()[0]
    recent = con.execute("SELECT COUNT(*) FROM cow_execution_reviews WHERE created_at >= ?",
                         (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 600)),)).fetchone()[0]
    con.close()
    print("db max created_at:", mx, "| total rows:", total, "| rows in the last 10 min:", recent)
    print("now(local):", time.strftime("%Y-%m-%dT%H:%M:%S"))
    con = connect()
    print("--- last 8 rows by rowid ---")
    for r in con.execute("SELECT rowid, pair, created_at, updated_at FROM cow_execution_reviews"
                         " ORDER BY rowid DESC LIMIT 8"):
        print("   ", tuple(r))
    print("--- columns ---")
    print("   ", [c[1] for c in con.execute("PRAGMA table_info(cow_execution_reviews)")])
    con.close()


def cmd_identity():
    con = connect()
    states = {}
    for r in con.execute("SELECT scope_key, directional_pool_version, materialized_checksum"
                         " FROM manager_v4_directional_pool_library_state ORDER BY scope_key"):
        states[r["scope_key"]] = (r["directional_pool_version"], r["materialized_checksum"])
    closed = con.execute("SELECT directional_pool_version, directional_pool_checksum"
                         " FROM manager_v4_closed_loop_library_state").fetchone()
    con.close()
    payload = "|".join("%s:%d:%s" % (k, v[0], v[1]) for k, v in sorted(states.items()))
    computed = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    print("payload :", payload)
    print("computed:", computed)
    print("stored  :", closed["directional_pool_checksum"])
    print("version :", closed["directional_pool_version"], "vs", max(v[0] for v in states.values()))
    print("MATCH" if computed == closed["directional_pool_checksum"] else "MISMATCH")


def cmd_shape():
    """Top-level keys of the newest live review that carries a loop key."""
    con = connect()
    cat = catalog(con)
    newest = None
    for _r, d in live_rows(con):
        lk = (d.get("loopKey") or "").strip().lower()
        if lk and lk in cat:
            newest = d
    con.close()
    if newest is None:
        print("no in-catalog live review found")
        return
    print("top-level keys:")
    for key in sorted(newest):
        value = newest[key]
        text = json.dumps(value, ensure_ascii=False, default=str)
        print("  %-38s %s" % (key, text[:120]))
    calc = newest.get("bestAmountCalculation") or {}
    print("\nbestAmountCalculation keys:", sorted(calc))
    print("rows[0]:", json.dumps((calc.get("rows") or calc.get("probeRecords") or [{}])[0],
                                 ensure_ascii=False, default=str)[:600])


def cmd_publish():
    """The Rust publish block that the live reviews carry."""
    con = connect()
    seen = collections.Counter()
    sample = None
    for _r, d in live_rows(con):
        block = d.get("reviewPublish") or d.get("review_publish")
        if not isinstance(block, dict):
            continue
        key = "%s|%s|%s|%s|%s" % (block.get("published"), block.get("reason"),
                                   block.get("evidenceCount"), block.get("catalogCount"),
                                   block.get("missingCount"))
        seen[key] += 1
        if sample is None:
            sample = block
    con.close()
    print("publish blocks seen in the live payloads:")
    for k, v in seen.most_common(12):
        print("  %4d -> %s" % (v, k))
    if sample:
        print("sample:", json.dumps(sample, ensure_ascii=False)[:600])
    if not seen:
        print("(no reviewPublish key in the stored review payloads)")


def cmd_workers():
    """The scanner's own worker / capacity bookkeeping (read-only)."""
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot",
                                timeout=180) as handle:
        payload = json.loads(handle.read().decode("utf-8", "replace"))
    workers = (payload.get("scanner") or {}).get("closed_loop_rust_workers") or {}
    rows = workers.get("workers") or workers.get("groups") or []
    print("worker dict keys:", sorted(workers))
    for key in sorted(workers):
        if key in ("workers", "groups"):
            continue
        value = workers[key]
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > 400:
            text = text[:400] + "……"
        print("  %-28s %s" % (key, text))
    states = collections.Counter()
    phases = collections.Counter()
    for row in rows:
        if isinstance(row, dict):
            states[row.get("state")] += 1
            phases[row.get("phase")] += 1
    print("worker states:", dict(states))
    print("worker phases:", dict(phases))
    print("worker rows:", len(rows))


def cmd_groups():
    """Compare the resident worker groups with the published catalog.

    The resident limit is derived from the number of **discovered** groups,
    so a group that is never discovered can never hold a worker, never be
    reviewed, and never reach the published review generation.
    """
    import urllib.request
    sys.path.insert(0, os.path.join(ROOT, "flashloan", "src"))
    from dex.runtime.closed_loop_identity import worker_group_key_from_symbols
    with urllib.request.urlopen("http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot",
                                timeout=180) as handle:
        payload = json.loads(handle.read().decode("utf-8", "replace"))
    workers = (payload.get("scanner") or {}).get("closed_loop_rust_workers") or {}
    rows = workers.get("workers") or workers.get("groups") or []
    resident = set()
    for row in rows:
        if isinstance(row, dict):
            key = str(row.get("group_key") or row.get("groupKey") or "").strip().lower()
            if key:
                resident.add(key)
    con = connect()
    catalog_keys = {}
    for row in con.execute("SELECT loop_key, path_symbols_json FROM manager_v4_closed_loops"
                           " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?", SCOPE):
        try:
            symbols = json.loads(row["path_symbols_json"] or "[]")
        except Exception:
            symbols = []
        key = worker_group_key_from_symbols(symbols)
        if key:
            catalog_keys.setdefault(key, []).append(row["loop_key"])
    con.close()
    missing = sorted(set(catalog_keys) - resident)
    extra = sorted(resident - set(catalog_keys))
    print("catalog group keys:", len(catalog_keys), "| resident worker groups:", len(resident))
    print("catalog groups with NO resident worker:", len(missing))
    for key in missing[:20]:
        print("   ", key, "-> loops:", len(catalog_keys[key]))
    print("resident groups that are not catalog groups:", len(extra))
    for key in extra[:12]:
        print("   ", key)


def cmd_missing():
    """The catalog loops that have no live review with a loop key yet."""
    con = connect()
    cat = catalog(con)
    seen = set()
    pair_of = {}
    for _r, d in live_rows(con):
        lk = (d.get("loopKey") or "").strip().lower()
        if lk:
            seen.add(lk)
            pair_of[lk] = d.get("pair")
    missing = sorted(cat - seen)
    # the token path of every catalog loop, so the missing loops can be
    # matched to the worker group that should serve them
    paths = {}
    for row in con.execute("SELECT loop_key, path_symbols_json, pool_path_key"
                           " FROM manager_v4_closed_loops WHERE network=? AND chain_id=?"
                           " AND protocol=? AND scope_key=?", SCOPE):
        try:
            symbols = json.loads(row["path_symbols_json"] or "[]")
        except Exception:
            symbols = []
        paths[row["loop_key"]] = ("|".join(str(s) for s in symbols), row["pool_path_key"])
    # the pair of every live review, so a group that was reviewed but did not
    # get a loop key can still be recognised
    reviewed_pairs = set()
    for _r, d in live_rows(con):
        if d.get("pair"):
            reviewed_pairs.add(d["pair"])
    con.close()
    print("missing loops:", len(missing))
    # the worker that serves each missing group, and its last state
    import urllib.request
    sys.path.insert(0, os.path.join(ROOT, "flashloan", "src"))
    from dex.runtime.closed_loop_identity import worker_group_key_from_symbols
    with urllib.request.urlopen("http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot",
                                timeout=180) as handle:
        api = json.loads(handle.read().decode("utf-8", "replace"))
    workers = (api.get("scanner") or {}).get("closed_loop_rust_workers") or {}
    rows = workers.get("workers") or workers.get("groups") or []
    by_group = {}
    for row in rows:
        if isinstance(row, dict):
            key = str(row.get("group_key") or row.get("groupKey") or "").strip().lower()
            if key:
                by_group[key] = row
    con = connect()
    group_of = {}
    for row in con.execute("SELECT loop_key, path_symbols_json FROM manager_v4_closed_loops"
                           " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?", SCOPE):
        try:
            symbols = json.loads(row["path_symbols_json"] or "[]")
        except Exception:
            symbols = []
        key = worker_group_key_from_symbols(symbols)
        if key:
            group_of[row["loop_key"]] = key
    con.close()
    summary = collections.Counter()
    for lk in missing:
        key = group_of.get(lk)
        worker = by_group.get(key)
        if worker is None:
            summary["<no worker>"] += 1
            continue
        summary[(worker.get("state"), worker.get("phase"))] += 1
    print("worker state of the missing groups:", dict(summary))
    shown = 0
    for lk in missing:
        key = group_of.get(lk)
        worker = by_group.get(key)
        if worker is None or shown >= 6:
            continue
        shown += 1
        print("   group=%-18s state=%s phase=%s probes=%s last_error=%s" % (
            key, worker.get("state"), worker.get("phase"), worker.get("probe_count"),
            str(worker.get("last_error"))[:120]))
    for lk in missing:
        symbols, pool_path = paths.get(lk, ("", ""))
        arrow = " -> ".join(symbols.split("|")) if symbols else "?"
        print("   %-40s group=%-18s reviewed_as_pair=%s"
              % (arrow, group_of.get(lk), arrow in reviewed_pairs))


def cmd_search():
    """Every live review row that mentions the symbols given on the command line."""
    wanted = [w.upper() for w in sys.argv[2:]]
    con = connect()
    total = 0
    for row in con.execute("SELECT pair, payload_json, created_at FROM cow_execution_reviews"
                           " WHERE created_at >= ? ORDER BY rowid", (FIX_RESTART_UTC,)):
        try:
            d = json.loads(row["payload_json"] or "{}")
        except Exception:
            continue
        path = [str(s).upper() for s in (d.get("route_path") or [])]
        if wanted and not all(w in path for w in wanted):
            continue
        total += 1
        print("%-44s loopKey=%-46s reason=%s" % (
            row["pair"], str(d.get("loopKey"))[:46],
            (d.get("auto_execute") or {}).get("reason")))
    con.close()
    print("matching live rows:", total)


def cmd_worker():
    """The full worker row of one group (compare a missing one with a working one)."""
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:5000/api/dex/opportunity-scanner/snapshot",
                                timeout=180) as handle:
        api = json.loads(handle.read().decode("utf-8", "replace"))
    workers = (api.get("scanner") or {}).get("closed_loop_rust_workers") or {}
    rows = workers.get("workers") or workers.get("groups") or []
    wanted = [w.lower() for w in sys.argv[2:]]
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("group_key") or row.get("groupKey") or "").strip().lower()
        if not any(w in key for w in wanted):
            continue
        print("=== %s ===" % key)
        for field in sorted(row):
            value = json.dumps(row[field], ensure_ascii=False, default=str)
            if len(value) > 300:
                value = value[:300] + "……"
            print("   %-26s %s" % (field, value))


def cmd_anchors():
    """Which anchor token of the catalog is covered by a live review.

    A closed loop is ``token -> mid -> out -> token``, so the anchor is the
    first token.  This prints the coverage per anchor over the **whole**
    history, not only the current run.
    """
    con = connect()
    cat = catalog(con)
    covered = set()
    for row in con.execute("SELECT payload_json FROM cow_execution_reviews"):
        try:
            d = json.loads(row[0] or "{}")
        except Exception:
            continue
        lk = (d.get("loopKey") or "").strip().lower()
        if lk:
            covered.add(lk)
    symbols = {}
    for row in con.execute("SELECT loop_key, path_symbols_json FROM manager_v4_closed_loops"
                           " WHERE network=? AND chain_id=? AND protocol=? AND scope_key=?", SCOPE):
        try:
            path = json.loads(row["path_symbols_json"] or "[]")
        except Exception:
            path = []
        anchor = str(path[0]).upper() if path else "?"
        symbols.setdefault(anchor, []).append(row["loop_key"])
    print("%-10s %6s %6s %6s" % ("anchor", "total", "covrd", "missng"))
    for anchor in sorted(symbols):
        loops = symbols[anchor]
        hit = sum(1 for lk in loops if lk in covered)
        print("%-10s %6d %6d %6d" % (anchor, len(loops), hit, len(loops) - hit))
    print("\nanchor token addresses (from the catalog loop keys):")
    for anchor in sorted(symbols):
        first = symbols[anchor][0].split(":")[0]
        print("   %-10s %s" % (anchor, first))
    con.close()


def cmd_core():
    """The runtime core snapshot state, read from the live API."""
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:5000/api/status", timeout=300) as handle:
        panel_api = json.loads(handle.read().decode("utf-8", "replace"))
    panel = (panel_api.get("chain_runtime") or {}).get("runtimeCore") or {}
    with urllib.request.urlopen("http://127.0.0.1:5000/api/dex/overview", timeout=120) as handle:
        api = json.loads(handle.read().decode("utf-8", "replace"))
    core = api.get("runtimeCore") or {}
    print("overview.runtimeCore:", json.dumps(
        {k: core.get(k) for k in ("state", "snapshotAvailable", "generation", "snapshotChecksum",
                                  "upstreamFingerprint", "instanceId")}, ensure_ascii=False, indent=1))
    print("panel.runtimeCore:", json.dumps(
        {k: panel.get(k) for k in ("state", "snapshotAvailable", "generation", "closed",
                                   "lastError", "registered", "instanceId")}, ensure_ascii=False, indent=1))
    events = panel.get("recentEvents") or []
    committed = [e for e in events if isinstance(e, dict) and e.get("eventType") == "snapshot_committed"]
    rejected = [e for e in events if isinstance(e, dict) and e.get("eventType") == "snapshot_rejected"]
    print("events: committed=%d rejected=%d" % (len(committed), len(rejected)))
    for event in committed[-3:]:
        print("   committed:", json.dumps(event.get("detail"), ensure_ascii=False))
    for event in rejected[-2:]:
        print("   rejected :", json.dumps(event.get("detail"), ensure_ascii=False)[:300])


def cmd_layers():
    """The upstream layers the runtime core snapshot is built from."""
    con = connect()
    for table in ("manager_broadcast_library_state", "manager_review_library_state",
                  "manager_v4_closed_loop_library_state", "manager_v4_candidate_edge_library_state",
                  "manager_v4_candidate_exchange_pool_library_state", "manager_v4_pool_scope_library_state",
                  "manager_v4_directional_pool_library_state"):
        try:
            rows = con.execute("SELECT * FROM %s WHERE network='robinhood' AND chain_id=4663" % table).fetchall()
        except Exception as error:
            print("-- %-52s ERR %s" % (table, error))
            continue
        if not rows:
            print("-- %-52s (no row)" % table)
            continue
        for row in rows:
            keys = [k for k in row.keys() if k in ("scope_key", "review_version", "closed_loop_version",
                                                   "broadcast_version", "candidate_edge_version",
                                                   "observation_version", "directional_pool_version",
                                                   "request_count", "attempt_count", "pending_count",
                                                   "generated_at", "updated_at")]
            print("-- %-52s %s" % (table, json.dumps({k: row[k] for k in keys}, ensure_ascii=False, default=str)))
    con.close()


def cmd_row():
    """One published review row, as the collector sees it."""
    con = connect()
    cols = [c[1] for c in con.execute("PRAGMA table_info(manager_v4_reviews)")]
    print("columns:", cols)
    row = con.execute("SELECT * FROM manager_v4_reviews WHERE network='robinhood' AND chain_id=4663"
                      " ORDER BY reviewed_at DESC LIMIT 1").fetchone()
    if row is None:
        print("no review row")
        return
    for key in row.keys():
        value = row[key]
        if isinstance(value, str) and len(value) > 500:
            value = value[:500] + "……"
        print("  %-34s %s" % (key, value))
    print("\n-- static_preview_json keys --")
    try:
        preview = json.loads(row["static_preview_json"] or "{}")
    except Exception:
        preview = {}
    calc = preview.get("bestAmountCalculation") or {}
    print("   preview keys:", sorted(preview)[:40])
    print("   calc keys   :", sorted(calc))
    for key in ("bestAmount", "bestAmountMeasured", "bestAmountConfirmed", "bestAmountMissing",
                "bestAmountZeroReason", "method", "bestAmountSelectionBasis"):
        print("   %-24s %s" % (key, calc.get(key)))
    rows = calc.get("rows") or calc.get("probeRecords") or []
    print("   ladder rows:", len(rows))
    if rows:
        print("   ladder[0]:", json.dumps(rows[0], ensure_ascii=False, default=str)[:400])
    print("\n-- run_arbitrage_json --")
    try:
        run = json.loads(row["run_arbitrage_json"] or "{}")
    except Exception:
        run = {}
    for key in sorted(run):
        value = json.dumps(run[key], ensure_ascii=False, default=str)
        if len(value) > 400:
            value = value[:400] + "……"
        print("   %-28s %s" % (key, value))
    con.close()


def cmd_shape_hist():
    """What shapes the published review library holds, per generation."""
    con = connect()
    print("rows by (closed_loop_version, review_status, has bestAmountCalculation):")
    counter = collections.Counter()
    for row in con.execute("SELECT closed_loop_version, review_status, static_preview_json,"
                           " run_arbitrage_json, reviewed_at FROM manager_v4_reviews"
                           " WHERE network='robinhood' AND chain_id=4663"):
        preview = row["static_preview_json"] or "{}"
        run = row["run_arbitrage_json"] or "{}"
        counter[(row["closed_loop_version"], row["review_status"],
                  "bestAmountCalculation" in preview,
                  "attempts" in run, "ladder" in run)] += 1
    for key in sorted(counter, key=str):
        print("   ", key, "->", counter[key])
    con.close()


def cmd_incomplete():
    """The data_incomplete reviews: what is missing and why."""
    con = connect()
    rows = con.execute("SELECT loop_key, review_status, review_reason, static_preview_json,"
                       " run_arbitrage_json, signed_pnl_raw, signed_pnl_amount, reviewed_at"
                       " FROM manager_v4_reviews WHERE network='robinhood' AND chain_id=4663"
                       " AND review_status='data_incomplete'").fetchall()
    print("data_incomplete rows:", len(rows))
    reasons = collections.Counter()
    for row in rows:
        reasons[row["review_reason"]] += 1
    for reason, count in reasons.most_common():
        print("   %4d  %s" % (count, reason))
    if rows:
        row = rows[0]
        print("\n-- sample --")
        print("   loop_key :", row["loop_key"])
        print("   reason   :", row["review_reason"])
        print("   preview  :", (row["static_preview_json"] or "")[:400])
        print("   run      :", (row["run_arbitrage_json"] or "")[:700])
        print("   pnl_raw  :", row["signed_pnl_raw"], "| pnl_amount:", row["signed_pnl_amount"])
    print("\n-- diagnostics --")
    for row in con.execute("SELECT diagnostic_code, COUNT(*) n FROM manager_v4_review_diagnostics"
                           " WHERE network='robinhood' AND chain_id=4663"
                           " GROUP BY diagnostic_code ORDER BY n DESC"):
        print("   %-40s %d" % (row["diagnostic_code"], row["n"]))
    con.close()


def cmd_items(path):
    d = json.load(open(path, encoding="utf-8"))
    ck = d.get("checklist")
    ck = ck.get("items", ck) if isinstance(ck, dict) else ck
    for i in ck:
        print("  %-4s %-12s %s" % (i.get("name"), "MET" if i.get("met") else "NOT MET",
                                     (i.get("requirement") or "")[:80]))
    acc = d.get("acceptance", {})
    print("ACCEPT met=%s verdict=%s" % (acc.get("met"), str(acc.get("verdict"))[:160]))
    for name in sys.argv[3:]:
        for i in ck:
            if i.get("name") == name:
                print(json.dumps(i, ensure_ascii=False, indent=1)[:1600])


def cmd_monitor():
    while True:
        con = connect()
        cat = catalog(con)
        seen = set()
        for _r, d in live_rows(con):
            lk = (d.get("loopKey") or "").strip().lower()
            if lk:
                seen.add(lk)
        state = con.execute("SELECT review_version, closed_loop_version, loop_count,"
                            " completed_count, data_incomplete_count, not_executable_count,"
                            " generated_at FROM manager_review_library_state").fetchone()
        con.close()
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "catalog": len(cat),
               "covered": len(seen & cat), "missing": len(cat - seen),
               "publish": dict(zip(["review_version", "closed_loop_version", "loop_count",
                                     "completed_count", "data_incomplete_count",
                                     "not_executable_count", "generated_at"], state))}
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        if rec["publish"].get("review_version", 0) > 24:
            print("PUBLISHED", flush=True)
            return
        time.sleep(30)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "coverage":
        cmd_coverage()
    elif cmd == "quality":
        cmd_quality()
    elif cmd == "recent":
        cmd_recent()
    elif cmd == "shape":
        cmd_shape()
    elif cmd == "publish":
        cmd_publish()
    elif cmd == "workers":
        cmd_workers()
    elif cmd == "groups":
        cmd_groups()
    elif cmd == "missing":
        cmd_missing()
    elif cmd == "search":
        cmd_search()
    elif cmd == "worker":
        cmd_worker()
    elif cmd == "anchors":
        cmd_anchors()
    elif cmd == "core":
        cmd_core()
    elif cmd == "layers":
        cmd_layers()
    elif cmd == "row":
        cmd_row()
    elif cmd == "shape_hist":
        cmd_shape_hist()
    elif cmd == "incomplete":
        cmd_incomplete()
    elif cmd == "identity":
        cmd_identity()
    elif cmd == "items":
        cmd_items(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "monitor":
        cmd_monitor()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

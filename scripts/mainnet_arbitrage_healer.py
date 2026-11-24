#!/usr/bin/env python3
"""Robinhood 4663 主网套利闭环审计、自愈编排和观察工具。

固定顺序：代币 -> 池子 -> 闭环 -> 复核 -> 广播。默认只读；repair 只有
显式 --apply 才允许写入。不伪造 PnL、签名者、nonce、RPC 或交易哈希。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import re
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FLASHLOAN = ROOT / "flashloan"
SRC_ROOT = FLASHLOAN / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from core.config.fee_policy import MAX_CANDIDATE_FIXED_FEE

# The closed-loop identity is published by the closed-loop Manager and
# reaches the runtime through the kernel result. The healer therefore only
# **consumes** it: the closed-loop key is the funding anchor plus the
# directed pool path, and the healer never re-anchors a cycle or assembles
# a loop key out of the route addresses. A review that carries no published
# identity stays unassociated and is reported as such instead of being
# filed under a guess (MIO-7 / TASK-149 / AC-214).
from dex.runtime.closed_loop_identity import resolve_registered_group_key

# The records the Rust managers own are read through the manager API. The
# healer consumes what the managers published and never reads the manager's
# own tables with SQL.
from dex.runtime import manager_record_reader

# DEP-4.2: the healer and the Rust pool manager have to admit a pool on the
# same boundary. The pool manager owns the depth refresh and the capacity
# schema, so the shared shallow-band capacity is the single admission floor
# for both sides. The healer used to carry no floor of its own and classify
# the same pool differently from the manager.
try:
    from dex.intent_trade.chains.robinhood.v4_pool_capacity import (
        DEPTH_BAND_SHALLOW_CAPACITY as MIN_POOL_CAPACITY_RAW,
    )
except Exception:  # pragma: no cover - the constant is optional at import time
    MIN_POOL_CAPACITY_RAW = 0.001

DEFAULT_DB = FLASHLOAN / "db" / "local_store_4663.sqlite3"
DEFAULT_TOOLS = FLASHLOAN / "tools" / "target" / "debug"

# The pool manager owns the depth refresh and the capacity schema. The
# healer drives it as a CLI, so the deployed binary has to be at least as new
# as the source that introduced the commands it calls.
#
# The healer used to call `pool-manager depth` and carry on when the binary
# rejected the command as unknown, so a stale deployment silently skipped the
# whole capacity rollout. The command set the healer depends on is therefore
# probed explicitly before any repair runs.
POOL_MANAGER_REQUIRED_COMMANDS = ("audit", "baseline-audit", "cleanup", "materialize-scope",
                                  "ensure-indexes", "ensure-depth-schema", "depth")
REGISTRIES = {
    4663: FLASHLOAN / "chains" / "robinhood" / "robinhood_stock_tokens.json",
    46630: FLASHLOAN / "chains" / "robinhood-testnet" / "robinhood_stock_tokens.json",
}
# The plan tree was migrated from `logs/plan/` to `plan/` on 2026-11-14
# (see `plan/back/计划目录迁移记录_20261114_v9.11.md`). These constants still
# pointed at the old location, so the healer never found the observation
# epoch and silently fell back to the unix epoch, which widened every
# review window to "everything" and made the counts unreproducible. The
# migration record states that the default paths in code and documents were
# changed to `plan`; these were the ones that were missed.
#
# The legacy location is kept as a fallback so a store that still has its
# epoch in the old tree keeps working, but the current location wins.
LEGACY_PLAN_ROOT = ROOT / "logs" / "plan"
PLAN_ROOT = ROOT / "plan"


def _plan_path(relative: str) -> Path:
    """Return the current plan-tree path, falling back to the legacy tree."""
    current = PLAN_ROOT / relative
    if current.exists():
        return current
    legacy = LEGACY_PLAN_ROOT / relative
    if legacy.exists():
        return legacy
    return current


RUNTIME_EVIDENCE = PLAN_ROOT / "runtime_evidence"
REPORT_DIR = RUNTIME_EVIDENCE / "reports"
WATCH_DIR = RUNTIME_EVIDENCE / "diagnostics" / "mainnet_arbitrage_healer"
SUPERVISOR_DIR = RUNTIME_EVIDENCE / "diagnostics" / "mainnet_self_healing_orchestrator"
EPOCH_FILE = _plan_path("runtime_evidence/observation_epoch/mainnet_current.json")
RUNTIME_ENTRYPOINT = FLASHLOAN / "app_runtime" / "run.py"
NETWORK, CHAIN_ID, SCOPE = "robinhood", 4663, "core4"
KNOWN_SCOPES = {
    4663: ("robinhood", "core4"),
    46630: ("robinhood-testnet", "core4"),
}


def activate_scope(chain_id: int) -> tuple[str, int, str]:
    global NETWORK, CHAIN_ID, SCOPE, EPOCH_FILE
    if int(chain_id) not in KNOWN_SCOPES:
        raise ValueError(f"unsupported chain-id: {chain_id}")
    NETWORK, SCOPE = KNOWN_SCOPES[int(chain_id)]
    CHAIN_ID = int(chain_id)
    epoch_name = "mainnet_current.json" if CHAIN_ID == 4663 else "testnet_current.json"
    EPOCH_FILE = _plan_path(f"runtime_evidence/observation_epoch/{epoch_name}")
    return NETWORK, CHAIN_ID, SCOPE


def database_for_chain(chain_id: int) -> Path:
    if int(chain_id) == 4663:
        return FLASHLOAN / "db" / "local_store_4663.sqlite3"
    if int(chain_id) == 46630:
        return FLASHLOAN / "db" / "local_store_46630.sqlite3"
    raise ValueError(f"unsupported chain-id: {chain_id}")


def registry_for_chain(chain_id: int) -> Path:
    try:
        return REGISTRIES[int(chain_id)]
    except KeyError as error:
        raise ValueError(f"unsupported chain-id: {chain_id}") from error
# The market observation layer is materialized from its own pool scope. It is
# not a copy of the candidate scope, so it legitimately carries a different
# scope checksum and must never be compared against the candidate scope.
OBSERVATION_SCOPE = "candidate_exchange"
CLOSED_LOOP_SCOPE_LAYER = "manager_v4_closed_loop_library_state"
#: The fallback funding addresses of the mainnet. The chain's own token
#: allowlist is the authority, because the funding addresses are chain
#: specific: mainnet's test token `TZ` lives on a different address than
#: testnet's, and the wrapped native token differs as well. The fallback
#: only covers a chain whose allowlist cannot be read.
OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL = {
    "ETH": "0x0000000000000000000000000000000000000000",
    "WETH": "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
    "USDG": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
    "USDE": "0x5d3a1ff2b6bab83b63cd9ad0787074081a52ef34",
    "TZ": "0xa8cf7c51eee671646ba440ffa180f970902fd2fb",
}
#: The funding symbols every chain has to provide. The addresses come from
#: the chain's allowlist; the symbol set itself is chain agnostic.
FUNDING_TOKEN_SYMBOLS = ("ETH", "WETH", "USDG", "USDE", "TZ")
#: The chain data files that carry the authoritative token allowlist.
TOKEN_ALLOWLIST_FILES = {
    4663: FLASHLOAN / "chains" / "robinhood" / "v4_token_allowlist.json",
    46630: FLASHLOAN / "chains" / "robinhood-testnet" / "v4_token_allowlist.json",
}
KNOWN_COUNTERFEIT_FUNDING_TOKEN_ADDRESSES = {
    "0xa8aeed8ac378684eb120674c12907c0e9149210b",
    "0x446faa4d3e8a0e30d3edcbf84170ef6e638c0f78",
    "0x4064b717b4cb9eab3bf9065cedcc403ae5881dda",
    "0x66f0a24854f1e6f587935bce945e6cc71e72460a",
    "0xc4f6c19fe4b1ad825866af04b8019c61c4ca301e",
    "0x03f5c4ff794618a20add6cab0383d559782fe1d8",
    "0x02865c612393ceb2482fdc08fb30fce113305d12",
}

def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_iso_timestamp(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt.datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


#: The window that is substituted when the pinned epoch cannot be read. The
#: window stays **bounded**: it is never widened to the unix epoch, because
#: an unbounded window makes every count unreproducible and silently turns
#: "we do not know" into "everything counts as recent".
OBSERVATION_EPOCH_FALLBACK = "2026-01-01T00:00:00Z"


def observation_epoch() -> dict[str, Any]:
    """Return the pinned observation epoch without silently widening it."""
    try:
        value = json.loads(EPOCH_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {
            "startedAt": OBSERVATION_EPOCH_FALLBACK,
            "epochFallback": True,
            "epochUnknown": True,
            "epochFallbackReason": type(error).__name__,
            "epochFile": str(EPOCH_FILE),
        }
    if not isinstance(value, dict):
        return {"startedAt": OBSERVATION_EPOCH_FALLBACK, "epochFallback": True,
                "epochUnknown": True, "epochFallbackReason": "epoch_not_an_object",
                "epochFile": str(EPOCH_FILE)}
    started_at = str(value.get("startedAt") or "")
    if not started_at:
        # A document without a startedAt is a different failure from a
        # document with a malformed one, and the two must stay
        # distinguishable: the first means the epoch was never pinned, the
        # second means it was pinned and then corrupted.
        return {"startedAt": OBSERVATION_EPOCH_FALLBACK, "epochFallback": True,
                "epochUnknown": True, "epochFallbackReason": "startedAt_missing",
                "epochFile": str(EPOCH_FILE)}
    if not _is_iso_timestamp(started_at):
        return {"startedAt": OBSERVATION_EPOCH_FALLBACK, "epochFallback": True,
                "epochUnknown": True, "epochFallbackReason": "startedAt_not_a_timestamp",
                "epochFile": str(EPOCH_FILE)}
    return {"startedAt": started_at, "epochFallback": False, "epochUnknown": False,
            "epochFallbackReason": None, "epochFile": str(EPOCH_FILE)}
def observation_started_at() -> str:
    return str(observation_epoch()["startedAt"])


#: Every supported chain owns exactly one SQLite database file. The file is
#: the unit of isolation: a chain never reads, writes or reports on another
#: chain's rows, and a row never migrates between files.
CHAIN_DATABASES: dict[int, str] = {
    4663: "local_store_4663.sqlite3",
    46630: "local_store_46630.sqlite3",
}


def assert_chain_database(db: Path, chain_id: int | None = None) -> Path:
    """Assert that ``db`` is the database file of exactly one chain.

    Every chain has its own SQLite file, and that separation is the
    precondition for the whole cascade: the healer decides what to repair by
    reading one file, so a file that belongs to another chain would make the
    verdict describe the wrong chain entirely.

    The check is deliberately strict. A database file that is not the
    registered file of the requested chain is refused, including the
    legacy ``local_store.sqlite3`` and the per-chain-id files that the
    runtime may create for unrelated chains. This is the same class of
    defect that stranded closed loops in ``preflight_running`` in round 21,
    where the claim and the completion landed in two different files.
    """
    target = int(chain_id if chain_id is not None else CHAIN_ID)
    expected_name = CHAIN_DATABASES.get(target)
    if expected_name is None:
        raise ValueError(f"unsupported chain-id: {target}")
    expected = (FLASHLOAN / "db" / expected_name).resolve()
    resolved = Path(db).resolve()
    if resolved != expected:
        raise ValueError(
            "chain database mismatch: "
            f"chain {target} owns {expected.name}, but {resolved.name} was requested; "
            "each chain has exactly one SQLite database file and a chain never "
            "reads or writes another chain's file"
        )
    return resolved


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def one(db: Path, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    con = connect(db)
    try:
        row = con.execute(sql, params).fetchone()
    finally:
        con.close()
    return dict(row) if row else None


def all_rows(db: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    con = connect(db)
    try:
        return [dict(row) for row in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def count(db: Path, table: str) -> int:
    return int((one(db, f"SELECT COUNT(*) AS n FROM {table}") or {"n": 0})["n"])


# ---------------------------------------------------------------------------
# The records the Rust managers own
# ---------------------------------------------------------------------------
#
# The **Review Manager** owns the review library, the **Broadcast Manager**
# owns the broadcast attempts, and the **closed-loop Manager** owns the
# closed-loop catalog. The healer reads those records through the manager
# API, never with SQL against the manager's own tables.
#
# The reason is not a preference. A direct read binds the healer to the
# storage layout of a table it does not own, and it lets the healer observe
# a half-written state that the manager never publishes. The API answers
# with a committed generation, so every read below is a read of a state the
# manager actually published, and the row shapes the audits work with are
# produced by the adapter functions that follow.
#
# The tables that the healer still reads with SQL are the ones **no Rust
# manager owns**: the dynamic legacy review library the runtime writes
# (``cow_execution_reviews``), the token registries and the pool scope
# configuration. Those stay in the store until a manager takes them over.

#: The manager API the healer reads the manager-owned records from.
MANAGER_API_DEFAULT = "http://127.0.0.1:8091"


def manager_read_reviews(manager_api: str | None) -> list[dict[str, Any]]:
    """Return the review rows the Review Manager published.

    The rows are the shape the healer reasons about. The Manager API
    publishes the nested blocks as structured values and the columns in
    camel case, so the adapter re-names them into the column-shaped names
    the audits use.

    The re-naming is a pure change of **name**, never of **value**: a
    field the manager did not publish stays absent, and a published
    ``null`` stays a missing value rather than becoming a default.
    """
    if not manager_api:
        return []
    try:
        published = manager_record_reader.read_reviews(manager_api, CHAIN_ID)
    except manager_record_reader.ManagerReadError:
        return []
    rows: list[dict[str, Any]] = []
    for item in published:
        row = dict(item)
        # The published identity and the published upstream versions.
        if row.get("loopKey") is not None:
            row["loop_key"] = row.get("loopKey")
        if row.get("network") is not None:
            row["network"] = row.get("network")
        if row.get("chainId") is not None:
            row["chain_id"] = row.get("chainId")
        if row.get("protocol") is not None:
            row["protocol"] = row.get("protocol")
        if row.get("scopeKey") is not None:
            row["scope_key"] = row.get("scopeKey")
        if row.get("closedLoopVersion") is not None:
            row["closed_loop_version"] = row.get("closedLoopVersion")
        if row.get("closedLoopChecksum") is not None:
            row["closed_loop_checksum"] = row.get("closedLoopChecksum")
        if row.get("candidateEdgeVersion") is not None:
            row["candidate_edge_version"] = row.get("candidateEdgeVersion")
        if row.get("candidateEdgeChecksum") is not None:
            row["candidate_edge_checksum"] = row.get("candidateEdgeChecksum")
        if row.get("observationVersion") is not None:
            row["observation_version"] = row.get("observationVersion")
        if row.get("observationChecksum") is not None:
            row["observation_checksum"] = row.get("observationChecksum")
        if row.get("poolScopeChecksum") is not None:
            row["pool_scope_checksum"] = row.get("poolScopeChecksum")
        # The published route identity.
        if row.get("anchorTokenKeyId") is not None:
            row["anchor_token_key_id"] = row.get("anchorTokenKeyId")
        if row.get("anchorAddress") is not None:
            row["anchor_address"] = row.get("anchorAddress")
        if row.get("anchorSymbol") is not None:
            row["anchor_symbol"] = row.get("anchorSymbol")
        if row.get("pathText") is not None:
            row["path_text"] = row.get("pathText")
        if row.get("poolPathKey") is not None:
            row["pool_path_key"] = row.get("poolPathKey")
        if row.get("pathAddresses") is not None:
            row["path_addresses_json"] = json.dumps(row.get("pathAddresses"))
        if row.get("pathSymbols") is not None:
            row["path_symbols_json"] = json.dumps(row.get("pathSymbols"))
        if row.get("poolIds") is not None:
            row["pool_ids_json"] = json.dumps(row.get("poolIds"))
        # The published verdict and its reason.
        if row.get("reviewStatus") is not None:
            row["review_status"] = row.get("reviewStatus")
        if row.get("reviewReason") is not None:
            row["review_reason"] = row.get("reviewReason")
        # The published closed-loop identity.  The Review Manager owns the
        # identity of a review and publishes it, so the healer only reads
        # it.  It never rebuilds the identity from the route addresses.
        identity = row.get("closedLoopIdentity")
        if isinstance(identity, dict) and identity:
            row["closed_loop_identity"] = dict(identity)
            if identity.get("closedLoopIdentityStatus") is not None:
                row["closed_loop_identity_status"] = identity.get("closedLoopIdentityStatus")
            if identity.get("closedLoopPoolIdentityStatus") is not None:
                row["closed_loop_pool_identity_status"] = identity.get(
                    "closedLoopPoolIdentityStatus"
                )
            if identity.get("identityMismatch") is not None:
                row["identity_mismatch"] = identity.get("identityMismatch")
            if identity.get("closedLoopRepairTarget") is not None:
                row["closed_loop_repair_target"] = identity.get("closedLoopRepairTarget")
        # The published nested blocks.
        if row.get("staticPreview") is not None:
            row["static_preview_json"] = json.dumps(row.get("staticPreview"))
        if row.get("runArbitrage") is not None:
            row["run_arbitrage_json"] = json.dumps(row.get("runArbitrage"))
        # The published timestamps.
        if row.get("reviewedAt") is not None:
            row["reviewed_at"] = row.get("reviewedAt")
        if row.get("createdAt") is not None:
            row["created_at"] = row.get("createdAt")
        if row.get("updatedAt") is not None:
            row["updated_at"] = row.get("updatedAt")
        signed_pnl = item.get("signedPnl")
        if not isinstance(signed_pnl, dict):
            signed_pnl = {}
        row["signed_pnl_raw"] = signed_pnl.get("raw")
        row["signed_pnl_amount"] = signed_pnl.get("amount")
        row["signed_pnl_currency"] = signed_pnl.get("currency")
        row["signed_pnl_address"] = signed_pnl.get("address")
        row["signed_pnl_unit"] = signed_pnl.get("unit")
        row["signed_pnl_source"] = signed_pnl.get("source")
        rows.append(row)
    return rows


def manager_read_closed_loops(manager_api: str | None) -> dict[str, Any]:
    """Return the closed-loop records the closed-loop Manager published."""
    if not manager_api:
        return {"status": {}, "loops": [], "hops": [], "optimizations": [], "runtimeStates": []}
    try:
        return manager_record_reader.read_closed_loops(manager_api, CHAIN_ID)
    except manager_record_reader.ManagerReadError:
        return {"status": {}, "loops": [], "hops": [], "optimizations": [], "runtimeStates": []}


def manager_read_broadcasts(manager_api: str | None) -> dict[str, Any]:
    """Return the broadcast records the Broadcast Manager published."""
    if not manager_api:
        return {"status": {}, "attempts": [], "diagnostics": []}
    try:
        return manager_record_reader.read_broadcasts(manager_api, CHAIN_ID)
    except manager_record_reader.ManagerReadError:
        return {"status": {}, "attempts": [], "diagnostics": []}


def manager_loop_rows(manager_api: str | None) -> list[dict[str, Any]]:
    """Return the closed-loop catalog the closed-loop Manager published.

    The rows are the shape the healer works with: the published loop key,
    the path, the directed pool set and the status. Nothing is derived
    here, and a field the manager did not publish stays absent.
    """
    rows: list[dict[str, Any]] = []
    for item in manager_read_closed_loops(manager_api).get("loops") or []:
        row = dict(item)
        row["loop_key"] = str(item.get("loopKey") or "")
        row["path_text"] = item.get("pathText")
        row["path_symbols_json"] = json.dumps(item.get("pathSymbols") or [])
        row["path_addresses_json"] = json.dumps(item.get("pathAddresses") or [])
        row["pool_ids_json"] = json.dumps(item.get("poolIds") or [])
        row["anchor_symbol"] = item.get("anchorSymbol")
        row["status"] = item.get("status")
        rows.append(row)
    return rows


def table_names(db: Path) -> set[str]:
    """Return every user table in the store.

    The healer reads several independently materialized tables. A table that
    is absent from the store must be reported as absent rather than raising,
    so the audit can describe a chain that owns only some of the tables.
    """
    try:
        con = connect(db)
    except sqlite3.Error:
        return set()
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {str(row["name"]) for row in rows}


def table_columns(db: Path, table: str) -> set[str]:
    try:
        con = connect(db)
    except sqlite3.Error:
        return set()
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {str(row["name"]) for row in rows}


#: The depth-evidence columns that ``pool-manager ensure-depth-schema`` adds
#: to the liquidity snapshot table.  The list mirrors the Rust constant
#: ``DEPTH_CAPACITY_COLUMNS``; a store that predates the columns keeps its
#: old shape, because SQLite never alters an existing table.
DEPTH_CAPACITY_COLUMNS: tuple[str, ...] = (
    "capacity_token0_raw",
    "capacity_token1_raw",
    "capacity_raw",
    "capacity_binding_raw",
    "price_impact_bps",
    "price_impact_limit_bps",
    "depth_basis",
    "tick",
    "sqrt_price_x96",
    "single_tick_max_token0_raw",
    "single_tick_max_token1_raw",
)

#: The table that carries the on-chain depth observation.
DEPTH_SNAPSHOT_TABLE = "v4_exchange_pool_liquidity_snapshots"


def missing_depth_capacity_columns(db: Path) -> list[str]:
    """Return the depth-evidence columns the store does not carry yet.

    The check is read-only: the healer never changes a store outside an
    explicit ``--apply`` round, so a dry run asks this question instead of
    running the writing schema command.
    """
    present = table_columns(db, DEPTH_SNAPSHOT_TABLE)
    if not present:
        return []
    return [column for column in DEPTH_CAPACITY_COLUMNS if column not in present]


def as_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def amount(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def addresses(payload: dict[str, Any]) -> list[str]:
    values = payload.get("route_addresses") or payload.get("routeAddresses") or []
    return [str(value).lower() for value in values if value] if isinstance(values, list) else []


def row_route_addresses(row: dict[str, Any]) -> list[str]:
    """Return the route addresses of a review row, whatever its source.

    The legacy table stores the route inside the JSON payload; the Manager
    library stores it in a dedicated column. Both shapes have to resolve to
    the same loop identity, otherwise a route would look like two different
    routes depending on which table it came from.
    """
    payload = as_json(row.get("payload_json"))
    route = addresses(payload)
    if route:
        return route
    manager_route = row.get("path_addresses_json")
    if manager_route:
        try:
            values = json.loads(manager_route)
        except (TypeError, ValueError, json.JSONDecodeError):
            values = []
        if isinstance(values, list):
            return [str(value).lower() for value in values if value]
    return []


def review_loop_key(row: dict[str, Any]) -> str | None:
    """Return the closed-loop identity a review row was filed under.

    The identity is **published**, never derived. Two tables carry it:

    * the Manager review table owns it in its ``loop_key`` column;
    * the legacy review table owns it in the review payload, under
      ``loopKey`` -- the field the bridge layer writes from the kernel's
      ``closedLoopKey``.

    A row that carries neither has **no** identity: it is not associated
    with any closed loop, and the caller reports it as unassociated rather
    than assembling a key out of the route addresses. Assembling one would
    file the review under a loop the Manager never published, and that
    loop's capacity and pool set with it (MIO-7 / AC-214).
    """
    loop_key = str(row.get("loop_key") or "").strip()
    if loop_key:
        return loop_key
    for payload in review_payloads(row):
        value = str(payload.get("loopKey") or "").strip()
        if value:
            return value
    return None


def review_pool_ids(row: dict[str, Any]) -> list[str]:
    """Return the pools the review actually exercised.

    The runtime writes the reviewed pool into the review record it owns, so
    the pool sequence is the **evidence** of which physical route the review
    describes. It is a stronger identity than the address path, because the
    address path only says which tokens the route visited while the pool
    sequence says which pools it used.
    """
    payload = as_json(row.get("payload_json"))
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("poolId", "pool_id") and isinstance(value, str):
                    normalized = value.strip().lower()
                    if normalized and normalized not in found:
                        found.append(normalized)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return found


def review_loop_key_by_pool(db: Path, row: dict[str, Any],
                            known_loops: dict[str, list[str]]) -> str | None:
    """Resolve a review row to its closed loop by the pools it exercised.

    A review belongs to the loop whose **full** ordered hop-pool sequence
    it reproduces. The match used to be a plain set intersection, which
    accepted a review of one loop that happened to touch a single pool of
    another loop and then filed it under that other loop's identity,
    capacity and pool set.

    The match is now exact and ordered: every hop pool of the review has to
    equal the hop pool of the loop at the same position. A review that
    shares no full sequence with any known loop belongs to no known loop,
    and the caller treats it as unmatched rather than guess.
    """
    pools = review_pool_ids(row)
    if not pools:
        return None
    for loop_key, hop_pools in known_loops.items():
        if not hop_pools:
            continue
        if len(hop_pools) != len(pools):
            continue
        if list(hop_pools) == list(pools):
            return loop_key
    return None


def closed_loop_hop_pools(
    db: Path,
    manager_api: str | None = None,
) -> dict[str, list[str]]:
    """Return the pool of every hop of every known closed loop.

    The **closed-loop Manager** owns the closed-loop structure, so its hop
    rows are the ones that describe the loops the reviews are filed
    against. They are read through the manager API, which is the manager's
    own read surface; the healer never selects from the manager's tables.

    The runtime's own hop table only carries the loops the runtime
    currently keeps resident, which is a subset: a review of a loop that
    the runtime no longer holds resident would otherwise be filed against
    nothing.
    """
    hops: dict[str, list[str]] = {}
    published = manager_read_closed_loops(manager_api)
    for row in published.get("hops") or []:
        loop_key = str(row.get("loopKey") or row.get("loop_key") or "").strip()
        pool_id = str(row.get("poolId") or row.get("pool_id") or "").strip().lower()
        if not loop_key or not pool_id:
            continue
        hops.setdefault(loop_key, []).append(pool_id)
    if hops:
        return hops
    # The runtime's own hop table is a **runtime** table that no Rust
    # manager owns, so it is still read from the store. It only carries the
    # loops the runtime keeps resident, which is a subset of the manager's
    # catalog.
    if "v4_closed_loop_hops" in table_names(db):
        for row in all_rows(
            db,
            "SELECT loop_key, pool_id FROM v4_closed_loop_hops WHERE network=? AND chain_id=?",
            (NETWORK, CHAIN_ID),
        ):
            pool_id = str(row.get("pool_id") or "").strip().lower()
            if not pool_id:
                continue
            hops.setdefault(str(row.get("loop_key") or ""), []).append(pool_id)
    return hops


def pnl(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return the explicit profit value of a review row, or ``None``.

    A review that carries no value is **not** a zero-profit review. The
    caller has to be able to tell the two apart, so a missing value stays
    ``None`` and the reason it is missing is reported separately.
    """
    payload = as_json(row.get("payload_json"))
    value = amount(row.get("selected_profit_amount"))
    if value is None:
        value = amount(row.get("signed_pnl_amount"))
    if value is None:
        value = amount(payload.get("selectedProfitAmount"))
    if value is None:
        run_arbitrage = as_json(row.get("run_arbitrage_json"))
        value = amount(run_arbitrage.get("signedPnl", {}).get("amount")
                       if isinstance(run_arbitrage.get("signedPnl"), dict) else None)
    if value is None:
        return None
    raw = (row.get("selected_profit_raw") or row.get("signed_pnl_raw")
           or payload.get("selectedProfitRaw") or str(value))
    symbol = (row.get("selected_profit_amount_symbol") or row.get("signed_pnl_currency")
              or payload.get("selectedProfitAmountSymbol") or "UNKNOWN")
    source = (row.get("selected_profit_source") or row.get("signed_pnl_source")
              or payload.get("selectedProfitSource") or "legacy_review")
    route = row_route_addresses(row)
    return {"raw": str(raw), "amount": value, "currency": str(symbol),
            "address": route[0] if route else None,
            "unit": "raw" if str(raw).startswith(("0x", "-0x")) else "decimal",
            "source": str(source)}


def review_missing_reason(row: dict[str, Any]) -> str:
    """Return why a review carries no explicit profit value."""
    reason = str(row.get("selected_profit_missing_reason") or "").strip()
    if reason:
        return reason
    status = str(row.get("review_status") or "").strip()
    manager_reason = str(row.get("review_reason") or "").strip()
    if manager_reason:
        return f"{status}:{manager_reason}" if status else manager_reason
    return status


def missing_pnl_classification(message: str) -> tuple[str, bool]:
    text = message.lower()
    if "rpc" in text or "eth_call" in text or "network" in text:
        return "rpc_unavailable", True
    if "pool_key_currencies_unavailable" in text or "pool key" in text:
        return "pool_key_unavailable", False
    if "swapnotresolved" in text:
        return "swap_not_resolved", False
    if "revert" in text or "quote" in text or "noprofitableroute" in text:
        return "quote_failed", False
    return ("evidence_missing", True) if not message else ("other", False)


BEST_AMOUNT_MISSING_CODE = "best_amount_missing"
POOL_DEPTH_INSUFFICIENT_CODE = "pool_depth_insufficient"
BEST_AMOUNT_TOO_SMALL_CODE = "best_amount_too_small"
BEST_AMOUNT_LADDER_INCOMPLETE_CODE = "best_amount_ladder_incomplete"
# The amount ladder ran, priced every size it tried and found none of them
# profitable. The best trade amount is a concrete, confirmed **zero**: the
# route pays at no size the ladder tried. This is a market conclusion, not a
# measurement gap, and it must never be dispatched to the review manager as a
# missing amount to repair.
BEST_AMOUNT_NO_PROFITABLE_SIZE_CODE = "no_profitable_size_measured"

# Minimum number of probe records a completed best-trade-amount ladder must
# produce. A single record means the amount ladder never expanded: the reported
# ``bestAmount`` is only the lower bound of the initial probe, so it must never
# be treated as a confirmed trade size.
BEST_AMOUNT_LADDER_MIN_PROBE_ROWS = 2

# Upper bound for a trustworthy candidate-pool TVL reading. Liquidity
# snapshots observed values up to 1e43 which are stale or malformed; those
# readings must never qualify a candidate pool.
MAX_POOL_TVL_USD = 1_000_000_000.0

# Default cap for review-row scans. The production table currently holds ~20k
# rows and the 24h window still covers thousands of them, so an unbounded scan
# forces a full table read plus a temporary sort.
REVIEW_QUERY_LIMIT = 20000


def review_payloads(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every JSON payload that can carry best-trade-amount evidence.

    A review row reaches the healer from two different libraries and each
    library stores the evidence under a different column:

    * the legacy ``cow_execution_reviews`` table keeps the whole review
      record in ``payload_json``;
    * the Manager-authoritative ``manager_v4_reviews`` table has no
      ``payload_json`` column at all. It keeps the static preview and the
      live on-chain run in ``static_preview_json`` and
      ``run_arbitrage_json``.

    Reading only ``payload_json`` therefore made every Manager row look like
    a review with a missing best trade amount, even when the Manager had
    materialised a concrete amount. The healer must inspect all carriers and
    use the first one that actually carries the evidence.
    """
    payloads: list[dict[str, Any]] = []
    for column in ("payload_json", "static_preview_json", "run_arbitrage_json"):
        payload = as_json(row.get(column))
        if payload:
            payloads.append(payload)
    return payloads


def _first_present(payloads: list[dict[str, Any]], *keys: str) -> Any:
    """Return the first non-empty value found under ``keys`` across payloads."""
    for payload in payloads:
        for key in keys:
            value = payload.get(key)
            if value:
                return value
    return None


def _best_amount_calculation(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the best-trade-amount calculation block from any carrier."""
    for payload in payloads:
        for key in ("bestAmountCalculation", "best_amount_calculation"):
            calculation = payload.get(key)
            if isinstance(calculation, dict) and calculation:
                return calculation
        precheck = payload.get("precheck")
        if isinstance(precheck, dict):
            calculation = precheck.get("best_amount_calculation") or \
                precheck.get("bestAmountCalculation")
            if isinstance(calculation, dict) and calculation:
                return calculation
        quote = payload.get("quote")
        if isinstance(quote, dict):
            for key in ("bestAmountCalculation", "best_amount_calculation"):
                calculation = quote.get(key)
                if isinstance(calculation, dict) and calculation:
                    return calculation
    return {}


def _live_onchain_evidence(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the live on-chain evidence block from any carrier."""
    for payload in payloads:
        quote = payload.get("quote")
        if isinstance(quote, dict):
            evidence = quote.get("live_onchain_evidence")
            if isinstance(evidence, dict) and evidence:
                return evidence
        evidence = payload.get("live_onchain_evidence")
        if isinstance(evidence, dict) and evidence:
            return evidence
        analysis = payload.get("quote_analysis") or payload.get("analysis")
        if isinstance(analysis, dict):
            evidence = analysis.get("live_onchain_evidence")
            if isinstance(evidence, dict) and evidence:
                return evidence
    return {}


def _probe_rows(evidence: dict[str, Any]) -> list[Any]:
    """Return the on-chain execution-size probe ladder."""
    rows = evidence.get("probeRecords")
    if not isinstance(rows, list):
        rows = evidence.get("probe_records")
    if not isinstance(rows, list):
        probe = evidence.get("executionSizeProbe")
        if isinstance(probe, dict):
            rows = probe.get("probeRecords")
            if not isinstance(rows, list):
                rows = probe.get("attempts")
    if not isinstance(rows, list):
        rows = []
    return rows


def _calculation_probe_rows(calculation: dict[str, Any]) -> list[Any]:
    """Return the probe ladder the kernel published with the best amount.

    The kernel publishes the measured sizes of the amount ladder next to the
    best trade amount under ``probeRecords``.  The ladder is what proves
    that the measurement is a real expansion of the amount space, so it has
    to be read from the calculation block as well as from the live on-chain
    evidence envelope.
    """
    for key in ("probeRecords", "probe_records"):
        rows = calculation.get(key)
        if isinstance(rows, list) and rows:
            return rows
    return []


def best_amount_state(row: dict[str, Any]) -> dict[str, Any]:
    """Return the concrete best-trade-amount evidence for one review.

    A review is only complete when the on-chain review resolved a concrete best
    trade amount. ``bestAmount=None`` means the ladder never produced a
    selection, which is an upstream review failure rather than a zero-profit
    conclusion, so it must never be normalised to zero.
    """
    payloads = review_payloads(row)
    calculation = _best_amount_calculation(payloads)
    best_amount = calculation.get("bestAmount")
    if best_amount is None:
        best_amount = calculation.get("best_amount")
    confirmed = bool(calculation.get("bestAmountConfirmed")
                     or calculation.get("best_amount_confirmed"))
    missing = bool(calculation.get("bestAmountMissing")
                   or calculation.get("best_amount_missing"))
    # The Rust kernel owns the amount ladder, so it also owns the distinction
    # between "the ladder never ran" (a measurement gap) and "the ladder ran
    # and no size was profitable" (a measured zero). When the kernel published
    # that measurement, the best amount is a concrete zero and the review is
    # complete.
    measured = bool(
        calculation.get("bestAmountMeasured")
        or calculation.get("best_amount_measured")
    )
    zero_reason = str(
        calculation.get("bestAmountZeroReason")
        or calculation.get("best_amount_zero_reason")
        or ""
    ).strip()
    selection_basis = str(
        calculation.get("bestAmountSelectionBasis")
        or calculation.get("best_amount_selection_basis")
        or ""
    ).strip()
    if measured and best_amount is None:
        # The kernel measured the ladder and no size paid. Normalise the
        # measured zero into an explicit, confirmed value.
        best_amount = "0x0"
        confirmed = True
        missing = False
    if best_amount is None:
        confirmed = False
        missing = True
    evidence = _live_onchain_evidence(payloads)
    raw = evidence.get("bestAmountRaw") or evidence.get("selectedAmountRaw")
    probe_rows = _probe_rows(evidence)
    if not probe_rows:
        # The kernel publishes the measured ladder next to the best amount,
        # so the calculation block is the authoritative carrier of it.
        probe_rows = _calculation_probe_rows(calculation)
    probe_count = len(probe_rows)
    # The best-trade-amount ladder must actually expand. A single probe row
    # means the reported amount is only the initial-probe lower bound, so it
    # cannot be treated as a confirmed trade size.
    ladder_incomplete = probe_count < BEST_AMOUNT_LADDER_MIN_PROBE_ROWS
    confirmation_reason = str((evidence.get("costModel") or {}).get(
        "bestAmountConfirmationReason") or "")[:200]
    review_failure_kind = str(evidence.get("reviewFailureKind") or "")[:80]
    review_failure_detail = str(evidence.get("reviewFailureDetail") or "")[:300]
    status = str(_first_present(payloads, "status", "review_status")
                 or evidence.get("status") or "")[:80]
    if ladder_incomplete and confirmed and not measured:
        confirmed = False
        missing = True
        review_failure_kind = review_failure_kind or BEST_AMOUNT_LADDER_INCOMPLETE_CODE
        review_failure_detail = review_failure_detail or (
            "best trade amount ladder never expanded: "
            f"{probe_count} probe record(s) observed, "
            f"at least {BEST_AMOUNT_LADDER_MIN_PROBE_ROWS} required"
        )
    elif ladder_incomplete and measured and not missing:
        # The kernel owns the ladder, so it is also the authority on whether
        # the measurement is complete.  When the kernel published
        # ``bestAmountMeasured`` it measured the amount space and published
        # the result; a ladder of one size is a legitimate measurement when
        # that single size was the whole space the kernel explored (the
        # single-tick capacity limits it), and the amount is a measured zero.
        #
        # The reviewer used to downgrade such a row to "missing" because the
        # local probe-row counter never saw the kernel's ladder.  That turned
        # a measured market conclusion into a measurement gap and sent the
        # orchestrator after a review tool to repair a measurement that was
        # never broken.
        ladder_incomplete = False
    if measured and not missing and not any(
        bool(row.get("profitable")) or bool(row.get("ok"))
        for row in probe_rows
        if isinstance(row, dict)
    ):
        review_failure_kind = review_failure_kind or BEST_AMOUNT_NO_PROFITABLE_SIZE_CODE
        review_failure_detail = review_failure_detail or (
            zero_reason
            or "the amount ladder measured every size it tried and none of them was profitable"
        )
    return {
        "bestAmount": best_amount,
        "bestAmountRaw": raw,
        "confirmed": confirmed,
        "missing": missing,
        "measured": measured,
        "ladderIncomplete": ladder_incomplete,
        "probeCount": probe_count,
        "selectionBasis": selection_basis,
        "zeroReason": zero_reason,
        "initialProbeAmount": (calculation.get("initialProbeAmount")
                               or calculation.get("initial_probe_amount")),
        "requestedSymbol": (calculation.get("requestedSymbol")
                            or calculation.get("requested_symbol")),
        "method": calculation.get("method"),
        "status": status,
        "reviewFailureKind": review_failure_kind,
        "reviewFailureDetail": review_failure_detail,
        "confirmationReason": confirmation_reason,
    }


def audit_database_baseline(db: Path, tools: Path) -> dict[str, Any]:
    """Run the Rust-owned startup baseline audit without mutating the store."""
    command = [str(tools / "pool-manager.exe"), "baseline-audit", "--store", str(db),
               "--chain-id", str(CHAIN_ID)]
    result = run_tool(command)
    if not result.get("ok"):
        return {"valid": False, "issues": ["baseline_audit_tool_failed"],
                "command": command, "toolResult": result}
    try:
        payload = json.loads(result.get("stdout") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"valid": False, "issues": ["baseline_audit_output_invalid"],
                "command": command, "toolResult": result}
    if not isinstance(payload, dict):
        return {"valid": False, "issues": ["baseline_audit_output_invalid"],
                "command": command, "toolResult": result}
    payload["command"] = command
    # The exact set of migrations a production apply would run belongs in
    # the healer report, not only in the tool output: the operator scopes
    # the change window from the healer report, and "the latest expected
    # migration is missing" does not say how many migrations that means.
    migrations = payload.get("migrations")
    if isinstance(migrations, dict):
        missing = [str(item) for item in (migrations.get("missingMigrations") or [])]
        payload["missingMigrationCount"] = len(missing)
        payload["missingMigrations"] = missing
    return payload


def best_amount_failure_reason(state: dict[str, Any]) -> str:
    """Explain why the best trade amount could not be resolved."""
    detail = state.get("reviewFailureDetail") or ""
    kind = state.get("reviewFailureKind") or ""
    status = state.get("status") or ""
    if detail:
        return f"{kind or 'best_amount_unavailable'}: {detail}"[:300]
    if kind:
        return f"{kind}: best amount unresolved"[:300]
    if status:
        return f"{status}: best amount unresolved"[:300]
    return "best amount unresolved"


def diag_type(message: str) -> str:
    code = missing_pnl_classification(message)[0]
    return "quote_failed" if code == "pool_key_unavailable" else code


def review_row_execution_verdict(row: dict[str, Any]) -> bool:
    """The execution verdict the reviewed row itself recorded.

    The verdict belongs to the layer that executed the route, never to the
    caller that converts the record:

    * a **Review Manager** row carries the kernel's own
      ``runArbitrage.executable`` flag, and
    * a **legacy** row carries the outcome of the attempt under its
      ``category`` column, where ``executable`` marks the rows the chain
      accepted.

    The verdict is therefore **copied** from the record and never declared
    here.  A caller that writes its own verdict decides the two execution
    gates of the Review Manager by itself, and the gates then never run -
    which is exactly how the review library came to hold 164 rows that no
    execution ever saw.
    """
    run = row.get("runArbitrage") or row.get("run_arbitrage")
    if isinstance(run, dict) and isinstance(run.get("executable"), bool):
        return bool(run["executable"])
    return str(row.get("category") or "").strip().lower() == "executable"


def review_evidence(row: dict[str, Any], resolved_loop_key: str | None = None) -> dict[str, Any]:
    payload = as_json(row.get("payload_json"))
    route = row_route_addresses(row)
    loop_key = resolved_loop_key or review_loop_key(row) or f"missing-route:{row.get('id')}"
    signed = pnl(row)
    # CORE-R34-06: the Rust kernel owns the amount ladder, so it also owns the
    # best-trade-amount evidence. The materialization used to publish only
    # the signed profit and the diagnostics, so the Review Manager received a
    # review without a best trade amount and reported every route as
    # ``best_amount_missing`` even when the kernel had measured the ladder
    # and published a concrete amount.
    #
    # The measured amount travels with the evidence, so the Review Manager
    # sees the same conclusion the kernel reached: a measured zero (the
    # ladder ran and no size paid) and a confirmed amount are two different
    # facts, and neither of them is a missing measurement.
    amount_state = best_amount_state(row)
    # The legacy library records the reason in
    # ``selected_profit_missing_reason``; the Manager library records it in
    # ``review_status``/``review_reason``. Reading only the legacy column
    # made every Manager review look like it had no reason at all, so the
    # diagnostic that names the actual upstream failure was lost.
    missing = review_missing_reason(row)
    blocked = payload.get("blocked_reasons") or []
    message = " | ".join([missing] + [str(item) for item in blocked]) if isinstance(blocked, list) else missing
    diagnostics = [] if not message else [{"diagnosticType": diag_type(message),
        "diagnosticCode": diag_type(message), "message": message[:1000],
        "observedAt": row.get("created_at") or now()}]
    category = str(row.get("category") or "")
    static_preview = {"legacyReviewId": row.get("id"), "attemptKey": row.get("attempt_key"),
                      "category": category, "pair": row.get("pair"), "routeAddresses": route,
                      "historicalEvidence": True, "automaticBroadcastEligible": False}
    # The moment the route was measured travels with the review. The Manager
    # row's own timestamp is the moment the review was materialized, so
    # without this stamp a materialized review would always look newer than
    # the measurement it carries and would freeze that measurement forever.
    #
    # The stamp is the **measurement** time, never the materialization time.
    # A Manager row that already carries a stamp keeps the stamp of the
    # measurement it was materialized from, so the chain of measurements
    # never drifts forward on its own.
    measured_at = review_row_measurement_time(row).strip()
    if measured_at:
        static_preview["measuredAt"] = measured_at
    if amount_state.get("bestAmount") is not None:
        static_preview["bestAmountCalculation"] = {
            "bestAmount": amount_state.get("bestAmount"),
            "bestAmountRaw": amount_state.get("bestAmountRaw"),
            "bestAmountConfirmed": bool(amount_state.get("confirmed")),
            "bestAmountMeasured": bool(amount_state.get("measured")),
            "bestAmountMissing": bool(amount_state.get("missing")),
            "bestAmountSelectionBasis": amount_state.get("selectionBasis"),
            "bestAmountZeroReason": amount_state.get("zeroReason"),
            "initialProbeAmount": amount_state.get("initialProbeAmount"),
            "method": amount_state.get("method"),
        }
        if amount_state.get("probeCount"):
            static_preview["bestAmountCalculation"]["probeCount"] = amount_state.get("probeCount")
    return {"loopKey": loop_key,
        "staticPreview": static_preview,
        "runArbitrage": {"ok": signed is not None,
                         "executable": review_row_execution_verdict(row),
                         "blockNumber": None, "signedPnl": signed, "diagnostics": diagnostics},
        "diagnostics": [], "reviewedAt": row.get("created_at") or now()}


def manager_review_rows(
    db: Path,
    limit: int | None = None,
    manager_api: str | None = None,
) -> list[dict[str, Any]]:
    """Read the Manager-authoritative review library.

    The **Review Manager** owns the review library. It is the only review
    source that carries the upstream versions and checksums of the closed
    loop, the candidate edge, the observation layer and the pool scope, so
    it is the only source that can prove a review belongs to the current
    structure.

    The library is read through the **manager API**, which is the Review
    Manager's own read surface. The healer never selects from the
    manager's table: a direct read would bind the healer to a layout it
    does not own and could show a generation the manager never published.

    The legacy ``cow_execution_reviews`` table has none of those columns. A
    review read from there cannot be tied to the current closed-loop library,
    so it can never satisfy "every review carries an explicit profit value"
    in a way an operator can audit.
    """
    effective_limit = REVIEW_QUERY_LIMIT if limit is None else int(limit)
    rows = manager_read_reviews(manager_api)
    if not rows and manager_api:
        # The manager API could not be read. That is a failed read, not an
        # empty library, and it must not be mistaken for one: the audit
        # reports the failure instead of reporting "no reviews".
        return []
    return rows[:max(0, effective_limit)] if effective_limit else rows


def legacy_review_rows(db: Path, hours: int, limit: int | None = None) -> list[dict[str, Any]]:
    """Read the legacy review table, which the Manager no longer owns."""
    # ``None`` means "use the default scan cap"; an explicit value is honoured
    # as given, including 0 which returns no rows at all.
    effective_limit = REVIEW_QUERY_LIMIT if limit is None else int(limit)
    suffix = f" LIMIT {max(0, effective_limit)}"
    return all_rows(db, "SELECT id,attempt_key,category,pair,tx_hash,created_at,payload_json,"
        "selected_profit_raw,selected_profit_amount,selected_profit_amount_symbol,"
        "selected_profit_source,selected_profit_missing_reason FROM cow_execution_reviews "
        "WHERE network=? AND chain_id=? AND created_at>=datetime('now',?) "
        "AND datetime(created_at)>=datetime(?) ORDER BY created_at DESC" + suffix,
        (NETWORK, CHAIN_ID, f"-{hours} hours", observation_started_at()))


def review_rows(
    db: Path,
    hours: int,
    limit: int | None = None,
    manager_api: str | None = None,
) -> list[dict[str, Any]]:
    """Return the review rows the healer reasons about.

    The Manager library is authoritative for the **structure** of a review:
    it is the only source that can prove a review belongs to the current
    closed-loop library. The legacy table is the only source that still
    carries the **measurement** of a review, because the runtime writes the
    measured best trade amount and the probe ladder into the review record
    it owns.

    The two are read in that order of authority. A store that has not
    materialized a Manager review yet is audited from the legacy table, so
    a migration in progress never loses its reviews. Once the Manager
    library holds rows it is the **only** audit source: every Manager row
    carries the measured value it was materialized from together with the
    upstream checksums that prove it belongs to the current structure.

    Reading the legacy table alongside the Manager library produced a
    second row for the same route, so the healer counted one route twice
    and the operator had to reconcile two rows for one review. A legacy row
    the Manager has not adopted is not an audit item: it is a runtime record
    of a route the current library does not own.

    Every row carries a ``review_source`` marker so a caller can tell which
    authority produced it.
    """
    manager_rows = manager_review_rows(db, limit, manager_api)
    for row in manager_rows:
        row["review_source"] = "manager"
    legacy_rows = legacy_review_rows(db, hours, limit)
    for row in legacy_rows:
        row["review_source"] = "legacy"
    if not manager_rows:
        return legacy_rows
    if not legacy_rows:
        return manager_rows
    # The Manager library is the **audit source**. Once it holds rows it is
    # the only authority the healer may report.
    return manager_rows


def review_row_carries_measurement(row: dict[str, Any]) -> bool:
    """Whether a review row carries a measured best trade amount.

    The Manager library records the **structure** of a review: the closed
    loop it belongs to, the upstream versions and the audit trail. Its
    timestamp is the moment the review was materialized, so it says nothing
    about how fresh the measurement is.

    The legacy library records the **measurement**: the measured best trade
    amount, the probe ladder and the signed profit. Its timestamp is the
    moment the runtime measured the route.

    A Manager row therefore never hides a measured legacy row, even when the
    Manager row was materialized later. The two facts are complementary, and
    a row that carries a measurement always wins over a row that does not.
    """
    state = best_amount_state(row)
    return not state.get("missing") and state.get("bestAmount") is not None


def review_row_measurement_time(row: dict[str, Any]) -> str:
    """Return the moment the route was measured, for any row of either library.

    The legacy row is the measurement itself, so its creation time is the
    measurement time. A Manager row carries the measurement it was
    materialized from and stamps that moment into its static preview, so the
    stamp is the measurement time and not the materialization time.
    """
    if str(row.get("review_source") or "") == "legacy":
        return str(row.get("created_at") or "")
    preview = as_json(row.get("static_preview_json"))
    if isinstance(preview, dict):
        stamped = str(preview.get("measuredAt") or "").strip()
        if stamped:
            return stamped
    return ""


def review_row_is_newer(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    """Whether one review row should replace another for the same route.

    A row that carries a measured best trade amount always wins over a row
    that does not, because the measurement is the fact the route is judged
    by and a row without one carries no conclusion at all.

    Two rows that both carry a measurement are ordered by the moment the
    route was **measured**, not by the moment a row was written. The Manager
    row's timestamp is the moment the review was materialized, so it is
    later than the measurement it already carries and it would otherwise
    freeze the first measurement forever.
    """
    # A row that carries an explicit profit value is the row that answers
    # the question the healer asks. The Manager library is the audit
    # source, so a materialized Manager row that carries a value must
    # not be hidden by a legacy row that explicitly has none.
    candidate_value = pnl(candidate) is not None
    current_value = pnl(current) is not None
    if candidate_value != current_value:
        return candidate_value
    candidate_measured = review_row_carries_measurement(candidate)
    current_measured = review_row_carries_measurement(current)
    if candidate_measured != current_measured:
        return candidate_measured
    # Both rows carry a measurement. The legacy row is the **measurement**
    # the runtime wrote; the Manager row is a copy of a measurement that was
    # already copied, and its timestamp is the moment of the copy. The
    # legacy row therefore always wins, so the copy can never freeze the
    # measurement it holds.
    candidate_source = str(candidate.get("review_source") or "")
    current_source = str(current.get("review_source") or "")
    if candidate_source != current_source:
        return candidate_source == "legacy"
    candidate_at = review_measurement_time(candidate)
    current_at = review_measurement_time(current)
    if not candidate_at:
        return False
    if not current_at:
        return True
    return candidate_at > current_at


def review_measurement_time(row: dict[str, Any]) -> str:
    """Return the moment the route was measured.

    The runtime writes the measurement into the legacy review record, so the
    record's own creation time is the measurement time. A Manager row
    carries the measurement it was materialized with, so its measurement
    time is the time of the review it was materialized from.
    """
    measured_at = str(row.get("measured_at") or row.get("measurement_at") or "")
    if measured_at:
        return measured_at
    stamped = review_row_measurement_time(row)
    if stamped:
        return stamped
    # A Manager row without a stamp carries no measurement time at all: its
    # own timestamp is the moment it was materialized, which says nothing
    # about the market. It therefore never wins against a measured row.
    return ""


def pnl_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values, currencies = [], {}
    positive_amount = 0
    executable_positive = 0
    positive_estimate_only = 0
    confirmed_sum = 0.0
    for row in rows:
        signed = pnl(row)
        if signed is None:
            continue
        value, currency = float(signed["amount"]), str(signed["currency"])
        values.append(value)
        currencies[currency] = currencies.get(currency, 0.0) + value
        if value > 0:
            positive_amount += 1
            category = str(row.get("category") or "").strip().lower()
            source = str(signed.get("source") or "").strip().lower()
            if category == "executable":
                executable_positive += 1
                confirmed_sum += value
            elif source == "native_contract_rate_delta_estimate":
                # A positive value that comes from a rate-delta estimate is
                # not an executable, on-chain confirmed profit.
                positive_estimate_only += 1
    return {"total": len(rows), "withValue": len(values), "missingValue": len(rows) - len(values),
            "positive": executable_positive, "positiveAmount": positive_amount,
            "positiveEstimateOnly": positive_estimate_only,
            "estimatedProfitOnlyCount": positive_estimate_only,
            "confirmedSum": confirmed_sum,
            "nonPositive": len(values) - positive_amount,
            "sum": sum(values), "minimum": min(values) if values else None,
            "maximum": max(values) if values else None, "byCurrency": currencies}


#: The four token libraries the healer audits. A library table that does not
#: exist in this store is reported as absent rather than raising: the
#: libraries are materialized independently, so a chain can legitimately own
#: only some of them, and a missing table is not a corrupt table.
TOKEN_LIBRARY_TABLES = (
    ("core", "token_core"),
    ("stocks", "token_stocks"),
    ("extension", "token_extension"),
    ("test", "token_test"),
)


#: The provenance every stock token has to carry, independent of the chain.
#:
#: ``admission_source`` and the registry seed are invariants of the token
#: manager: a stock token is always admitted by the stock registry and always
#: carries the registry seed in its provenance payload. The **metadata**
#: source is chain specific -- mainnet stocks are valued from the assets API
#: while testnet stocks carry on-chain V4 evidence -- so the healer must not
#: hardcode one chain's metadata source. It reads the chain's own registry
#: instead and compares each admitted token against the source that registry
#: declares for it.
STOCK_ADMISSION_SOURCE = "bootstrap:stock_registry"
STOCK_REGISTRY_SEED = "robinhood_stock_token_registry"


def official_funding_token_addresses(chain_id: int) -> dict[str, str]:
    """Return the official funding addresses of a chain.

    The funding addresses are chain specific: mainnet's test token lives on
    a different address than testnet's, and the wrapped native token differs
    as well. The chain's own token allowlist is therefore the authority. The
    hardcoded mainnet map is only the fallback for a chain whose allowlist
    cannot be read, and even then the chain id decides whether it applies.
    """
    try:
        allowlist = TOKEN_ALLOWLIST_FILES[int(chain_id)]
    except (KeyError, ValueError):
        return dict(OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL)
    try:
        payload = json.loads(allowlist.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL)
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return dict(OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL)
    resolved: dict[str, str] = {}
    for item in tokens.values():
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        address = str(item.get("address") or "").strip().lower()
        if symbol and address:
            resolved.setdefault(symbol, address)
    # A symbol the allowlist does not declare falls back to the mainnet map
    # only for the mainnet, where that map is the authority anyway.
    if int(chain_id) == 4663:
        for symbol, address in OFFICIAL_FUNDING_TOKEN_ADDRESS_BY_SYMBOL.items():
            resolved.setdefault(symbol, address.lower())
    return resolved


def stock_registry_provenance(chain_id: int) -> dict[str, dict[str, str]]:
    """Return the provenance the chain's own stock registry declares.

    The registry is the authority for what a stock token on this chain is
    allowed to look like. A chain whose registry is missing or unreadable
    yields an empty mapping, and the caller then treats every stock token as
    unverified rather than silently accepting it.
    """
    try:
        registry = registry_for_chain(chain_id)
    except (KeyError, ValueError):
        return {}
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return {}
    provenance: dict[str, dict[str, str]] = {}
    for registry_key, item in tokens.items():
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        provenance[symbol] = {
            "registryKey": str(registry_key),
            "address": str(item.get("address") or "").strip().lower(),
            "metadataSource": str(item.get("source") or "").strip(),
            "registryPath": str(registry),
        }
    return provenance


def audit_stock_provenance(db: Path) -> list[dict[str, Any]]:
    """Return every admitted stock token whose provenance does not hold.

    A stock token is valid when it was admitted by the stock registry, its
    provenance payload carries the registry seed, and its metadata source and
    address match the chain's own registry entry. The last two are read from
    the registry of the chain that is being audited, so the rule is identical
    on every chain even though the expected values differ per chain.
    """
    rows = all_rows(
        db,
        "SELECT token_symbol,token_address,metadata_source,admission_source,source_json "
        "FROM token_stocks WHERE network=? AND chain_id=? AND admission_status='admitted'",
        (NETWORK, CHAIN_ID),
    )
    if not rows:
        return []
    provenance = stock_registry_provenance(CHAIN_ID)
    if not provenance:
        # The chain's registry is unavailable, so the provenance of a stock
        # token cannot be verified. Reporting the tokens as invalid is the
        # safe answer: a defect that cannot be disproved is not a proof of
        # validity.
        return [{
            "token_symbol": row.get("token_symbol"),
            "token_address": row.get("token_address"),
            "metadata_source": row.get("metadata_source"),
            "admission_source": row.get("admission_source"),
            "reason": "stock_registry_unavailable",
        } for row in rows]
    invalid: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("token_symbol") or "").strip().upper()
        address = str(row.get("token_address") or "").strip().lower()
        admission_source = str(row.get("admission_source") or "").strip()
        metadata_source = str(row.get("metadata_source") or "").strip()
        source_json = str(row.get("source_json") or "")
        expected = provenance.get(symbol)
        reasons: list[str] = []
        if admission_source != STOCK_ADMISSION_SOURCE:
            reasons.append("admission_source_mismatch")
        if STOCK_REGISTRY_SEED not in source_json:
            reasons.append("registry_seed_missing")
        if expected is None:
            reasons.append("symbol_not_in_registry")
        else:
            if expected["address"] and address and expected["address"] != address:
                reasons.append("registry_address_mismatch")
            if expected["metadataSource"] and metadata_source != expected["metadataSource"]:
                reasons.append("registry_metadata_source_mismatch")
        if reasons:
            invalid.append({
                "token_symbol": row.get("token_symbol"),
                "token_address": row.get("token_address"),
                "metadata_source": row.get("metadata_source"),
                "admission_source": row.get("admission_source"),
                "reason": ",".join(reasons),
                "reasons": reasons,
                "expectedMetadataSource": (expected or {}).get("metadataSource"),
                "registryPath": (expected or {}).get("registryPath"),
            })
    return invalid


def audit_tokens(db: Path) -> dict[str, Any]:
    libraries = {}
    missing_tables = []
    present = table_names(db)
    for name, table in TOKEN_LIBRARY_TABLES:
        if present and table not in present:
            # The table does not exist in this store at all.
            missing_tables.append(table)
            libraries[name] = {"total": 0, "admitted": 0, "missing_decimals": 0,
                               "missing_symbol": 0, "tablePresent": False}
            continue
        libraries[name] = one(db, f"SELECT COUNT(*) total,"
            f"SUM(CASE WHEN admission_status='admitted' THEN 1 ELSE 0 END) admitted,"
            f"SUM(CASE WHEN token_decimals IS NULL OR token_decimals='' THEN 1 ELSE 0 END) missing_decimals,"
            f"SUM(CASE WHEN token_symbol IS NULL OR token_symbol='' THEN 1 ELSE 0 END) missing_symbol "
            f"FROM {table} WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)) or {}
        libraries[name]["tablePresent"] = True
    source = one(db, "SELECT COUNT(*) total,"
        "SUM(CASE WHEN token_decimals IS NULL OR token_decimals='' THEN 1 ELSE 0 END) missing_decimals,"
        "SUM(CASE WHEN token_symbol IS NULL OR token_symbol='' THEN 1 ELSE 0 END) missing_symbol "
        "FROM chain_tokens WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)) or {}
    admitted = sum(int(item.get("admitted") or 0) for item in libraries.values())
    missing = sum(int(item.get("missing_decimals") or 0) + int(item.get("missing_symbol") or 0)
                  for item in libraries.values())
    funding_rows = all_rows(
        db,
        "SELECT token_symbol,token_address,'core' library_name FROM token_core "
        "WHERE network=? AND chain_id=? AND admission_status='admitted' UNION ALL "
        "SELECT token_symbol,token_address,'test' library_name FROM token_test "
        "WHERE network=? AND chain_id=? AND admission_status='admitted'",
        (NETWORK, CHAIN_ID, NETWORK, CHAIN_ID),
    )
    funding_by_symbol = {
        str(row.get("token_symbol") or "").strip().upper():
        str(row.get("token_address") or "").strip().lower()
        for row in funding_rows
    }
    official_addresses = official_funding_token_addresses(CHAIN_ID)
    address_mismatches = [
        {
            "symbol": symbol,
            "expectedAddress": expected,
            "actualAddress": funding_by_symbol.get(symbol),
        }
        for symbol, expected in official_addresses.items()
        if symbol in FUNDING_TOKEN_SYMBOLS
        and funding_by_symbol.get(symbol) != expected
    ]
    placeholders = ",".join("?" for _ in KNOWN_COUNTERFEIT_FUNDING_TOKEN_ADDRESSES)
    counterfeit_rows = []
    for _, table in TOKEN_LIBRARY_TABLES:
        if table in missing_tables:
            continue
        counterfeit_rows.extend(all_rows(
            db,
            f"SELECT token_symbol,token_address,? library_name FROM {table} "
            f"WHERE network=? AND chain_id=? AND admission_status='admitted' "
            f"AND lower(token_address) IN ({placeholders})",
            (table, NETWORK, CHAIN_ID, *sorted(KNOWN_COUNTERFEIT_FUNDING_TOKEN_ADDRESSES)),
        ))
    stock_provenance_invalid = audit_stock_provenance(db)
    valid = (
        admitted > 0
        and missing == 0
        and not address_mismatches
        and not counterfeit_rows
        and not stock_provenance_invalid
    )
    defects = token_defects(db)
    return {"valid": valid, "source": source, "libraries": libraries,
            "admittedTotal": admitted, "libraryMissingFields": missing,
            "missingLibraryTables": missing_tables,
            "officialFundingAddresses": official_addresses,
            "fundingAddressMismatches": address_mismatches,
            "counterfeitFundingAdmissions": counterfeit_rows,
            "stockProvenanceInvalid": stock_provenance_invalid,
            # GAP-C: the repair needs the symbols, not just the counts.
            "defects": defects,
            "defectCount": len(defects),
            "defectKinds": sorted({str(item.get("kind") or "") for item in defects
                                   if item.get("kind")}),
            "resolvableTargets": resolve_token_targets(defects),
            "unresolvableDefectCount": sum(1 for item in defects
                                           if not item.get("symbol")
                                           or item.get("kind") == "counterfeit_admission")}


#: The token defects the healer can repair one symbol at a time. The
#: batch path (``materialize-libraries``) rewrites every library, which is
#: the wrong tool when exactly one symbol is broken: it is slow, it touches
#: rows that are already correct, and it cannot tell the operator which
#: symbol was actually repaired. ``resolve_token`` repairs one symbol
#: through the Manager API instead.
TOKEN_DEFECT_KINDS = (
    "missing_decimals",
    "missing_symbol",
    "funding_address_mismatch",
    "counterfeit_admission",
    "stock_provenance_invalid",
)


def token_defects(db: Path) -> list[dict[str, Any]]:
    """Return every per-symbol token defect the healer has to repair.

    The audit layer reports counts; this reports the *symbols*. The healer
    cannot repair "3 tokens are missing decimals" -- it has to know
    **which** three, so the repair can target them one by one through
    ``POST /v1/tokens/resolve``.

    An empty list is meaningful: it means every funding token resolves to
    its official address, every admitted token carries a symbol and
    decimals, and no counterfeit address is admitted. That is the normal
    state, and it must be distinguishable from "the audit did not run".
    """
    defects: list[dict[str, Any]] = []
    admitted_rows: list[dict[str, Any]] = []
    for _, table in TOKEN_LIBRARY_TABLES:
        if table not in table_names(db):
            continue
        admitted_rows.extend(all_rows(
            db,
            f"SELECT token_symbol,token_address,token_decimals,admission_status,"
            f"metadata_source,admission_source,source_json,'{table}' library_name "
            f"FROM {table} WHERE network=? AND chain_id=? AND admission_status='admitted'",
            (NETWORK, CHAIN_ID),
        ))
    if not admitted_rows:
        return defects
    official_addresses = official_funding_token_addresses(CHAIN_ID)
    stock_provenance = stock_registry_provenance(CHAIN_ID)
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in admitted_rows:
        symbol = str(row.get("token_symbol") or "").strip().upper()
        by_symbol.setdefault(symbol, []).append(row)
    # A symbol that is admitted without a symbol at all cannot be resolved
    # by symbol, so it is reported against its address instead.
    for row in admitted_rows:
        symbol = str(row.get("token_symbol") or "").strip().upper()
        address = str(row.get("token_address") or "").strip().lower()
        if not symbol:
            defects.append({
                "kind": "missing_symbol", "symbol": None, "address": address,
                "library": row.get("library_name"),
                "reason": "an admitted token carries no symbol, so it cannot be "
                          "resolved by symbol; it has to be removed or re-admitted "
                          "with its symbol",
            })
            continue
        if row.get("token_decimals") is None or str(row.get("token_decimals")) == "":
            defects.append({
                "kind": "missing_decimals", "symbol": symbol, "address": address,
                "library": row.get("library_name"),
                "reason": "the admitted token carries no decimals; the token "
                          "manager has to re-read them from the chain",
            })
        expected = official_addresses.get(symbol)
        counterfeit = address in KNOWN_COUNTERFEIT_FUNDING_TOKEN_ADDRESSES
        # A counterfeit address is also an address mismatch for a funding
        # symbol, but it must be reported **only** as a counterfeit: the
        # counterfeit verdict is the stronger one, and reporting both would
        # make the resolver believe the symbol is repairable. Resolving a
        # counterfeit symbol returns the same counterfeit.
        if counterfeit:
            defects.append({
                "kind": "counterfeit_admission", "symbol": symbol, "address": address,
                "library": row.get("library_name"),
                "expectedAddress": expected,
                "reason": "the admitted address is a known counterfeit; the "
                          "admission has to be withdrawn, not repaired",
            })
        elif expected is not None and address != expected:
            defects.append({
                "kind": "funding_address_mismatch", "symbol": symbol, "address": address,
                "library": row.get("library_name"),
                "expectedAddress": expected,
                "reason": "the funding token is admitted under an address that "
                          "is not its official address",
            })
        if row.get("library_name") == "token_stocks":
            source_json = str(row.get("source_json") or "")
            expected_source = stock_provenance.get(symbol)
            provenance_reasons: list[str] = []
            if str(row.get("admission_source") or "") != STOCK_ADMISSION_SOURCE:
                provenance_reasons.append("admission_source_mismatch")
            if STOCK_REGISTRY_SEED not in source_json:
                provenance_reasons.append("registry_seed_missing")
            if expected_source is None:
                provenance_reasons.append("symbol_not_in_registry")
            else:
                if (expected_source.get("address") and address
                        and expected_source.get("address") != address):
                    provenance_reasons.append("registry_address_mismatch")
                if (expected_source.get("metadataSource")
                        and str(row.get("metadata_source") or "")
                        != expected_source.get("metadataSource")):
                    provenance_reasons.append("registry_metadata_source_mismatch")
            if provenance_reasons:
                defects.append({
                    "kind": "stock_provenance_invalid", "symbol": symbol, "address": address,
                    "library": row.get("library_name"),
                    "reason": "the stock token is admitted without the stock "
                              "registry provenance the token library requires",
                    "reasons": provenance_reasons,
                    "expectedMetadataSource": (expected_source or {}).get("metadataSource"),
                })
    return defects


#: The token defects the per-symbol resolver can actually repair.
#:
#: ``POST /v1/tokens/resolve`` re-reads one symbol from the chain and
#: repairs its metadata. It cannot repair a defect that lives in the
#: **address** of an admitted token: the address is the identity of the
#: token, so resolving the symbol would only return the same wrong address.
#: Such a defect has to be withdrawn or re-materialized by the batch path
#: instead, and the healer says so instead of pretending it repaired the
#: token.
RESOLVABLE_TOKEN_DEFECT_KINDS = (
    "missing_decimals",
    "missing_symbol",
)
#: The token defects only the batch path can repair.
BATCH_ONLY_TOKEN_DEFECT_KINDS = (
    "funding_address_mismatch",
    "counterfeit_admission",
    "stock_provenance_invalid",
)


def resolve_token_targets(defects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the symbols the per-symbol resolver can actually repair.

    ``POST /v1/tokens/resolve`` resolves a **symbol** and re-reads its
    metadata from the chain. A counterfeit admission is not a resolvable
    symbol: the address itself is wrong, so resolving the symbol would only
    return the same counterfeit. The same holds for an address mismatch and
    for a broken stock provenance: all three live in the identity of the
    admitted row, which the resolver reads but never rewrites. Those have to
    be withdrawn or re-materialized by the batch path instead, and the healer
    says so instead of pretending it repaired the token.
    """
    targets: dict[str, dict[str, Any]] = {}
    for defect in defects:
        symbol = defect.get("symbol")
        if not symbol:
            continue
        kind = str(defect.get("kind") or "")
        if kind in BATCH_ONLY_TOKEN_DEFECT_KINDS:
            continue
        if kind not in RESOLVABLE_TOKEN_DEFECT_KINDS:
            continue
        entry = targets.setdefault(str(symbol), {
            "symbol": str(symbol), "defectKinds": [], "addresses": [],
        })
        kind = str(defect.get("kind") or "")
        if kind and kind not in entry["defectKinds"]:
            entry["defectKinds"].append(kind)
        address = defect.get("address")
        if address and address not in entry["addresses"]:
            entry["addresses"].append(address)
    return sorted(targets.values(), key=lambda item: item["symbol"])


def closed_loop_scope_drift(scope_drift: dict[str, Any] | None) -> dict[str, Any]:
    """Describe a closed-loop layer that records the wrong pool scope.

    The pool scope drift audit compares every materialized layer against the
    scope it belongs to. A layer that drifted is a **closed-loop** defect
    when the closed-loop library itself carries a checksum that is not the
    `core4` scope checksum: the library was materialized from the
    observation scope, so it can no longer prove which pool set its hops
    belong to.

    This has to be separated from a hop that references a pool the current
    candidate set dropped. That second case is a genuine data defect and
    needs the pool set fixed first; the first case only needs the closed
    loop library rebuilt from the correct scope.
    """
    drift = scope_drift or {}
    drifted_layers = [str(name) for name in (drift.get("driftedLayers") or [])]
    closed_loop_drifted = CLOSED_LOOP_SCOPE_LAYER in drifted_layers
    missing_hops = int(drift.get("missingPoolHopCount") or 0)
    affected_loops = int(drift.get("affectedLoopCount") or 0)
    return {
        "closedLoopScopeDrifted": closed_loop_drifted,
        "driftedLayers": drifted_layers,
        "missingPoolHopCount": missing_hops,
        "affectedLoopCount": affected_loops,
        # A hop that names a pool outside the candidate set is a pool-set
        # defect; it has to be fixed before the closed loop can be rebuilt
        # from a scope that actually contains the hop.
        "hopsMissingFromCandidateScope": missing_hops > 0,
        "reason": (
            "the closed-loop library records a pool scope checksum that is not "
            "the core4 scope checksum; rebuilding it from the current scope "
            "restores the scope contract without touching the pool set"
            if closed_loop_drifted and missing_hops == 0
            else "closed-loop hops reference pools the current candidate scope "
                 "does not contain; the pool set has to be repaired first"
            if closed_loop_drifted
            else None
        ),
    }


def cascade_matrix(db: Path, manager_api: str | None = None) -> dict[str, Any]:
    """Audit the whole cascade from the funding token to the closed loop.

    The plan requires the cascade to be examined **in full range**, from
    the funding token through the candidate token, the candidate pool and
    the closed loop. Each link has to be counted explicitly, because an
    empty set is the signal that the chain above it is broken: a silent
    "skipped" would make the convergence loop read an empty result as
    health.

    The four stages are:

    * **F** - the funding tokens: the official address is unique, the
      symbol and the decimals are present, and no counterfeit address is
      admitted;
    * **C** - the candidate tokens: every admitted token of the candidate
      scope, with its valuation state;
    * **P** - the candidate pools: fixed fee in range, no dynamic fee, no
      hook, and a depth reading inside the band;
    * **L** - the closed loops: exactly three hops, and every hop hits a
      pool the current candidate scope contains.

    Every count is reported even when it is zero.
    """
    official = official_funding_token_addresses(CHAIN_ID)
    present = table_names(db)
    admitted_by_library: dict[str, int] = {}
    funding_seen: dict[str, list[str]] = {}
    candidate_tokens: list[dict[str, Any]] = []
    for _, table in TOKEN_LIBRARY_TABLES:
        if table not in present:
            admitted_by_library[table] = 0
            continue
        admitted_by_library[table] = int((one(db,
            f"SELECT COUNT(*) n FROM {table} WHERE network=? AND chain_id=? "
            f"AND admission_status='admitted'", (NETWORK, CHAIN_ID)) or {}).get("n") or 0)
        for row in all_rows(db,
                f"SELECT token_symbol,token_address,token_decimals,admission_status "
                f"FROM {table} WHERE network=? AND chain_id=? AND admission_status='admitted'",
                (NETWORK, CHAIN_ID)):
            symbol = str(row.get("token_symbol") or "").strip().upper()
            address = str(row.get("token_address") or "").strip().lower()
            funding_seen.setdefault(symbol, []).append(address)
            candidate_tokens.append({
                "symbol": symbol, "address": address, "library": table,
                "decimals": row.get("token_decimals"),
                "isFundingToken": symbol in FUNDING_TOKEN_SYMBOLS,
                "addressMatchesOfficial": (official.get(symbol) == address
                                           if symbol in official else None),
            })
    funding_valid = 0
    funding_invalid: list[dict[str, Any]] = []
    # The allowlist carries every token of the chain, but only the funding
    # symbols have to be admitted for the arbitrage loop to close. The
    # matrix therefore checks the funding set, not the whole allowlist.
    for symbol in FUNDING_TOKEN_SYMBOLS:
        expected = official.get(symbol)
        if expected is None:
            continue
        seen = funding_seen.get(symbol) or []
        if not seen:
            funding_invalid.append({"symbol": symbol, "reason": "not_admitted",
                                    "expectedAddress": expected})
            continue
        distinct = {value for value in seen}
        if distinct != {expected}:
            funding_invalid.append({"symbol": symbol, "reason": "address_mismatch",
                                    "expectedAddress": expected,
                                    "actualAddresses": sorted(distinct)})
            continue
        funding_valid += 1
    counterfeit_admitted = sorted({
        str(item.get("token_address") or "").lower()
        for item in candidate_tokens
        if str(item.get("address") or "") in KNOWN_COUNTERFEIT_FUNDING_TOKEN_ADDRESSES})
    # ---- candidate pools -------------------------------------------------
    candidate_pools = all_rows(db,
        "SELECT DISTINCT d.pool_id pool_id,d.depth_status depth_status "
        "FROM dex_candidate_pools d WHERE d.network=? AND d.chain_id=?",
        (NETWORK, CHAIN_ID))
    pool_ids = {str(row.get("pool_id") or "") for row in candidate_pools if row.get("pool_id")}
    pool_role_counts = one(db,
        "SELECT COUNT(DISTINCT pool_id) total FROM dex_candidate_pools "
        "WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)) or {}
    # ---- closed loops ----------------------------------------------------
    # The **closed-loop Manager** owns the closed-loop catalog, so it is
    # read through the manager API.  The healer never selects from the
    # manager's table: a direct read would bind the healer to the layout it
    # does not own and could show a generation the manager never published.
    loops = []
    for row in (manager_read_closed_loops(manager_api).get("loops") or []):
        pool_ids_value = row.get("poolIds")
        if pool_ids_value is None:
            pool_ids_value = row.get("pool_ids")
        loops.append({
            "loop_key": str(row.get("loopKey") or row.get("loop_key") or ""),
            "pool_ids_json": json.dumps(pool_ids_value) if isinstance(pool_ids_value, list) else "[]",
            "path_symbols_json": json.dumps(
                row.get("pathSymbols") or row.get("path_symbols") or []
            ),
            "anchor_symbol": str(row.get("anchorSymbol") or row.get("anchor_symbol") or ""),
            "status": str(row.get("status") or ""),
        })
    exact_three = 0
    hops_total = 0
    hops_in_scope = 0
    loops_with_missing_hop = 0
    for loop in loops:
        try:
            hop_pool_ids = json.loads(loop.get("pool_ids_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            hop_pool_ids = []
        if not isinstance(hop_pool_ids, list):
            hop_pool_ids = []
        hops_total += len(hop_pool_ids)
        in_scope = sum(1 for pool_id in hop_pool_ids
                       if str(pool_id).lower() in {value.lower() for value in pool_ids})
        hops_in_scope += in_scope
        if len(hop_pool_ids) == 3:
            exact_three += 1
        if in_scope != len(hop_pool_ids):
            loops_with_missing_hop += 1
    # The allowlist carries every token of the chain, but only the funding
    # symbols have to be admitted for the arbitrage loop to close. The
    # matrix therefore reports the funding set, not the whole allowlist.
    funding_token_set = {symbol for symbol in FUNDING_TOKEN_SYMBOLS
                         if symbol in official}
    funding_total = len(funding_token_set)
    candidate_token_total = len(candidate_tokens)
    candidate_token_set = {item["symbol"] for item in candidate_tokens if item["symbol"]}
    return {
        "fundingTokens": {
            "expected": funding_total,
            "symbols": sorted(funding_token_set),
            "valid": funding_valid,
            "invalid": funding_invalid,
            "invalidCount": len(funding_invalid),
            "counterfeitAdmitted": counterfeit_admitted,
            "counterfeitAdmittedCount": len(counterfeit_admitted),
            "complete": funding_valid == funding_total and not counterfeit_admitted,
        },
        "candidateTokens": {
            "admitted": candidate_token_total,
            "byLibrary": admitted_by_library,
            "symbols": sorted(candidate_token_set),
            "symbolCount": len(candidate_token_set),
            "fundingSymbolCount": len(funding_token_set & candidate_token_set),
            "nonFundingSymbolCount": len(candidate_token_set - funding_token_set),
            "missingDecimals": sorted({
                str(item["symbol"]) for item in candidate_tokens
                if item.get("decimals") is None or str(item.get("decimals")) == ""}),
        },
        "candidatePools": {
            "total": int(pool_role_counts.get("total") or 0),
            "distinctPoolCount": len(pool_ids),
            "byDepthStatus": {
                status: sum(1 for row in candidate_pools
                            if str(row.get("depth_status") or "") == status)
                for status in sorted({str(row.get("depth_status") or "")
                                      for row in candidate_pools})
            },
        },
        "closedLoops": {
            "total": len(loops),
            "exactThreeHops": exact_three,
            "notExactThreeHops": len(loops) - exact_three,
            "hopsTotal": hops_total,
            "hopsInCandidateScope": hops_in_scope,
            "hopsMissingFromCandidateScope": hops_total - hops_in_scope,
            "loopsWithMissingHop": loops_with_missing_hop,
            "allHopsInScope": hops_total == hops_in_scope,
            "allExactThreeHops": exact_three == len(loops) and bool(loops),
        },
    }


def audit_pools(
    db: Path,
    hours: int = 24,
    tools: Path | None = None,
    manager_api: str | None = None,
) -> dict[str, Any]:
    scope = one(db, "SELECT enabled,materialized_checksum,last_rebuilt_at FROM pool_scope_config "
        "WHERE network=? AND chain_id=? AND scope_key=?", (NETWORK, CHAIN_ID, SCOPE))
    candidate = one(db, "SELECT eligible_pool_count,candidate_token_count,abnormal_pool_count,"
        "materialized_checksum,generated_at FROM manager_dex_candidate_library_state "
        "WHERE network=? AND chain_id=? AND scope_key=?", (NETWORK, CHAIN_ID, SCOPE))
    observation = one(db, "SELECT pool_count,direction_count,empty_direction_count,abnormal_pool_count,"
        "materialized_checksum,generated_at FROM manager_market_observation_library_state "
        "WHERE network=? AND chain_id=? AND scope_key='candidate_exchange'", (NETWORK, CHAIN_ID))
    exchange = one(db, "SELECT COUNT(*) total,"
        "SUM(CASE WHEN token0 IS NULL OR token0='' OR token1 IS NULL OR token1='' THEN 1 ELSE 0 END) missing_currencies,"
        "SUM(CASE WHEN fee<=0 OR dynamic_fee!=0 OR special_fee!=0 THEN 1 ELSE 0 END) filtered_fee "
        "FROM v4_exchange_pools WHERE network=? AND chain_id=?", (NETWORK, CHAIN_ID)) or {}
    depth = pool_depth_audit(db)
    best_amount = best_amount_audit(db, hours, manager_api)
    # A pool-manager binary that predates a command rejects it with
    # "unknown command". The healer used to carry on as if the step had
    # succeeded, which turned a deployment gap into a silent no-op: the
    # depth refresh never ran and the capacity columns never appeared while
    # the report still looked healthy. The probe is read-only.
    binary_support = (pool_manager_command_support(tools) if tools is not None
                      else {"available": None, "complete": None,
                            "reason": "pool_manager_binary_not_probed",
                            "missingCommands": list(POOL_MANAGER_REQUIRED_COMMANDS)})
    binary_complete = binary_support.get("complete")
    valid = bool(scope and scope.get("enabled") and scope.get("materialized_checksum") and candidate
                 and int(candidate.get("eligible_pool_count") or 0) > 0 and observation
                 and int(observation.get("pool_count") or 0) > 0
                 and depth["valid"] and best_amount["valid"]
                 and binary_complete is not False)
    return {"valid": valid, "scope": scope, "candidate": candidate, "observation": observation,
            "exchange": exchange, "depth": depth, "bestAmount": best_amount,
            "binarySupport": binary_support}


MIN_POOL_TVL_USD = 100_000.0
GAS_ESTIMATE_ETH = 6.2e-06


def best_amount_audit(
    db: Path,
    hours: int,
    manager_api: str | None = None,
) -> dict[str, Any]:
    """Audit whether confirmed best trade amounts are economically usable.

    A best trade amount that only yields a profit far below the gas estimate
    is not a market condition: it means the selected pools cannot carry a
    meaningful position. Such routes must be reported as a pool-depth defect
    instead of being counted as "positive but not executable" profit.
    """
    rows = review_rows(db, hours, None, manager_api)
    confirmed = []
    for row in rows:
        state = best_amount_state(row)
        if state["missing"] or state["bestAmount"] is None:
            continue
        try:
            amount = float(state["bestAmount"])
        except (TypeError, ValueError):
            continue
        confirmed.append((row, state, amount))
    too_small = []
    below_gas = []
    measured_zero = []
    by_symbol: dict[str, list[float]] = {}
    for row, state, amount in confirmed:
        symbol = str(state.get("requestedSymbol") or "unknown").upper()
        by_symbol.setdefault(symbol, []).append(amount)
        payloads = review_payloads(row)
        evidence = _live_onchain_evidence(payloads)
        gate = evidence.get("offchainGasGate") or _first_present(
            payloads, "offchainGasGate", "offchain_gas_gate") or {}
        blocked = bool(gate.get("blocked"))
        reason = str(gate.get("reason") or "")[:200]
        if blocked and reason == "expected_loss_after_gas_before_broadcast":
            below_gas.append({"reviewId": row.get("id"), "symbol": symbol,
                              "bestAmount": amount, "reason": reason,
                              "gasEstimateEth": gate.get("gasEstimateEth"),
                              "netAfterGas": gate.get("netAfterGas")})
        if amount <= 0:
            # A best trade amount of zero is only a defect when the kernel
            # never measured it.  A **measured** zero is a market conclusion:
            # the ladder priced every size it could carry and none of them
            # paid, so there is no profitable position to take.  That is a
            # correct answer, not a shallow pool.
            #
            # The audit used to report every non-positive amount as
            # `non_positive_best_amount`, which fed `best_amount_too_small`
            # and therefore the pool-depth repair path.  The healer then
            # tried to replace pools for a route that was simply not
            # profitable at any size, and the pool scope churned on every
            # round without ever producing a position.
            #
            # The design says it explicitly (see the single-tick capacity
            # document, section 2.4.2): ``x_single == 0`` is a **measured
            # non-pass** that must be propagated as a measurement, never as a
            # missing value and never as a pool defect.
            if state.get("measured"):
                measured_zero.append({"reviewId": row.get("id"), "symbol": symbol,
                                      "bestAmount": amount,
                                      "reason": "measured_no_profitable_size",
                                      "zeroReason": state.get("zeroReason") or "",
                                      "probeCount": state.get("probeCount")})
            else:
                too_small.append({"reviewId": row.get("id"), "symbol": symbol,
                                  "bestAmount": amount,
                                  "reason": "non_positive_unmeasured_best_amount"})
    # A confirmed best trade amount is only usable when it both clears the gas
    # estimate and is a positive trade size. Either defect means the selected
    # pools cannot carry a meaningful position, so both feed the same
    # insufficiency counter.
    insufficient = len(below_gas) + len(too_small)
    return {
        "code": BEST_AMOUNT_TOO_SMALL_CODE if insufficient else None,
        "valid": insufficient == 0,
        "confirmedCount": len(confirmed),
        "belowGasCount": len(below_gas),
        "tooSmallCount": len(too_small),
        "insufficientCount": insufficient,
        "measuredZeroCount": len(measured_zero),
        "gasEstimateEth": GAS_ESTIMATE_ETH,
        "reason": ("confirmed best trade amounts cannot cover the gas estimate; "
                   "the selected pools are too shallow for a meaningful position"),
        "bySymbol": {symbol: {"count": len(values), "min": min(values),
                              "median": sorted(values)[len(values) // 2],
                              "max": max(values)}
                      for symbol, values in sorted(by_symbol.items())},
        "belowGasSamples": below_gas[:10],
        "tooSmallSamples": too_small[:10],
        "measuredZeroSamples": measured_zero[:10],
    }


#: The depth verdicts of the pool manager that mean "this pool can carry a
#: position".
#:
#: The pool manager derives the verdict from the on-chain capacity of the
#: pool, which is the quantity that decides whether a position can actually
#: be routed through it. The USD valuation is only a tie-breaker and is
#: denominated in the chain's own quote asset, so it cannot be compared
#: against a fixed USD band on every chain. The audit therefore reads the
#: pool manager's verdict instead of re-deriving one.
ADEQUATE_DEPTH_STATUSES = frozenset({"deep", "liquid", "medium"})
#: The depth verdicts that mean "the pool really is too shallow", so the
#: repair is to replace it with a deeper one.
THIN_DEPTH_STATUSES = frozenset({"thin", "shallow", "dust", "unpriced"})


def pool_depth_audit(db: Path) -> dict[str, Any]:
    """Audit candidate pool depth against the minimum TVL needed for a usable best amount.

    A candidate pool whose on-chain TVL stays below the minimum depth band can
    only produce dust-sized best trade amounts, whose profit never covers gas.
    Such pools are a pool-selection defect, not a market condition, so they
    must block the pipeline instead of being carried as "no profit".
    """
    # ``dex_candidate_pools`` carries one row per (pool, anchor_scope) pair, so
    # a plain COUNT(*) double counts pools that appear under several scopes.
    # The depth verdict must be per pool, therefore count distinct pool ids.
    #
    # The **adequacy** criterion is the pool manager's own depth verdict, which
    # is derived from the on-chain capacity of the pool. It is deliberately not
    # a USD TVL band: the USD valuation is denominated in the chain's own
    # quote asset, so on a chain whose quote is not the USD the band rejects
    # every pool. A pool whose verdict is one of the adequate statuses can
    # carry a position, whatever the USD valuation says.
    #
    # The **bad reading** count stays a USD-band check, because a valuation
    # that is wildly out of range is exactly the malformed reading that has to
    # be re-measured. Every TVL comparison is cast to REAL first: SQLite
    # compares a TEXT column against a bound lexicographically when the column
    # affinity is TEXT, which silently makes ">= 100000" false for every
    # stored value.
    adequate_statuses = ",".join(f"'{status}'" for status in sorted(ADEQUATE_DEPTH_STATUSES))
    # The **adequate** criterion is the pool manager's own depth verdict, and
    # nothing else. The USD valuation is deliberately NOT part of it: the
    # shared depth policy lists ``depth_admission`` and
    # ``capacity_admission`` under ``usdgPegPolicy.forbiddenUses``, and the
    # legacy ``estimated_tvl_usd`` column is a reference-only valuation that
    # the current pool manager no longer maintains. Gating on it produced a
    # false "bad reading" for every pool whose stale column still carried a
    # value from the retired TVL valuation path.
    #
    # The **bad reading** count stays a reference-only diagnostic: it reports
    # that a stale or malformed USD number is present, but it never blocks the
    # layer. Every TVL comparison is cast to REAL first: SQLite compares a
    # TEXT column against a bound lexicographically when the column affinity
    # is TEXT, which silently makes ">= 100000" false for every stored value.
    rows = all_rows(db, "SELECT d.depth_status depth_status,"
        "COUNT(*) row_count,"
        "COUNT(DISTINCT d.pool_id) pool_count,"
        "COUNT(DISTINCT CASE WHEN COALESCE(s.depth_status, d.depth_status, '') IN ("
        + adequate_statuses + ") THEN d.pool_id END) "
        "adequate_pool_count,"
        "COUNT(DISTINCT CASE WHEN CAST(COALESCE(s.estimated_tvl_usd,0) AS REAL)>? "
        "THEN d.pool_id END) bad_reading_count,"
        "MIN(CAST(s.estimated_tvl_usd AS REAL)) min_tvl_usd,"
        "MAX(CAST(s.estimated_tvl_usd AS REAL)) max_tvl_usd,"
        "AVG(CAST(s.estimated_tvl_usd AS REAL)) avg_tvl_usd "
        "FROM dex_candidate_pools d "
        "LEFT JOIN v4_exchange_pool_liquidity_snapshots s "
        "  ON s.network=d.network AND s.chain_id=d.chain_id AND s.protocol=d.protocol "
        " AND s.pool_id=d.pool_id "
        "WHERE d.network=? AND d.chain_id=? GROUP BY d.depth_status",
        (MAX_POOL_TVL_USD, NETWORK, CHAIN_ID))
    by_status = {str(row.get("depth_status") or "unknown"): row for row in rows}
    total = sum(int(row.get("pool_count") or 0) for row in rows)
    healthy = sum(int(row.get("adequate_pool_count") or 0) for row in rows)
    bad_readings = sum(int(row.get("bad_reading_count") or 0) for row in rows)
    insufficient = total - healthy
    valid = total > 0 and healthy > 0 and insufficient == 0
    # GAP-D: "the pool has no capacity" and "the depth reading is broken"
    # are different defects with different repairs, and folding them into
    # one status sends the chain after the wrong tool.
    #
    # A pool is a **bad reading** when its stored TVL is outside the
    # plausible band: either above the ceiling (the raw liquidity index was
    # stored as a TVL) or missing entirely. Both mean the number is not
    # trustworthy, so the fix is to re-measure the pool, not to swap it.
    #
    # A pool is **genuinely thin** when its reading is present and inside
    # the band but below the floor. That is a market fact: the pool really
    # is too shallow, so the fix is to replace it with a deeper pool.
    # The depth evidence columns were added to the snapshot table after the
    # first stores were written, so a store that predates them does not carry
    # them at all. Selecting them unconditionally made the audit fail with an
    # ``OperationalError`` on every such store, which turned a coverage gap
    # into a crash of the whole healer. The columns are therefore rendered
    # against the schema the store actually has.
    snapshot_columns = table_columns(db, "v4_exchange_pool_liquidity_snapshots")
    depth_basis_expression = ("s.depth_basis" if "depth_basis" in snapshot_columns
                               else "NULL")
    binding_expression = ("s.capacity_binding_raw" if "capacity_binding_raw" in snapshot_columns
                          else "NULL")
    # A store that cannot hold the measured binding-side capacity has no
    # admission input at all: the kernel cannot tell an admitted pool from a
    # rejected one, so every pool is a coverage gap. This is a **store
    # defect**, not a market condition, and it is repaired by adding the
    # columns and re-measuring, never by replacing a pool.
    capacity_column_missing = "capacity_binding_raw" not in snapshot_columns
    reading_rows = all_rows(db,
        "SELECT d.pool_id pool_id,d.depth_status candidate_depth_status,"
        "s.depth_status measured_depth_status,"
        "s.estimated_tvl_usd estimated_tvl_usd,s.has_liquidity has_liquidity,"
        f"{depth_basis_expression} depth_basis,{binding_expression} capacity_binding_raw "
        "FROM dex_candidate_pools d "
        "LEFT JOIN v4_exchange_pool_liquidity_snapshots s "
        "  ON s.network=d.network AND s.chain_id=d.chain_id AND s.protocol=d.protocol "
        " AND s.pool_id=d.pool_id "
        "WHERE d.network=? AND d.chain_id=?",
        (NETWORK, CHAIN_ID))
    bad_reading_pools: set[str] = set()
    stale_reading_pools: set[str] = set()
    unmeasured_pools: set[str] = set()
    thin_pools: set[str] = set()
    adequate_pools: set[str] = set()
    capacity_unknown_pools: set[str] = set()
    for row in reading_rows:
        pool_id = str(row.get("pool_id") or "")
        if not pool_id:
            continue
        # The **measured** verdict of the pool manager is the authority: it
        # is derived from the on-chain capacity of the pool, which is the
        # quantity that decides whether a position can actually be routed.
        # The USD valuation is only a tie-breaker, and it is denominated in
        # the chain's own quote asset, so it cannot be compared against a
        # fixed USD band on every chain.
        measured = str(row.get("measured_depth_status") or "").strip().lower()
        candidate = str(row.get("candidate_depth_status") or "").strip().lower()
        status = measured or candidate
        if not status:
            # No verdict at all: the pool has never been measured, so the
            # reading is missing rather than thin.
            bad_reading_pools.add(pool_id)
            unmeasured_pools.add(pool_id)
            continue
        # The USD valuation is a **reference-only** observation. The shared
        # depth policy forbids a USD band from taking part in the admission
        # decision, and the current pool manager no longer maintains the
        # legacy ``estimated_tvl_usd`` column, so a stale value from the
        # retired TVL valuation path must not decide whether the pool is
        # adequate. It is still reported as a stale/malformed reading, but it
        # no longer blocks the layer and it no longer removes the pool from
        # the adequate set.
        reading = row.get("estimated_tvl_usd")
        try:
            reading_value = float(reading) if reading is not None else 0.0
        except (TypeError, ValueError):
            reading_value = 0.0
        stale_reading = reading_value > MAX_POOL_TVL_USD
        if stale_reading:
            stale_reading_pools.add(pool_id)
        # The pool carries a depth label but the store cannot say whether
        # the label was derived from a measured capacity. Without the
        # capacity the kernel cannot admit the pool, so it has to be
        # re-measured before anyone calls it adequate or thin.
        if capacity_column_missing or row.get("capacity_binding_raw") is None:
            capacity_unknown_pools.add(pool_id)
            bad_reading_pools.add(pool_id)
            unmeasured_pools.add(pool_id)
            continue
        if status in ADEQUATE_DEPTH_STATUSES:
            adequate_pools.add(pool_id)
            continue
        if status in THIN_DEPTH_STATUSES:
            thin_pools.add(pool_id)
            continue
        # A status the audit does not know is not a verdict about the depth.
        # The pool has to be re-measured before anyone can call it thin.
        bad_reading_pools.add(pool_id)
    # A pool can carry both an unknown status and a thin classification when
    # it has several snapshot rows. The unknown status wins: a pool whose
    # verdict the audit does not recognise has to be re-measured before
    # anyone can call the pool thin.
    genuinely_thin = thin_pools - bad_reading_pools
    # The aggregate above judges the depth label only. When the store cannot
    # hold the measured capacity, no label can be trusted to describe an
    # admissible pool, so the layer is not valid even though the labels
    # look adequate.
    if capacity_unknown_pools:
        valid = False
    # A pool whose depth verdict the audit does not recognise cannot be
    # called adequate either, so the layer stays invalid until it is
    # re-measured.
    if bad_reading_pools:
        valid = False
    # Three different defects live here and they carry three different
    # repairs, so they must never be folded into one status:
    #
    # * ``pool_capacity_store_defect`` - the store **cannot hold** the
    #   measured capacity at all, because the depth-evidence columns are
    #   missing. The kernel has no admission input for any pool. The repair
    #   adds the columns and re-measures.
    # * ``pool_depth_unmeasured`` - the store can hold the capacity, but
    #   these particular pools were never measured, so their admission
    #   input is still missing. That is a coverage gap of the measurement
    #   pass, not a defect of the store and not a market fact. The repair
    #   re-measures the pools.
    # * ``pool_depth_insufficient`` - the pool was measured and the market
    #   really is too shallow. The repair replaces the pool.
    capacity_store_defect = capacity_column_missing
    unmeasured_only = bool(capacity_unknown_pools) and not capacity_store_defect
    classification = (
        "pool_capacity_store_defect" if capacity_store_defect
        else "pool_depth_unmeasured" if unmeasured_only
        else "pool_reading_invalid" if bad_reading_pools and not genuinely_thin
        else "pool_depth_insufficient" if genuinely_thin
        else "pool_depth_insufficient" if bad_reading_pools
        else None)
    repair_path = (
        "ensure_depth_schema_and_remeasure" if capacity_store_defect
        else "remeasure_pool_depth" if unmeasured_only
        else "remeasure_pool_depth" if bad_reading_pools and not genuinely_thin
        else "replace_shallow_pool" if genuinely_thin and not bad_reading_pools
        else "replace_shallow_pool_and_remeasure_depth" if genuinely_thin and bad_reading_pools
        else None)
    return {
        "valid": valid,
        "code": classification,
        "classification": classification,
        "repairPath": repair_path,
        # Two different units, on purpose:
        # * ``badReadingCount`` counts the **snapshot rows** whose TVL is
        #   outside the band (the historical number, kept for continuity);
        # * ``badReadingPoolCount`` counts the distinct **pools** that carry
        #   at least one such row. A pool can hold several snapshots, so
        #   the pool count is the one that decides how much has to be
        #   re-measured.
        "badReadingCount": bad_readings,
        "badReadingPoolCount": len(bad_reading_pools),
        # The stale/malformed USD valuation is reported separately. It is a
        # reference-only observation and never blocks the layer, because the
        # shared depth policy forbids a USD band from taking part in the
        # admission decision.
        "staleTvlReadingCount": bad_readings,
        "staleTvlReadingPoolCount": len(stale_reading_pools),
        "unmeasuredPoolCount": len(unmeasured_pools),
        "thinPoolIdCount": len(genuinely_thin),
        "adequatePoolIdCount": len(adequate_pools),
        # The pools whose admission capacity is unknown to the kernel.
        # A non-zero count means the store cannot carry the measurement, so
        # the kernel has no admission input for those pools.
        "capacityUnknownPoolCount": len(capacity_unknown_pools),
        "capacityColumnMissing": capacity_column_missing,
        "candidatePoolIdCount": len(bad_reading_pools | genuinely_thin | adequate_pools),
        "minTvlUsd": MIN_POOL_TVL_USD,
        "maxTvlUsd": MAX_POOL_TVL_USD,
        "candidatePoolCount": total,
        "uniquePoolCount": total,
        "candidatePoolRows": sum(int(row.get("row_count") or 0) for row in rows),
        "badReadingCount": bad_readings,
        "adequateDepthPoolCount": healthy,
        "insufficientDepthPoolCount": insufficient,
        "thinPoolCount": sum(int(row.get("pool_count") or 0) for row in rows
                             if str(row.get("depth_status") or "") in ("thin", "shallow", "dust")),
        "unpricedPoolCount": sum(int(row.get("pool_count") or 0) for row in rows
                                 if str(row.get("depth_status") or "") == "unpriced"),
        "depthCriterion": "pool_manager_capacity_verdict",
        "adequateDepthStatuses": sorted(ADEQUATE_DEPTH_STATUSES),
        "reason": ("candidate pools whose capacity verdict is below the adequate band can "
                   "only produce dust-sized best trade amounts whose profit never covers gas"),
        "byDepthStatus": [{"depthStatus": row.get("depth_status"),
                           "poolCount": int(row.get("pool_count") or 0),
                           "adequatePoolCount": int(row.get("adequate_pool_count") or 0),
                           "badReadingCount": int(row.get("bad_reading_count") or 0),
                           "minTvlUsd": row.get("min_tvl_usd"),
                           "maxTvlUsd": row.get("max_tvl_usd"),
                           "avgTvlUsd": row.get("avg_tvl_usd")} for row in rows],
    }


def audit_loops(db: Path, manager_api: str | None = None) -> dict[str, Any]:
    """Audit the closed-loop layer through the manager API.

    The **closed-loop Manager** owns the closed-loop catalog, its hop rows
    and its runtime states, so the healer reads them from the manager API
    instead of selecting from the manager's tables.
    """
    published = manager_read_closed_loops(manager_api)
    loops = published.get("loops") or []
    hops = published.get("hops") or []
    runtime_states = published.get("runtimeStates") or []
    runtime_counts: dict[str, int] = {}
    for row in runtime_states:
        state = str(row.get("runtimeStatus") or "").strip()
        if state:
            runtime_counts[state] = runtime_counts.get(state, 0) + 1
    runtime = [{"state": state, "count": count}
               for state, count in sorted(runtime_counts.items(), key=lambda item: -item[1])]
    status = published.get("status") or {}
    loop_count = int(status.get("loopCount") or len(loops))
    consumers_need_rebuild = int(status.get("consumersNeedRebuild") or 0)
    valid = bool(loop_count > 0 and consumers_need_rebuild == 0)
    return {
        "valid": valid,
        "library": {
            "loop_count": loop_count,
            "hop_count": len(hops),
            "consumers_need_rebuild": consumers_need_rebuild,
            "materialized_checksum": status.get("materializedChecksum"),
            "generated_at": status.get("generatedAt"),
        },
        "runtime": runtime,
    }


ERC20_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def token_decimals(db: Path, address: str) -> int | None:
    normalized = str(address or "").strip().lower()
    if not normalized or normalized == ZERO_ADDRESS:
        return None
    for table in ("token_core", "token_stocks", "token_test"):
        row = one(db, f"SELECT token_decimals FROM {table} WHERE network=? AND chain_id=? "
            "AND lower(token_address)=? LIMIT 1", (NETWORK, CHAIN_ID, normalized))
        if row and row.get("token_decimals") is not None:
            try:
                return int(row["token_decimals"])
            except (TypeError, ValueError):
                return None
    return None


def receipt_realized_token_delta(
    db: Path,
    summary: dict[str, Any],
    symbol: str,
) -> tuple[float | None, str | None]:
    route = summary.get("route_addresses") or summary.get("routeAddresses") or []
    if not isinstance(route, list) or not route:
        return None, "route_addresses_missing"
    token = str(route[0] or "").strip().lower()
    if token == ZERO_ADDRESS:
        return None, "native_balance_delta_not_materialized"
    result = ((summary.get("auto_execute") or {}).get("result") or {})
    receipt = ((result.get("receipt") or {}).get("receipt") or {})
    executor = str(receipt.get("to") or "").strip().lower()
    logs = receipt.get("logs") or []
    if not executor or not isinstance(logs, list):
        return None, "receipt_logs_missing"
    net_raw = 0
    observed = False
    for log in logs:
        topics = log.get("topics") if isinstance(log, dict) else None
        if not isinstance(topics, list) or len(topics) < 3:
            continue
        if str(topics[0]).strip().lower() != ERC20_TRANSFER_TOPIC:
            continue
        if str(log.get("address") or "").strip().lower() != token:
            continue
        from_address = "0x" + str(topics[1])[-40:].lower()
        to_address = "0x" + str(topics[2])[-40:].lower()
        try:
            amount_raw = int(str(log.get("data") or "0x0"), 16)
        except (TypeError, ValueError):
            continue
        if to_address == executor:
            net_raw += amount_raw
            observed = True
        if from_address == executor:
            net_raw -= amount_raw
            observed = True
    if not observed:
        return None, "borrow_token_transfer_delta_missing"
    decimals = token_decimals(db, token)
    if decimals is None:
        return None, "borrow_token_decimals_missing"
    return net_raw / (10 ** decimals), None


def audit_reviews(
    db: Path,
    hours: int,
    manager_api: str | None = None,
) -> dict[str, Any]:
    rows, reasons, classifications = review_rows(db, hours, None, manager_api), {}, {}
    # The closed-loop Manager owns the catalog, so the known loops are the
    # ones it published.
    known_loops = {
        str(row.get("loopKey") or "")
        for row in manager_read_closed_loops(manager_api).get("loops") or []
        if str(row.get("loopKey") or "")
    }
    matched = [row for row in rows if review_loop_key(row) in known_loops]
    unmatched = [row for row in rows if review_loop_key(row) not in known_loops]
    best_amount_failures = []
    best_amount_failure_reasons: dict[str, int] = {}
    ladder_incomplete_count = 0
    ladder_incomplete_current = 0
    for row in rows:
        amount_state = best_amount_state(row)
        if amount_state["missing"]:
            reason = best_amount_failure_reason(amount_state)
            best_amount_failure_reasons[reason] = best_amount_failure_reasons.get(reason, 0) + 1
            if amount_state.get("ladderIncomplete"):
                ladder_incomplete_count += 1
                if review_loop_key(row) in known_loops:
                    ladder_incomplete_current += 1
            best_amount_failures.append({
                "reviewId": row.get("id"),
                "loopKey": review_loop_key(row),
                "currentLoop": review_loop_key(row) in known_loops,
                "reason": reason,
                "status": amount_state["status"],
                "reviewFailureKind": amount_state["reviewFailureKind"],
                "initialProbeAmount": amount_state["initialProbeAmount"],
                "requestedSymbol": amount_state["requestedSymbol"],
                "ladderIncomplete": bool(amount_state.get("ladderIncomplete")),
                "probeCount": amount_state.get("probeCount"),
            })
        if pnl(row) is None:
            # The legacy library records the reason in
            # ``selected_profit_missing_reason``; the Manager library records
            # it in ``review_status``/``review_reason``. Reading only the
            # legacy column made every Manager review look like a generic
            # "evidence_missing", which hid the actual upstream failure.
            reason = review_missing_reason(row) or "evidence_missing"
            reason = str(reason)[:200]
            reasons[reason] = reasons.get(reason, 0) + 1
            code, retryable = missing_pnl_classification(reason)
            key = (code, retryable, review_loop_key(row) in known_loops)
            entry = classifications.setdefault(key, {"code": code, "retryable": retryable,
                "currentLoop": key[2], "count": 0, "sampleReviewIds": []})
            entry["count"] += 1
            if len(entry["sampleReviewIds"]) < 5:
                entry["sampleReviewIds"].append(row.get("id"))
    stats = pnl_stats(rows)
    best_amount_missing_count = len(best_amount_failures)
    best_amount_missing_current = sum(1 for item in best_amount_failures if item["currentLoop"])
    # The review layer judges the reviews of the **current** closed-loop
    # library.  A review of a loop that no longer exists describes a route
    # the system already stopped trading: it is history, not a live defect,
    # and the operating manual says so explicitly ("stale/unmatched and
    # historical duplicates of one loop are only counted, never written into
    # the current Review Manager materialization").
    #
    # The validity verdict used to be taken over the whole window, so a
    # stale row from an earlier epoch kept the layer red forever.  The
    # healer then chased a review tool for reviews that no longer belonged
    # to any live loop, and the layer could never recover even though every
    # current loop carried a complete, valued review.
    #
    # The counted numbers stay over the whole window, so the report still
    # shows how much history the store holds; only the *verdict* is scoped
    # to the loops that are live right now.
    current_rows = matched
    current_stats = pnl_stats(current_rows)
    current_missing_pnl = sum(1 for row in current_rows if pnl(row) is None)
    current_best_amount_missing = best_amount_missing_current
    current_best_amount_confirmed = len(current_rows) - current_best_amount_missing
    manager_rows = manager_review_rows(db, None, manager_api)
    return {"valid": bool(manager_rows) and current_missing_pnl == 0
            and current_best_amount_missing == 0,
        "manager": {"reviews": len(manager_rows)},
        "legacy": {"windowHours": hours, "stats": stats,
                   "latestReviewAt": rows[0].get("created_at") if rows else None,
                   "currentLoopMatched": len(matched), "staleOrUnmatched": len(unmatched),
                   "currentDistinctLoops": len({review_loop_key(row) for row in matched}),
                   "matchedMissingPnl": sum(pnl(row) is None for row in matched),
                   "unmatchedMissingPnl": sum(pnl(row) is None for row in unmatched),
                   "bestAmountMissing": best_amount_missing_count,
                   "bestAmountMissingCurrentLoop": best_amount_missing_current,
                   "bestAmountLadderIncomplete": ladder_incomplete_count,
                   "bestAmountLadderIncompleteCurrentLoop": ladder_incomplete_current,
                   "bestAmountConfirmed": len(rows) - best_amount_missing_count,
                   "currentLoopStats": {
                       "total": len(current_rows),
                       "valued": len(current_rows) - current_missing_pnl,
                       "missingValue": current_missing_pnl,
                       "bestAmountConfirmed": current_best_amount_confirmed,
                       "bestAmountMissing": current_best_amount_missing,
                       "positive": current_stats.get("positive"),
                       "positiveAmount": current_stats.get("positiveAmount"),
                       "nonPositive": current_stats.get("nonPositive"),
                       "sum": current_stats.get("sum"),
                   }},
        "bestAmount": {
            "code": BEST_AMOUNT_MISSING_CODE if best_amount_missing_current else None,
            "missing": best_amount_missing_count,
            "missingCurrentLoop": best_amount_missing_current,
            "ladderIncomplete": ladder_incomplete_count,
            "ladderIncompleteCurrentLoop": ladder_incomplete_current,
            "total": len(rows),
            "confirmed": len(rows) - best_amount_missing_count,
            "currentLoopTotal": len(current_rows),
            "currentLoopConfirmed": current_best_amount_confirmed,
            "retryable": False,
            "failClosed": True,
            "reason": "every completed review must carry a concrete best trade amount; "
                      "a missing amount is an upstream review failure and stops execution",
            "reasons": [{"reason": key, "count": value} for key, value in
                        sorted(best_amount_failure_reasons.items(),
                               key=lambda item: item[1], reverse=True)[:10]],
            "samples": best_amount_failures[:10],
        },
        "missingReasons": [{"reason": key, "count": value}
                           for key, value in sorted(reasons.items(), key=lambda item: item[1], reverse=True)[:10]],
        "missingClassifications": sorted(classifications.values(),
                                         key=lambda item: item["count"], reverse=True)}


def audit_broadcast(
    db: Path,
    hours: int,
    manager_api: str | None = None,
) -> dict[str, Any]:
    """Return the broadcast record of the chain.

    The **Broadcast Manager** owns the broadcast attempts and publishes
    them.  The attempt rows are therefore read from the manager API, and
    the manager's published status is the authority for the attempt count.

    The legacy ``cow_execution_attempts`` table is a **dynamic runtime
    table** that no Rust manager owns: it carries the runtime's own
    execution summary, including the gas cost and the receipt of the
    transaction it sent.  It is read for that enrichment only, and it is
    never the source of the attempt count.
    """
    broadcast = manager_read_broadcasts(manager_api)
    broadcast_status = broadcast.get("status") or {}
    published_attempts = broadcast.get("attempts") or []
    legacy_attempts = all_rows(db, "SELECT id,attempt_key,category,tx_hash,created_at,selected_profit_amount,"
        "selected_profit_amount_symbol,summary_json FROM cow_execution_attempts WHERE network=? AND chain_id=? "
        "AND datetime(created_at)>=datetime(?) ORDER BY created_at DESC",
        (NETWORK, CHAIN_ID, observation_started_at()))
    # The published attempt is joined with the legacy execution summary by
    # its attempt key.  A published attempt without a legacy summary keeps
    # its own published facts and stays **missing** for the enrichment that
    # only the legacy row holds.
    legacy_by_key = {
        str(row.get("attempt_key") or ""): row for row in legacy_attempts
    }
    attempts: list[dict[str, Any]] = []
    for published in published_attempts:
        attempt_key = str(published.get("attemptId") or published.get("attempt_id") or "")
        legacy = legacy_by_key.get(attempt_key, {})
        signed_pnl = published.get("signedPnl")
        if not isinstance(signed_pnl, dict):
            signed_pnl = {}
        attempts.append({
            "id": published.get("attemptId"),
            "attempt_key": attempt_key,
            "category": str(published.get("status") or published.get("statusReason") or "unknown"),
            "tx_hash": published.get("txHash"),
            "created_at": published.get("createdAt"),
            "selected_profit_amount": signed_pnl.get("amount"),
            "selected_profit_amount_symbol": signed_pnl.get("currency"),
            "summary_json": json.dumps(legacy.get("summary_json") or "{}"),
            "published_by_manager": True,
        })
    manager_attempts = int(broadcast_status.get("attemptCount") or len(published_attempts))
    categories = {}
    expected_by_currency: dict[str, float] = {}
    gas_cost_eth_total = 0.0
    gas_cost_observed = 0
    eth_net_after_gas = 0.0
    eth_net_observed = 0
    realized_by_currency: dict[str, float] = {}
    realized_net_after_gas_by_currency: dict[str, float] = {}
    realized_observed = 0
    realized_missing: dict[str, int] = {}
    outcome_counts = {"success": 0, "failed": 0, "pending": 0, "other": 0}
    for row in attempts:
        key = str(row.get("category") or "unknown")
        categories[key] = categories.get(key, 0) + 1
        normalized = key.strip().lower()
        if "success" in normalized:
            outcome_counts["success"] += 1
        elif any(marker in normalized for marker in ("failed", "failure", "revert")):
            outcome_counts["failed"] += 1
        elif any(marker in normalized for marker in ("pending", "submitted", "broadcasting")):
            outcome_counts["pending"] += 1
        else:
            outcome_counts["other"] += 1
        try:
            expected = float(row.get("selected_profit_amount"))
        except (TypeError, ValueError):
            expected = None
        currency = str(row.get("selected_profit_amount_symbol") or "unknown").upper()
        if expected is not None:
            expected_by_currency[currency] = expected_by_currency.get(currency, 0.0) + expected
        try:
            summary = json.loads(row.get("summary_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            summary = {}
        # The stored summary is a JSON **document**, so it decodes to any
        # JSON value.  Only an object carries the blocks the audit reads; a
        # decoded string or list is a summary of another shape and stays
        # empty instead of aborting the whole audit.
        if not isinstance(summary, dict):
            summary = {}
        auto_execute = summary.get("auto_execute") if isinstance(summary.get("auto_execute"), dict) else {}
        result = auto_execute.get("result") if isinstance(auto_execute.get("result"), dict) else {}
        try:
            gas_cost_eth = float(result.get("gasCostEth"))
        except (TypeError, ValueError):
            gas_cost_eth = None
        if gas_cost_eth is not None:
            gas_cost_eth_total += gas_cost_eth
            gas_cost_observed += 1
            if expected is not None and currency in {"ETH", "WETH"}:
                eth_net_after_gas += expected - gas_cost_eth
                eth_net_observed += 1
        realized_amount, realized_missing_reason = receipt_realized_token_delta(db, summary, currency)
        if realized_amount is not None:
            realized_by_currency[currency] = realized_by_currency.get(currency, 0.0) + realized_amount
            realized_observed += 1
            if gas_cost_eth is not None and currency in {"ETH", "WETH"}:
                realized_net_after_gas_by_currency[currency] = (
                    realized_net_after_gas_by_currency.get(currency, 0.0)
                    + realized_amount - gas_cost_eth
                )
        else:
            missing_reason = realized_missing_reason or "realized_pnl_unavailable"
            realized_missing[missing_reason] = realized_missing.get(missing_reason, 0) + 1
    positive = one(db, "SELECT COUNT(*) n FROM cow_execution_reviews WHERE network=? AND chain_id=? "
        "AND created_at>=datetime('now',?) AND datetime(created_at)>=datetime(?) "
        "AND category='executable' AND selected_profit_amount IS NOT NULL "
        "AND selected_profit_amount!='' AND CAST(selected_profit_amount AS REAL)>0",
        (NETWORK, CHAIN_ID, f"-{hours} hours", observation_started_at())) or {"n": 0}
    return {"valid": manager_attempts > 0,
        "manager": {"attempts": manager_attempts,
                    "pending": int(broadcast_status.get("pendingCount") or 0),
                    "success": int(broadcast_status.get("successCount") or 0),
                    "failed": int(broadcast_status.get("failedCount") or 0),
                    "diagnostics": len(broadcast.get("diagnostics") or []),
                    "source": "broadcast_manager_published_records"},
        "legacy": {"total": len(attempts), "categories": categories,
                   "latestAttemptAt": attempts[0].get("created_at") if attempts else None,
                   "recent": attempts[:10],
                   "legacyRuntimeRows": len(legacy_attempts),
                   "source": "manager_published_attempts_joined_with_legacy_execution_summary"},
        "profitAnalysis": {
            "expectedByCurrency": expected_by_currency,
            "gasCostEthTotal": gas_cost_eth_total,
            "gasCostObservedAttempts": gas_cost_observed,
            "expectedEthOrWethNetAfterGas": eth_net_after_gas,
            "expectedEthOrWethNetObservedAttempts": eth_net_observed,
            "realizedByCurrency": realized_by_currency,
            "realizedNetAfterGasByCurrency": realized_net_after_gas_by_currency,
            "realizedPnlObservedAttempts": realized_observed,
            "realizedPnlMissingAttempts": len(attempts) - realized_observed,
            "realizedPnlMissingReasons": realized_missing,
            "realizedPnlAvailable": realized_observed > 0,
            "source": "contract_preview_plus_confirmed_receipt_gas",
        },
        "outcomes": outcome_counts,
        "historicalPositiveReviewsInWindow": int(positive["n"]),
        "automaticBroadcastCandidates": 0,
        "automaticBroadcastPolicy": "fresh_positive_review_only"}


def transaction_production_status(
    layers: dict[str, Any],
    generated_at: str,
    freshness_seconds: int = 600,
) -> dict[str, Any]:
    reviews = ((layers.get("reviews") or {}).get("legacy") or {})
    best_amount = ((layers.get("reviews") or {}).get("bestAmount") or {})
    broadcast = ((layers.get("broadcast") or {}).get("legacy") or {})
    stats = reviews.get("stats") or {}
    latest_review_at = reviews.get("latestReviewAt")
    review_age_seconds = None
    if latest_review_at:
        try:
            review_time = dt.datetime.fromisoformat(str(latest_review_at).replace("Z", "+00:00"))
            generated_time = dt.datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
            review_age_seconds = max(0.0, (generated_time - review_time).total_seconds())
        except (TypeError, ValueError):
            review_age_seconds = None
    review_fresh = review_age_seconds is not None and review_age_seconds <= freshness_seconds
    upstream_valid = all((layers.get(name) or {}).get("valid") for name in ("tokens", "pools", "closedLoops"))
    executable_positive = int(stats.get("positive") or 0)
    attempts = int(broadcast.get("total") or 0)
    best_amount_missing = int(best_amount.get("missing") or 0)
    if not upstream_valid:
        state, healthy, reason = "upstream_invalid", False, "token_pool_or_closed_loop_invalid"
    elif best_amount_missing > 0:
        state, healthy, reason = "review_best_amount_missing", False, "review_without_concrete_best_trade_amount"
    elif not review_fresh:
        state, healthy, reason = "review_stalled", False, "no_fresh_review_within_threshold"
    elif executable_positive <= 0:
        state, healthy, reason = "market_no_executable_profit", True, "fresh_reviews_have_no_executable_positive_pnl"
    elif attempts <= 0:
        state, healthy, reason = "profitable_review_waiting_for_broadcast", False, "positive_review_exists_without_transaction_record"
    else:
        state, healthy, reason = "transaction_records_present", True, "profitable_reviews_and_transaction_records_exist"
    return {
        "state": state,
        "healthy": healthy,
        "reason": reason,
        "latestReviewAt": latest_review_at,
        "reviewAgeSeconds": review_age_seconds,
        "reviewFreshnessThresholdSeconds": freshness_seconds,
        "executablePositiveReviews": executable_positive,
        "transactionRecords": attempts,
        "latestAttemptAt": broadcast.get("latestAttemptAt"),
        "bestAmountMissing": best_amount_missing,
    }


#: The stable-achievement criterion (TASK-130 / AC-200).
#:
#: "The mainnet stably produces arbitrage broadcast transactions" has to be
#: a *decidable* statement, not a feeling. The plan fixes the shape:
#:
#:     consecutive M windows, each window >= N minutes:
#:         positive_reviews > 0
#:         broadcast_success > 0
#:         blockers == []
#:         missingValue == 0
#:         productionState == healthy
#:
#: A window that fails any condition resets the counter to zero. The
#: windows must also be **contiguous in time**: three samples taken in the
#: same minute do not prove stability over ninety minutes.
STABILITY_REQUIRED_WINDOWS = 3
STABILITY_MIN_WINDOW_SECONDS = 30 * 60


def stability_window(report: dict[str, Any]) -> dict[str, Any]:
    """Evaluate whether one observation window satisfies the criterion."""
    layers = report.get("layers") or {}
    reviews = layers.get("reviews") or {}
    stats = (reviews.get("legacy") or {}).get("stats") or {}
    broadcast = layers.get("broadcast") or {}
    outcomes = broadcast.get("outcomes") or {}
    production = report.get("transactionProduction") or {}
    blockers = list(report.get("blockers") or [])
    conditions = {
        "positiveReviews": int(stats.get("positive") or 0) > 0,
        "broadcastSuccess": int(outcomes.get("success") or 0) > 0,
        "noBlockers": not blockers,
        "noMissingReviewValue": int(stats.get("missingValue") or 0) == 0,
        "productionHealthy": str(production.get("state") or "") == "healthy",
    }
    satisfied = all(conditions.values())
    return {
        "satisfied": satisfied,
        "conditions": conditions,
        "failedConditions": sorted(name for name, value in conditions.items()
                                   if not value),
        "positiveReviews": int(stats.get("positive") or 0),
        "broadcastSuccess": int(outcomes.get("success") or 0),
        "missingReviewValue": int(stats.get("missingValue") or 0),
        "blockers": blockers,
        "productionState": str(production.get("state") or ""),
        "observedAt": report.get("generatedAt"),
    }


class StabilityTracker:
    """Track the consecutive healthy observation windows.

    The tracker is the single place that decides whether the chain may be
    declared stable. It is deliberately stateful across samples, because
    the criterion is about **consecutive** windows: one healthy sample is
    not stability, and a broken window resets the run.
    """

    def __init__(self, required_windows: int = STABILITY_REQUIRED_WINDOWS,
                 min_window_seconds: int = STABILITY_MIN_WINDOW_SECONDS):
        self.required_windows = max(1, int(required_windows))
        self.min_window_seconds = max(0, int(min_window_seconds))
        self.consecutive = 0
        self.last_observed_at: dt.datetime | None = None
        self.history: list[dict[str, Any]] = []

    @staticmethod
    def _parse(value: Any) -> dt.datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)

    def observe(self, report: dict[str, Any]) -> dict[str, Any]:
        """Fold one report into the tracker and return the current verdict."""
        window = stability_window(report)
        observed = self._parse(window.get("observedAt"))
        gap_seconds: float | None = None
        contiguous = True
        if observed is not None and self.last_observed_at is not None:
            gap_seconds = (observed - self.last_observed_at).total_seconds()
            # A negative gap means the clock moved backwards, which is not
            # a continuation of the observation run.
            if gap_seconds < 0:
                contiguous = False
        if not window["satisfied"] or not contiguous:
            self.consecutive = 0
        else:
            self.consecutive += 1
        if observed is not None:
            self.last_observed_at = observed
        elapsed = (self.consecutive * self.min_window_seconds)
        achieved = self.consecutive >= self.required_windows
        entry = {
            "observedAt": window.get("observedAt"),
            "satisfied": window["satisfied"],
            "failedConditions": window["failedConditions"],
            "consecutiveWindows": self.consecutive,
            "gapSeconds": gap_seconds,
            "contiguous": contiguous,
            "requiredWindows": self.required_windows,
            "minWindowSeconds": self.min_window_seconds,
            "observedSpanSeconds": elapsed,
            "achieved": achieved,
        }
        self.history.append(entry)
        return {
            **entry,
            "window": window,
            "stable": achieved,
            "reason": (
                "the chain produced arbitrage broadcast transactions in "
                f"{self.consecutive} consecutive windows of at least "
                f"{self.min_window_seconds // 60} minutes each"
                if achieved else
                "the stable-achievement criterion is not met yet: "
                + (", ".join(window["failedConditions"]) or "the observation "
                   "windows are not contiguous")
            ),
        }


def build_report(
    db: Path,
    hours: int,
    tools: Path | None = None,
    manager_api: str | None = None,
) -> dict[str, Any]:
    # Every verdict in this report describes exactly one chain, so the
    # report may only be built from that chain's own database file.
    database = assert_chain_database(db)
    layers = {"tokens": audit_tokens(database),
              "pools": audit_pools(database, hours, tools, manager_api),
              "closedLoops": audit_loops(database, manager_api),
              "reviews": audit_reviews(database, hours, manager_api),
              "broadcast": audit_broadcast(database, hours, manager_api)}
    # The full-range matrix is a **read** of the same store, never a second
    # source of truth: it counts the funding tokens, the candidate tokens,
    # the candidate pools and the closed loops in one place, so an empty
    # set can never be mistaken for a skipped check.
    matrix = cascade_matrix(database, manager_api)
    layers["cascadeMatrix"] = matrix
    blockers = [name for name, layer in layers.items()
                if name != "cascadeMatrix" and not layer.get("valid")]
    generated_at = now()
    production = transaction_production_status(layers, generated_at)
    return {"mode": "check", "generatedAt": generated_at, "network": NETWORK, "chainId": CHAIN_ID,
            "database": str(database), "observationStartedAt": observation_started_at(),
            "orchestrationGoal": "continuously_produce_stable_arbitrage_transaction_records_and_analyze_pnl",
            "successCriteria": {
                "continuousFreshReviews": True,
                "broadcastOnlyFromFreshProfitableReview": True,
                "terminalTransactionRecords": True,
                "expectedAndGasAdjustedPnlAvailable": True,
            },
            "layers": layers, "blockers": blockers, "transactionProduction": production,
            "safeToAutoBroadcast": not blockers}


def print_report(report: dict[str, Any]) -> None:
    layers, reviews, broadcast = report["layers"], report["layers"]["reviews"], report["layers"]["broadcast"]
    stats = reviews["legacy"]["stats"]
    best_amount = reviews.get("bestAmount") or {}
    # The header has to name the chain the report actually describes. A
    # testnet report that says "mainnet" is a report an operator can
    # misread, and the orchestrator is chain-agnostic: the chain comes
    # from the active scope, not from the tool's name.
    print(f"=== {report.get('network')} (chain {report.get('chainId')}) healer "
          f"{report['generatedAt']} ===")
    print("L1 tokens    valid=%s admitted=%s missing=%s" %
          (layers["tokens"]["valid"], layers["tokens"]["admittedTotal"], layers["tokens"]["libraryMissingFields"]))
    print("L2 pools     valid=%s eligible=%s observation=%s" %
          (layers["pools"]["valid"], (layers["pools"].get("candidate") or {}).get("eligible_pool_count"),
           (layers["pools"].get("observation") or {}).get("pool_count")))
    pool_depth = layers["pools"].get("depth") or {}
    pool_best_amount = layers["pools"].get("bestAmount") or {}
    print("     poolDepth valid=%s uniquePools=%s candidateRows=%s adequate=%s insufficient=%s "
          "thin=%s unpriced=%s badReadingRows=%s badReadingPools=%s unmeasuredPools=%s "
          "minTvlUsd=%s maxTvlUsd=%s code=%s" %
          (pool_depth.get("valid"), pool_depth.get("uniquePoolCount"),
           pool_depth.get("candidatePoolRows"), pool_depth.get("adequateDepthPoolCount"),
           pool_depth.get("insufficientDepthPoolCount"), pool_depth.get("thinPoolCount"),
           pool_depth.get("unpricedPoolCount"), pool_depth.get("badReadingCount"),
           pool_depth.get("badReadingPoolCount"), pool_depth.get("unmeasuredPoolCount"),
           pool_depth.get("minTvlUsd"), pool_depth.get("maxTvlUsd"),
           pool_depth.get("code")))
    print("     poolDepth repairPath=%s classification=%s" %
          (pool_depth.get("repairPath"), pool_depth.get("classification")))
    print("     poolBestAmount valid=%s confirmed=%s belowGas=%s tooSmall=%s insufficient=%s "
          "gasEth=%s code=%s" %
          (pool_best_amount.get("valid"), pool_best_amount.get("confirmedCount"),
           pool_best_amount.get("belowGasCount"), pool_best_amount.get("tooSmallCount"),
           pool_best_amount.get("insufficientCount"),
           pool_best_amount.get("gasEstimateEth"), pool_best_amount.get("code")))
    print("L3 loops     valid=%s loops=%s rebuild=%s" %
          (layers["closedLoops"]["valid"], (layers["closedLoops"].get("library") or {}).get("loop_count"),
           (layers["closedLoops"].get("library") or {}).get("consumers_need_rebuild")))
    print("L4 reviews   valid=%s total=%s valued=%s missing=%s positive=%s positiveAmount=%s "
          "nonPositive=%s sum=%s" %
          (reviews["valid"], stats["total"], stats["withValue"], stats["missingValue"],
           stats["positive"], stats.get("positiveAmount"), stats.get("nonPositive"), stats["sum"]))
    print("     pnl positiveEstimateOnly=%s estimatedProfitOnlyCount=%s confirmedSum=%s" %
          (stats.get("positiveEstimateOnly"), stats.get("estimatedProfitOnlyCount"),
           stats.get("confirmedSum")))
    print("     bestAmount total=%s confirmed=%s missing=%s ladderIncomplete=%s code=%s failClosed=%s" %
          (best_amount.get("total"), best_amount.get("confirmed"),
           best_amount.get("missing"), best_amount.get("ladderIncomplete"),
           best_amount.get("code"), best_amount.get("failClosed")))
    current = (reviews.get("legacy") or {}).get("currentLoopStats") or {}
    print("     currentLoops total=%s valued=%s missingPnl=%s bestAmountConfirmed=%s bestAmountMissing=%s "
          "positive=%s sum=%s" %
          (current.get("total"), current.get("valued"), current.get("missingValue"),
           current.get("bestAmountConfirmed"), current.get("bestAmountMissing"),
           current.get("positive"), current.get("sum")))
    print("L5 broadcast valid=%s manager=%s epochLegacy=%s historicalPositiveInEpoch=%s" %
          (broadcast["valid"], broadcast["manager"]["attempts"], broadcast["legacy"]["total"],
           broadcast["historicalPositiveReviewsInWindow"]))
    pnl = broadcast.get("profitAnalysis") or {}
    print("PnL expected=%s gasEth=%s ethNetAfterGas=%s" %
          (pnl.get("expectedByCurrency"), pnl.get("gasCostEthTotal"),
           pnl.get("expectedEthOrWethNetAfterGas")))
    print("outcomes=%s realizedPnlAvailable=%s" %
          (broadcast.get("outcomes"), pnl.get("realizedPnlAvailable")))
    production = report.get("transactionProduction") or {}
    print("productionState=%s healthy=%s reason=%s reviewAgeSeconds=%s" %
          (production.get("state"), production.get("healthy"), production.get("reason"),
           production.get("reviewAgeSeconds")))
    matrix = report["layers"].get("cascadeMatrix") or {}
    funding = matrix.get("fundingTokens") or {}
    candidate_tokens = matrix.get("candidateTokens") or {}
    pools = matrix.get("candidatePools") or {}
    loops = matrix.get("closedLoops") or {}
    print("MATRIX funding valid=%s/%s counterfeit=%s | candidateTokens admitted=%s "
          "symbols=%s | pools distinct=%s | loops total=%s threeHops=%s hopsInScope=%s/%s" %
          (funding.get("valid"), funding.get("expected"),
           funding.get("counterfeitAdmittedCount"),
           candidate_tokens.get("admitted"), candidate_tokens.get("symbolCount"),
           pools.get("distinctPoolCount"), loops.get("total"), loops.get("exactThreeHops"),
           loops.get("hopsInCandidateScope"), loops.get("hopsTotal")))
    print("blockers=" + (",".join(report["blockers"]) or "none"))
    print("safeToAutoBroadcast=" + str(report["safeToAutoBroadcast"]))


def pnl_snapshot(
    report: dict[str, Any],
    session_started_at: str,
    elapsed_seconds: float,
    observation_hours: float,
    sample: int,
) -> dict[str, Any]:
    """Build a read-only PnL snapshot without inventing realized values."""
    reviews = ((report.get("layers") or {}).get("reviews") or {}).get("legacy") or {}
    best_amount = ((report.get("layers") or {}).get("reviews") or {}).get("bestAmount") or {}
    broadcast = ((report.get("layers") or {}).get("broadcast") or {})
    legacy = broadcast.get("legacy") or {}
    profit = broadcast.get("profitAnalysis") or {}
    stats = reviews.get("stats") or {}
    signed_by_currency = stats.get("byCurrency") or {}
    realized_missing = profit.get("realizedPnlMissingReasons") or {}
    remaining = None
    if observation_hours and observation_hours > 0:
        remaining = max(0.0, observation_hours * 3600.0 - elapsed_seconds)
    return {
        "event": "pnl_snapshot",
        "generatedAt": report.get("generatedAt") or now(),
        "sessionStartedAt": session_started_at,
        "observationStartedAt": report.get("observationStartedAt"),
        "sample": sample,
        "elapsedSeconds": round(elapsed_seconds, 3),
        "observationHours": observation_hours,
        "observationRemainingSeconds": None if remaining is None else round(remaining, 3),
        "production": {
            "state": (report.get("transactionProduction") or {}).get("state"),
            "healthy": (report.get("transactionProduction") or {}).get("healthy"),
            "reason": (report.get("transactionProduction") or {}).get("reason"),
            "latestReviewAt": reviews.get("latestReviewAt"),
            "latestAttemptAt": legacy.get("latestAttemptAt"),
            "blockers": report.get("blockers") or [],
        },
        "reviews": {
            "total": stats.get("total", 0),
            "withValue": stats.get("withValue", 0),
            "missingValue": stats.get("missingValue", 0),
            "executablePositive": stats.get("positive", 0),
            "positiveAmount": stats.get("positiveAmount", 0),
            "signedPnlByCurrency": signed_by_currency,
            "signedPnlSum": stats.get("sum", 0.0),
            "currentLoopMatched": reviews.get("currentLoopMatched", 0),
            "matchedMissingPnl": reviews.get("matchedMissingPnl", 0),
            "bestAmountMissing": best_amount.get("missing", 0),
            "bestAmountConfirmed": best_amount.get("confirmed", 0),
        },
        "broadcast": {
            "attempts": legacy.get("total", 0),
            "outcomes": broadcast.get("outcomes") or {},
            "categories": legacy.get("categories") or {},
            "latestAttemptAt": legacy.get("latestAttemptAt"),
            "automaticBroadcastPolicy": broadcast.get("automaticBroadcastPolicy"),
            "historicalPositiveReviewsInWindow": broadcast.get("historicalPositiveReviewsInWindow", 0),
        },
        "expectedPnl": {
            "byCurrency": profit.get("expectedByCurrency") or {},
            "gasCostEthTotal": profit.get("gasCostEthTotal", 0.0),
            "gasCostObservedAttempts": profit.get("gasCostObservedAttempts", 0),
            "ethOrWethNetAfterGas": profit.get("expectedEthOrWethNetAfterGas", 0.0),
            "ethOrWethNetObservedAttempts": profit.get("expectedEthOrWethNetObservedAttempts", 0),
            "source": "selected_profit_amount_and_receipt_gas",
        },
        "realizedPnl": {
            "byCurrency": profit.get("realizedByCurrency") or {},
            "netAfterGasByCurrency": profit.get("realizedNetAfterGasByCurrency") or {},
            "observedAttempts": profit.get("realizedPnlObservedAttempts", 0),
            "missingAttempts": profit.get("realizedPnlMissingAttempts", len(legacy.get("recent") or []) if False else 0),
            "missingReasons": realized_missing,
            "available": bool(profit.get("realizedPnlAvailable")),
            "source": "confirmed_receipt_transfer_delta",
        },
        "notes": [
            "expectedPnl is contract-preview based and is not realized profit",
            "realizedPnl is derived from confirmed receipt transfers only",
            "missing values are reported as missing and never replaced with zero",
            "a review without a concrete best trade amount is an upstream review failure and is fail-closed",
        ],
    }


def append_pnl_snapshot(
    snapshot: dict[str, Any],
    pnl_jsonl: Path,
    pnl_log: Path,
) -> None:
    with pnl_jsonl.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False, default=str) + "\n")
    expected = snapshot.get("expectedPnl") or {}
    realized = snapshot.get("realizedPnl") or {}
    reviews = snapshot.get("reviews") or {}
    broadcast = snapshot.get("broadcast") or {}
    outcomes = broadcast.get("outcomes") or {}
    line = (
        f"[{snapshot.get('generatedAt')}] pnl_snapshot "
        f"elapsedMin={round(float(snapshot.get('elapsedSeconds') or 0) / 60.0, 1)} "
        f"reviews={reviews.get('total')} valued={reviews.get('withValue')} missing={reviews.get('missingValue')} "
        f"execPositive={reviews.get('executablePositive')} "
        f"signed={reviews.get('signedPnlByCurrency')} "
        f"attempts={broadcast.get('attempts')} success={outcomes.get('success', 0)} "
        f"failed={outcomes.get('failed', 0)} pending={outcomes.get('pending', 0)} "
        f"expected={expected.get('byCurrency')} gasEth={expected.get('gasCostEthTotal')} "
        f"netAfterGas={expected.get('ethOrWethNetAfterGas')} "
        f"realized={realized.get('byCurrency')} realizedAvailable={realized.get('available')} "
        f"state={(snapshot.get('production') or {}).get('state')}"
    )
    with pnl_log.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run_tool(arguments: list[str], timeout: int = 900) -> dict[str, Any]:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "returncode": -1, "error": str(error)}
    stdout = result.stdout or ""
    return {"ok": result.returncode == 0, "returncode": result.returncode,
            "stdout": stdout[-200000:], "stdoutTruncated": len(stdout) > 200000,
            "stderr": (result.stderr or "")[-4000:]}


def pool_manager_command_support(tools: Path) -> dict[str, Any]:
    """Report which pool-manager commands the deployed binary understands.

    The healer drives the pool manager as a CLI. A binary that predates a
    command rejects it with "unknown command", and the healer used to carry
    on as if the step had succeeded. That turned a deployment gap into a
    silent no-op: the depth refresh never ran and the capacity columns never
    appeared, while the report looked healthy.

    The probe is read-only: the binary is invoked without a subcommand and
    prints its usage line, which lists every supported command.
    """
    binary = tools / "pool-manager.exe"
    if not binary.exists():
        return {"available": False, "complete": False,
                "reason": "pool_manager_binary_missing",
                "binary": str(binary), "missingCommands": list(POOL_MANAGER_REQUIRED_COMMANDS)}
    try:
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        # ``OSError`` covers the cases ``FileNotFoundError`` does not: a
        # truncated download, a binary built for another architecture, or a
        # file that is not an executable at all. The probe must never take
        # the healer down, because a broken deployment has to be reported
        # as a broken deployment, not as a crash.
        return {"available": False, "complete": False,
                "reason": "pool_manager_probe_failed",
                "binary": str(binary), "error": str(error),
                "missingCommands": list(POOL_MANAGER_REQUIRED_COMMANDS)}
    usage = f"{result.stdout or ''}\n{result.stderr or ''}"
    missing = [command for command in POOL_MANAGER_REQUIRED_COMMANDS
               if command not in usage]
    return {
        "available": True,
        "binary": str(binary),
        "binaryModifiedAt": dt.datetime.fromtimestamp(
            binary.stat().st_mtime, dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "requiredCommands": list(POOL_MANAGER_REQUIRED_COMMANDS),
        "missingCommands": missing,
        "complete": not missing,
        "reason": ("the deployed pool-manager binary does not implement the commands "
                   "the healer needs; rebuild and deploy it before the capacity rollout")
        if missing else None,
    }


def api_post(base: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(base.rstrip("/") + path,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-Request-Id": f"healer-{uuid.uuid4().hex}",
                 "Idempotency-Key": f"healer-{uuid.uuid4().hex}"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return {"ok": 200 <= response.status < 300, "status": response.status,
                    "body": response.read().decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code,
                "body": error.read().decode("utf-8", errors="replace")}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"ok": False, "status": None, "body": str(error)}


def api_get(base: str, path: str, timeout: int = 5) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {"body": raw[-2000:]}
            return {"ok": 200 <= response.status < 300, "status": response.status, "payload": payload}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        return {"ok": False, "status": getattr(error, "code", 0), "error": str(error)}


def runtime_business_health(runtime_api: str) -> dict[str, Any]:
    liveness = api_get(runtime_api, "/healthz", timeout=5)
    if not liveness.get("ok"):
        return {"ok": False, "stage": "liveness", "liveness": liveness}
    readiness = api_get(
        runtime_api,
        f"/api/dex/opportunity-scanner/snapshot?chain_id={CHAIN_ID}&protocols=uniswap_v4&summary=1",
        timeout=20,
    )
    return {
        "ok": bool(readiness.get("ok")),
        "stage": "business_readiness",
        "liveness": liveness,
        "readiness": readiness,
    }


def runtime_environment(port: int) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "FLASHLOAN_RUNTIME_PROFILE": "v4_dex",
        "DEX_ACTIVE_CHAIN_ID": str(CHAIN_ID),
        "DEX_ACTIVE_NETWORK": NETWORK,
        "PORT": str(port),
        "PYTHONUNBUFFERED": "1",
    })
    return environment


def healing_actions(report: dict[str, Any]) -> list[str]:
    layers = report.get("layers") or {}
    baseline_issues = set((layers.get("databaseBaseline") or {}).get("issues") or [])
    if any(issue.startswith("extension_library_") for issue in baseline_issues):
        return ["repair_tokens"]
    if "latest_manager_migration_not_applied" in baseline_issues:
        return ["repair_manager_schema"]
    if any(issue.startswith("core_scope_") or issue.startswith("candidate_scope_")
           or issue.startswith("observation_")
           for issue in baseline_issues):
        return ["repair_pools"]
    if not (layers.get("tokens") or {}).get("valid"):
        # GAP-C: a single broken symbol has to be repaired on its own.
        # The batch path rewrites every library, which cannot tell the
        # operator which symbol was actually repaired. The token layer
        # therefore reports its per-symbol defects, and the healer repairs
        # them one by one through ``POST /v1/tokens/resolve``.
        token_layer = layers.get("tokens") or {}
        targets = resolve_token_targets(token_layer.get("defects") or [])
        if targets:
            return ["resolve_token"]
        return ["repair_tokens"]
    # A closed-loop scope drift is a closed-loop defect, not a pool defect.
    # The pool set itself is healthy in this case: every hop resolves and no
    # hop names a pool the candidate scope dropped. Reporting it as a pool
    # repair sends the chain after the pool materializer, which cannot clear
    # the stale checksum, so the drift would survive every repair round.
    pool_layer = layers.get("pools") or {}
    loop_scope = closed_loop_scope_drift(pool_layer.get("scopeDrift"))
    if loop_scope.get("closedLoopScopeDrifted") and not loop_scope.get(
        "hopsMissingFromCandidateScope"
    ):
        return ["repair_closed_loops"]
    if not pool_layer.get("valid"):
        # GAP-D: the two pool defects have different repairs.
        #
        # * ``pool_reading_invalid`` means the stored depth number is not
        #   trustworthy, so the pools have to be **re-measured**. Swapping
        #   them would remove pools that may be perfectly fine.
        # * ``pool_depth_insufficient`` means the pools really are too
        #   shallow, so they have to be **replaced** by deeper ones.
        #
        # Both are handled by the pool manager, but the report has to say
        # which one it is, otherwise the operator cannot tell a broken
        # measurement from an exhausted market.
        depth = pool_layer.get("depth") or {}
        classification = str(depth.get("classification") or "")
        # * ``pool_capacity_store_defect`` - the store cannot hold the
        #   measured capacity, so the schema has to gain the columns before
        #   anything can be measured.
        # * ``pool_depth_unmeasured`` - the store is fine, these pools were
        #   simply never measured.
        # * ``pool_reading_invalid`` - the stored depth number is not
        #   trustworthy, so the pools have to be re-measured.
        #
        # The three are different defects with different repairs. Swapping a
        # pool would remove pools that may be perfectly fine, so only a
        # genuinely shallow market reaches the pool materializer.
        if classification == "pool_capacity_store_defect":
            return ["ensure_depth_schema", "remeasure_pool_depth"]
        if classification in ("pool_reading_invalid", "pool_depth_unmeasured"):
            return ["remeasure_pool_depth"]
        return ["repair_pools"]
    if not (layers.get("closedLoops") or {}).get("valid"):
        return ["repair_closed_loops"]
    actions = []
    reviews = layers.get("reviews") or {}
    stats = (reviews.get("legacy") or {}).get("stats") or {}
    if not reviews.get("valid") or int(stats.get("missingValue") or 0) > 0:
        actions.append("repair_reviews")
    # The broadcast layer is only repaired when the review layer above it is
    # already sound **and** produced a positive net result. A failed attempt
    # without an executable positive review is either a market outcome or a
    # defect of an upstream layer, and repairing it here would only paper
    # over the real cause.
    broadcast = layers.get("broadcast") or {}
    outcomes = broadcast.get("outcomes") or {}
    reviews_sound = bool(reviews.get("valid")) and int(stats.get("missingValue") or 0) == 0
    positive_reviews = int(stats.get("positive") or 0) > 0
    if int(outcomes.get("failed") or 0) > 0 and reviews_sound and positive_reviews:
        actions.append("repair_broadcast_failures")
    actions.append("fill_current_loop_reviews")
    actions.append("rereview_historical_positive_candidates")
    actions.append("observe_automatic_broadcast")
    return actions


#: The five layers of the cascade, in dependency order.  A layer may only be
#: repaired when every layer above it is already valid: the cascade is
#: strictly top-down, which is what keeps a broken upstream layer from being
#: masked by a downstream repair.
CASCADE_LAYERS = ("tokens", "pools", "closedLoops", "reviews", "broadcast")


def upstream_layers_valid(report: dict[str, Any]) -> bool:
    """Whether every layer above the review layer is currently valid.

    The broadcast layer is deliberately excluded: it is the layer the
    cascade converges *towards*, and it is repaired last.  A failed
    broadcast attempt must never be the reason the upstream gate opens.
    """
    layers = report.get("layers") or {}
    for layer in CASCADE_LAYERS[:3]:
        if not (layers.get(layer) or {}).get("valid"):
            return False
    return True


def cascade_layer_status(report: dict[str, Any]) -> dict[str, Any]:
    """Per-layer validity of the five cascade layers.

    The report is the single source of truth for the cascade: the same
    dict drives the gate, the action selection and the convergence
    verdict, so the three can never disagree about the current state.
    """
    layers = report.get("layers") or {}
    reviews = layers.get("reviews") or {}
    stats = (reviews.get("legacy") or {}).get("stats") or {}
    broadcast = layers.get("broadcast") or {}
    outcomes = broadcast.get("outcomes") or {}
    status = {
        "tokens": bool((layers.get("tokens") or {}).get("valid")),
        "pools": bool((layers.get("pools") or {}).get("valid")),
        "closedLoops": bool((layers.get("closedLoops") or {}).get("valid")),
        "reviews": bool(reviews.get("valid")) and int(stats.get("missingValue") or 0) == 0,
        "broadcast": bool(broadcast.get("valid")),
    }
    return {
        "layers": status,
        "allValid": all(status.values()),
        "missingReviewValue": int(stats.get("missingValue") or 0),
        "positiveReviews": int(stats.get("positive") or 0),
        "broadcastFailed": int(outcomes.get("failed") or 0),
        "broadcastSuccess": int(outcomes.get("success") or 0),
        "blockers": list(report.get("blockers") or []),
        "productionState": report.get("productionState"),
        "safeToAutoBroadcast": bool(report.get("safeToAutoBroadcast")),
    }


def start_runtime(port: int, log_dir: Path) -> tuple[subprocess.Popen[Any], dict[str, str]]:
    log_dir.mkdir(parents=True, exist_ok=True)
    launch_stamp = stamp()
    stdout_path = log_dir / f"runtime_{launch_stamp}.stdout.log"
    stderr_path = log_dir / f"runtime_{launch_stamp}.stderr.log"
    stdout_handle = stdout_path.open("a", encoding="utf-8")
    stderr_handle = stderr_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [sys.executable, str(RUNTIME_ENTRYPOINT), "--port", str(port)],
        cwd=str(ROOT),
        env=runtime_environment(port),
        stdin=subprocess.DEVNULL,
        stdout=stdout_handle,
        stderr=stderr_handle,
        creationflags=creationflags,
    )
    stdout_handle.close()
    stderr_handle.close()
    return process, {"stdout": str(stdout_path), "stderr": str(stderr_path)}


def wait_for_runtime(runtime_api: str, process: subprocess.Popen[Any] | None, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + max(1, timeout)
    last_health: dict[str, Any] = {"ok": False, "error": "runtime_not_checked"}
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return {"ok": False, "error": "runtime_exited_before_ready", "returncode": process.returncode}
        last_health = api_get(runtime_api, "/healthz")
        if last_health.get("ok"):
            return last_health
        time.sleep(1)
    return {"ok": False, "error": "runtime_readiness_timeout", "lastHealth": last_health}


def stop_runtime(process: subprocess.Popen[Any] | None, timeout: int = 15) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def response_contains(response: dict[str, Any], marker: str) -> bool:
    return marker.lower() in str(response.get("body") or response.get("error") or "").lower()


def response_payload(response: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(response.get("body") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def collect_completed_review(
    db: Path,
    candidate: dict[str, Any],
    attempts: int = 20,
    interval_seconds: float = 3.0,
) -> dict[str, Any] | None:
    """Collect the review the runtime finished after the transition was accepted.

    ``manual-review`` is an **asynchronous** RuntimeCore transition. Its HTTP
    200 response only says the transition was accepted; the review itself runs
    in the Rust worker and lands in the review library afterwards. Without
    this wait the healer saw zero evidence for every candidate and the
    Manager library never received a single row for the current library.

    The review is matched by the **published identity**: the kernel writes
    the closed-loop key of the Manager into the review payload, so the
    healer compares that key with the key it asked for. A review of a
    different starting point of the same triangle carries a different key
    and is therefore not accepted for this candidate.
    """
    loop_key = str(candidate.get("loopKey") or "").strip()
    if not loop_key:
        return None
    loop_keys = {loop_key}
    started = time.monotonic()
    for attempt in range(max(1, attempts)):
        rows = all_rows(
            db,
            "SELECT id,created_at,pair,category,payload_json,summary_json,"
            "selected_profit_raw,selected_profit_amount,selected_profit_amount_symbol,"
            "selected_profit_source,selected_profit_missing_reason "
            "FROM cow_execution_reviews WHERE network=? AND chain_id=? "
            "ORDER BY id DESC LIMIT 400",
            (NETWORK, CHAIN_ID),
        )
        for row in rows:
            if not review_row_matches_loop_keys(row, loop_keys):
                continue
            if not _review_row_has_concrete_pnl(row):
                continue
            return {
                "reviewId": row.get("id"),
                "reviewedAt": row.get("created_at"),
                "pair": row.get("pair"),
                "category": row.get("category"),
                "signedPnlRaw": row.get("selected_profit_raw"),
                "signedPnlAmount": row.get("selected_profit_amount"),
                "signedPnlCurrency": row.get("selected_profit_amount_symbol"),
                "signedPnlSource": row.get("selected_profit_source"),
                "payload": as_json(row.get("payload_json")),
                "summary": as_json(row.get("summary_json")),
                "attempts": attempt + 1,
                "waitSeconds": round(time.monotonic() - started, 3),
            }
        time.sleep(max(0.0, interval_seconds))
    return None


def review_row_matches_loop_keys(row: dict[str, Any], loop_keys: set[str]) -> bool:
    """Return whether the review was filed under one of the given keys.

    The key is read from the **single published** field name ``loopKey``,
    and the route addresses are never joined into a key. The bridge layer
    translates the kernel field (``closedLoopKey``) into the evidence field
    (``loopKey``) exactly once, so the healer consumes one name instead of
    guessing between several (MIO-7 / AC-214).

    The closed-loop key is the funding anchor plus the directed pool path.
    The match is exact: a review that carries the key of a different
    starting point names a different closed loop and is not accepted here.

    A review that carries no published identity is **not** associated with
    any loop: it is reported as unmatched rather than being matched by a
    fallback key name.
    """
    for payload in review_payloads(row):
        value = str(payload.get("loopKey") or "").strip()
        if value and value in loop_keys:
            return True
    return False


def _review_row_has_concrete_pnl(row: dict[str, Any]) -> bool:
    """Return whether the review carries a measured, signed profit value.

    A review whose best trade amount was measured always has a signed value:
    a measured amount of zero yields a profit of exactly zero, and that zero
    is a **result**, not a missing measurement. A review that never measured
    anything carries no value at all and is therefore not reviewable.
    """
    raw = str(row.get("selected_profit_raw") or "").strip()
    if not raw or raw.lower() in {"null", "none"}:
        return False
    if raw in {"0x0", "0x00", "0"}:
        # A zero is only a result when the best amount was measured.
        return _review_row_best_amount_measured(row)
    return True


def _review_row_best_amount_measured(row: dict[str, Any]) -> bool:
    for payload in review_payloads(row):
        for key in ("bestAmountCalculation", "best_amount_calculation"):
            block = payload.get(key)
            if not isinstance(block, dict):
                continue
            if block.get("bestAmountMeasured") or block.get("bestAmountConfirmed"):
                return True
    return False


def response_succeeded(response: dict[str, Any]) -> bool:
    payload = response_payload(response)
    return bool(response.get("ok")) and payload.get("ok") is not False


def merge_with_stored_evidence(
    db: Path,
    manager_api: str | None,
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the evidence of **every** measured cycle of the chain.

    The Review Manager replaces the whole review set of the scope with the
    evidence it receives. Submitting only the cycles that were just
    re-reviewed would therefore erase the reviews of the cycles that were
    left out, and the library would report them as "evidence is missing"
    even though a valid measurement exists for them.

    The stored reviews are read back from the Manager library, because that
    is the authority for the structure of a review: it knows which loop a
    measurement belongs to and it already holds the signed profit.
    """
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in evidence:
        loop_key = str(item.get("loopKey") or "").strip()
        if not loop_key:
            continue
        if loop_key in seen:
            continue
        seen.add(loop_key)
        merged.append(item)
    stored = stored_review_evidence(db, manager_api)
    for item in stored:
        loop_key = str(item.get("loopKey") or "").strip()
        if not loop_key:
            continue
        if loop_key in seen:
            continue
        seen.add(loop_key)
        merged.append(item)
    return merged


def stored_review_evidence(
    db: Path,
    manager_api: str | None = None,
) -> list[dict[str, Any]]:
    """Return the stored reviews of the chain as Review Manager evidence.

    Only a review that carries a complete ``runArbitrage`` block can be
    filed again: a review without one is an incomplete measurement, and it
    must be replaced by a fresh review rather than resubmitted.

    The stored reviews are the **Review Manager's** records, so they are
    read through the manager API. The manager publishes the static preview
    and the arbitrage block as structured values, and the adapter below
    only re-names them.
    """
    rows = manager_read_reviews(manager_api)
    if not rows:
        return []
    # The legacy review library still holds the kernel's measured best trade
    # amount, because the amount ladder is published by the kernel next to
    # the probe records.  A Manager row that lost the block on the way in
    # is restored from here, matched by the exact published closed-loop key
    # so a measurement never crosses into another closed loop.
    #
    # The legacy library is a **dynamic runtime table** that no Rust manager
    # owns yet, so it is still read from the store. It holds the runtime's
    # own measurements, not a manager-owned record.
    measured = legacy_best_amount_by_cycle(db)
    evidence: list[dict[str, Any]] = []
    for row in rows:
        run = row.get("runArbitrage")
        if not isinstance(run, dict) or not run:
            continue
        signed_pnl = run.get("signedPnl") or run.get("signed_pnl")
        if not _complete_signed_pnl(signed_pnl):
            continue
        normalized_run = dict(run)
        normalized_run["signedPnl"] = signed_pnl
        normalized_run.pop("signed_pnl", None)
        if "blockNumber" not in normalized_run and "block_number" in normalized_run:
            normalized_run["blockNumber"] = normalized_run.pop("block_number")
        if not isinstance(normalized_run.get("diagnostics"), list):
            normalized_run["diagnostics"] = []
        static_preview = row.get("staticPreview")
        preview = dict(static_preview) if isinstance(static_preview, dict) else {}
        # The stored row may carry the measured best trade amount in the
        # static preview or inside the run block.  Both are preserved so a
        # re-submission never downgrades a measured amount into a missing
        # one.
        calculation = kernel_best_amount_calculation([preview, normalized_run])
        if not calculation:
            loop_key = str(row.get("loopKey") or "").strip()
            calculation = measured.get(loop_key, {})
        if calculation and not isinstance(preview.get("bestAmountCalculation"), dict):
            preview["bestAmountCalculation"] = calculation
        stored_evidence: dict[str, Any] = {
            "loopKey": str(row.get("loopKey") or "").strip(),
            "staticPreview": preview,
            "runArbitrage": normalized_run,
            "diagnostics": [],
            "reviewedAt": str(row.get("reviewedAt") or "").strip() or now(),
        }
        # A stored review carries the identity it was measured under, and
        # the identity is published by the Review Manager.  It is copied
        # forward unchanged so a re-submission never downgrades a known
        # identity into an absent one.
        identity = row.get("closedLoopIdentity")
        if isinstance(identity, dict) and identity:
            stored_evidence["closedLoopIdentity"] = identity
        evidence.append(stored_evidence)
    return evidence


def legacy_best_amount_by_cycle(db: Path) -> dict[str, dict[str, Any]]:
    """Return the measured best trade amount of every cycle of the chain.

    The legacy review library keeps the whole kernel review payload, so it
    still holds the best-trade-amount block that the Manager rows lost.  The
    block is indexed by the **exact published closed-loop key**: the
    funding anchor plus the directed pool path.  A measurement belongs to
    the closed loop it names and to no other.
    """
    rows = all_rows(
        db,
        "SELECT id,created_at,payload_json,summary_json FROM cow_execution_reviews "
        "WHERE network=? AND chain_id=? ORDER BY id DESC LIMIT 4000",
        (NETWORK, CHAIN_ID),
    )
    measured: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = as_json(row.get("payload_json"))
        summary = as_json(row.get("summary_json"))
        calculation = kernel_best_amount_calculation([payload, summary])
        if not calculation:
            continue
        if not calculation.get("bestAmountMeasured") and \
                not calculation.get("bestAmountConfirmed"):
            continue
        loop_key = None
        for candidate in (payload, summary):
            if not isinstance(candidate, dict):
                continue
            value = str(candidate.get("loopKey") or "").strip()
            if value:
                loop_key = value
                break
            if loop_key:
                break
        if not loop_key:
            # A legacy row that carries only the route it measured has no
            # published closed-loop identity. The addresses are never
            # joined into a key here: that would invent an identity the
            # Manager never published and could name the wrong loop.
            # Such a row simply contributes no measured amount.
            continue
        # The newest measurement of a closed loop wins.
        measured.setdefault(loop_key, calculation)
    return measured


def pool_repair_keys(review_result: dict[str, Any]) -> list[str]:
    keys: set[str] = set()
    for item in review_result.get("results") or []:
        live = item.get("liveReviewRequest") if isinstance(item, dict) else None
        if not isinstance(live, dict):
            continue
        for response in (live.get("initialReview"), live.get("workerRefresh"), live.get("finalReview")):
            if not isinstance(response, dict):
                continue
            payload = response_payload(response)
            detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
            for edge in detail.get("missingEdges") or []:
                if isinstance(edge, dict) and str(edge.get("keyId") or "").strip():
                    keys.add(str(edge["keyId"]).strip().lower())
            submission = payload.get("submission") if isinstance(payload.get("submission"), dict) else {}
            for failure in submission.get("failures") or []:
                failure_detail = failure.get("detail") if isinstance(failure, dict) else None
                if not isinstance(failure_detail, dict):
                    continue
                for edge in failure_detail.get("missingEdges") or []:
                    if isinstance(edge, dict) and str(edge.get("keyId") or "").strip():
                        keys.add(str(edge["keyId"]).strip().lower())
    return sorted(keys)


def review_expected_upstream(
    db: Path,
    manager_api: str | None = None,
) -> dict[str, Any]:
    """Read the upstream versions and checksums the review library must carry.

    The Review Manager refuses a review whose upstream does not match the
    current closed-loop library. The healer therefore has to send the
    versions and checksums it actually sees, otherwise the manager rejects
    every review as stale and the convergence can never make progress.

    The versions belong to the **closed-loop Manager**, so they are read
    through the manager API.
    """
    status = manager_read_closed_loops(manager_api).get("status") or {}
    if not status:
        return {}
    return {
        "closedLoopVersion": int(status.get("closedLoopVersion") or 0),
        "closedLoopChecksum": str(status.get("materializedChecksum") or ""),
        "candidateEdgeVersion": int(status.get("candidateEdgeVersion") or 0),
        "candidateEdgeChecksum": str(status.get("candidateEdgeChecksum") or ""),
        "observationVersion": int(status.get("observationVersion") or 0),
        "observationChecksum": str(status.get("observationChecksum") or ""),
        "poolScopeChecksum": str(status.get("poolScopeChecksum") or ""),
    }


def materialize_reviews(
    db: Path,
    manager_api: str,
    evidence: list[dict[str, Any]],
    apply: bool,
) -> dict[str, Any]:
    """Materialize the reviews into the Manager-authoritative library.

    ``manager_v4_reviews`` is the table the Review Manager owns, and it is
    the only review source that can prove a review belongs to the current
    closed-loop library. A review that only exists in the legacy table has
    no upstream versions, so it cannot be audited against the current
    structure.

    The materialization therefore goes through the manager API, which owns
    the write connection and the idempotency ledger.
    """
    if not evidence:
        return {"mode": "materialize-reviews", "generatedAt": now(), "apply": apply,
                "dryRun": not apply, "evidenceCount": 0, "materializedCount": 0,
                "results": [], "policy": "no evidence means nothing to materialize"}
    expected = review_expected_upstream(db, manager_api)
    # The Review Manager keys every review by its closed-loop identity.
    #
    # That identity is **published by the closed-loop Manager** and travels
    # with the evidence. It is never rebuilt here: the Manager may publish
    # several loops for one symbol path, so a key assembled out of the
    # route addresses can name the wrong loop and file the review against a
    # capacity that was never proved for it.
    #
    # An evidence block without an identity is therefore **not** reviewable.
    # It is dropped, and the orchestrator reviews the loop again, rather
    # than being filed under a guess.
    prepared: list[dict[str, Any]] = []
    dropped_without_identity = 0
    # One closed loop is measured once.  The Review Manager files a review
    # per loop and rejects a second evidence block for the same loop, so the
    # evidence has to be deduplicated by its published key before it is
    # submitted.
    seen_loops: set[str] = set()
    duplicate_loops = 0
    for item in evidence:
        if not isinstance(item, dict):
            continue
        enriched = dict(item)
        if not str(enriched.get("loopKey") or "").strip():
            dropped_without_identity += 1
            continue
        loop_key = str(enriched["loopKey"]).strip()
        if loop_key in seen_loops:
            duplicate_loops += 1
            continue
        seen_loops.add(loop_key)
        prepared.append(enriched)
    evidence = prepared
    if not evidence:
        return {"mode": "materialize-reviews", "generatedAt": now(), "apply": apply,
                "dryRun": not apply, "evidenceCount": 0, "materializedCount": 0,
                "droppedWithoutLoopIdentity": dropped_without_identity,
                "duplicateLoops": duplicate_loops,
                "results": [],
                "policy": "no evidence carries a closed-loop identity"}
    # The Review Manager replaces the **whole** review set of the scope with
    # the evidence it receives. A partial evidence set would therefore erase
    # the reviews of the cycles it does not mention, so the submission always
    # carries every measured cycle: the fresh evidence for the cycles that
    # were just re-reviewed, plus the evidence that is already stored for the
    # cycles that are still valid.
    merged = merge_with_stored_evidence(db, manager_api, evidence)
    body = {"chainId": CHAIN_ID, "dryRun": not apply, "evidence": merged}
    if expected:
        body["expectedUpstream"] = expected
    response = api_post(manager_api, "/v1/reviews/run", body)
    materialized = count(db, "manager_v4_reviews") if apply else None
    return {"mode": "materialize-reviews", "generatedAt": now(), "apply": apply,
            "dryRun": not apply, "evidenceCount": len(merged),
            "submittedEvidenceCount": len(evidence),
            "droppedWithoutLoopIdentity": dropped_without_identity,
            "duplicateLoops": duplicate_loops,
            "expectedUpstream": expected or None,
            "managerReviewRows": materialized,
            "response": response,
            "policy": "reviews are materialized into the Manager-authoritative library"}


def registered_closed_loop_group_keys(runtime_api: str) -> list[str]:
    """Return the group keys the resident review workers actually run under.

    The worker pool keys its state machine by the exact string the worker
    was registered with, and the scanner registers a worker under the route
    order it discovered.  The closed-loop key is the funding anchor plus
    the directed pool path, so the registered key is the exact ordered
    path of the loop the worker serves; a route that starts at an
    intermediate token belongs to a different closed loop and resolves to
    a different worker (or to none).

    The worker list lives on the **scanner snapshot**, not on
    ``/api/dex/overview``.  The overview endpoint answers with the DEX
    surface document itself and has no ``payload`` member at all, so
    reading ``overview["payload"]["workers"]`` silently yields an empty
    list and makes every registered key invisible.  The scanner snapshot
    reports the resident workers under
    ``scanner.closed_loop_rust_workers.workers``.
    """
    status = api_get(
        runtime_api,
        f"/api/dex/opportunity-scanner/snapshot?chain_id={CHAIN_ID}"
        "&protocols=uniswap_v4&summary=1",
        timeout=60,
    )
    if not status.get("ok"):
        return []
    payload = status.get("payload") if isinstance(status.get("payload"), dict) else {}
    scanner = payload.get("scanner") if isinstance(payload.get("scanner"), dict) else {}
    workers_block = (
        scanner.get("closed_loop_rust_workers")
        if isinstance(scanner.get("closed_loop_rust_workers"), dict)
        else {}
    )
    workers = workers_block.get("workers") if isinstance(workers_block.get("workers"), list) else []
    keys: list[str] = []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        if worker.get("thread_live") is False:
            continue
        key = str(worker.get("group_key") or "").strip().lower()
        if key and key not in keys:
            keys.append(key)
    return keys


def resolve_closed_loop_group_key(runtime_api: str, route_symbols: list[str]) -> str:
    """Resolve the group key that serves the requested route.

    The lookup is **exact and ordered**. The worker pool keys its state
    machine by the exact string the worker was registered with, so only the
    identical ordered token path addresses that worker. A different
    starting point of the same triangle is a different worker with a
    different closed-loop identity, and it must not borrow this one's key.

    When no worker is registered the requested key is returned unchanged so
    the runtime answers with the precise ``closed_loop_group_not_found``
    error instead of a silent fallback onto another loop.
    """
    requested = "|".join(
        str(symbol).strip().lower() for symbol in (route_symbols or [])[:3]
    )
    registered = registered_closed_loop_group_keys(runtime_api)
    if not registered:
        return requested
    resolved = resolve_registered_group_key(route_symbols, registered)
    return resolved or requested


def request_live_review(
    db: Path,
    runtime_api: str,
    candidate: dict[str, Any],
    apply: bool,
) -> dict[str, Any]:
    body = {
        "routeSymbols": candidate["routeSymbols"],
        "routeAddresses": candidate["routeAddresses"],
        "mode": "deep",
        "dryRun": not apply,
    }
    initial = api_post(
        runtime_api,
        f"/api/dex/closed-loop-groups/manual-review?chain_id={CHAIN_ID}",
        body,
    )
    result = {"initialReview": initial, "workerRefreshRequired": False}
    # A closed loop that has **no resident worker** answers with
    # ``closed_loop_group_not_found`` (the worker pool never registered the
    # key), while a loop whose worker exists but is stopped answers with
    # ``closed_loop_group_worker_not_running``.  Both mean the same thing to
    # the healer: the worker has to be (re)registered and started before the
    # review can run.  Treating only the second one as refreshable left every
    # loop outside the resident set permanently unreachable, because the
    # healer never registered the worker it needed.
    worker_missing = (
        response_contains(initial, "closed_loop_group_worker_not_running")
        or response_contains(initial, "closed_loop_group_not_found")
    )
    if not worker_missing:
        result["finalReview"] = initial
        result["completedReview"] = collect_completed_review(db, candidate)
        return result
    result["workerRefreshRequired"] = True
    group_key = resolve_closed_loop_group_key(runtime_api, candidate["routeSymbols"])
    result["groupKey"] = group_key
    if not apply:
        result["workerRefreshApplied"] = False
        result["finalReview"] = initial
        result["completedReview"] = collect_completed_review(db, candidate)
        return result
    refresh_path = (
        "/api/dex/closed-loop-groups/"
        + urllib.parse.quote(group_key, safe="")
        + f"/refresh?chain_id={CHAIN_ID}"
    )
    refresh = api_post(runtime_api, refresh_path, {"mode": "deep"})
    result["workerRefreshApplied"] = True
    result["workerRefresh"] = refresh
    if not response_succeeded(refresh):
        result["finalReview"] = initial
        result["completedReview"] = collect_completed_review(db, candidate)
        return result
    result["reviewTriggeredByRefresh"] = True
    result["finalReview"] = refresh
    result["completedReview"] = collect_completed_review(db, candidate)
    return result


def historical_positive_candidates(
    db: Path,
    limit: int,
    excluded_loop_keys: set[str] | None = None,
    manager_api: str | None = None,
) -> list[dict[str, Any]]:
    loops = {row["loop_key"]: row for row in manager_loop_rows(manager_api)}
    latest = {}
    con = connect(db)
    try:
        rows = con.execute(
            "SELECT id,created_at,category,selected_profit_amount,selected_profit_amount_symbol,"
            "payload_json "
            "FROM cow_execution_reviews WHERE network=? AND chain_id=? "
            "AND datetime(created_at)>=datetime(?) AND selected_profit_amount IS NOT NULL",
            (NETWORK, CHAIN_ID, observation_started_at())).fetchall()
        for row in rows:
            row = dict(row)
            value = amount(row.get("selected_profit_amount"))
            if value is None or value <= 0:
                continue
            key = review_loop_key(row)
            if key not in loops:
                continue
            previous = latest.get(key)
            if previous is None or str(row.get("created_at")) > str(previous.get("created_at")):
                latest[key] = row
    finally:
        con.close()
    candidates = []
    excluded = excluded_loop_keys or set()
    for key, row in latest.items():
        if key in excluded:
            continue
        loop = loops[key]
        candidates.append({"loopKey": key, "path": loop["path_text"],
            "routeSymbols": json.loads(loop["path_symbols_json"]),
            "routeAddresses": json.loads(loop["path_addresses_json"]),
            "historicalReviewId": row["id"], "historicalProfit": row["selected_profit_amount"],
            "historicalCurrency": row["selected_profit_amount_symbol"],
            "historicalCategory": row["category"],
            "historicalReviewedAt": row["created_at"]})
    candidates.sort(key=lambda item: str(item["historicalReviewedAt"]), reverse=True)
    return candidates[:limit]


def rereview_historical_positive(
    db: Path,
    runtime_api: str,
    apply: bool,
    limit: int,
    excluded_loop_keys: set[str] | None = None,
    manager_api: str | None = None,
) -> dict[str, Any]:
    """Re-review the routes that were profitable before.

    The historical profit only ranks the candidates. A route may only be
    broadcast on the strength of a **new** positive live review, and that
    review has to land in the Manager-authoritative library.
    """
    candidates = historical_positive_candidates(db, limit, excluded_loop_keys, manager_api)
    results = []
    evidence_payloads: list[dict[str, Any]] = []
    for candidate in candidates:
        response = request_live_review(db, runtime_api, candidate, apply)
        evidence = live_review_evidence(response, candidate)
        if evidence:
            evidence_payloads.append(evidence)
        results.append({**candidate, "historicalProfitUsedOnlyForPriority": True,
                        "broadcastRequestedByHealer": False, "liveReviewRequest": response,
                        "evidenceCaptured": bool(evidence)})
    materialization = None
    if manager_api and evidence_payloads:
        materialization = materialize_reviews(db, manager_api, evidence_payloads, apply)
    return {"mode": "rereview", "generatedAt": now(), "apply": apply, "dryRun": not apply,
            "policy": "historical profit ranks candidates; only a new positive live review may broadcast",
            "candidateCount": len(candidates), "results": results,
            "evidenceCount": len(evidence_payloads),
            "materialization": materialization}


def missing_review_candidates(
    db: Path,
    limit: int,
    excluded_loop_keys: set[str] | None = None,
    manager_api: str | None = None,
) -> list[dict[str, Any]]:
    reviewed = {review_loop_key(row) for row in review_rows(db, 24 * 365, None, manager_api)}
    # A review covers a loop when it was filed under the **same published
    # identity**. The key of the loop and the key of its review are the
    # same string the Manager published, so exact equality decides it. A
    # review of a different starting point of the same triangle is a
    # different loop and leaves this loop unreviewed.
    reviewed_keys: set[str] = {key for key in reviewed if key}
    excluded = excluded_loop_keys or set()
    loops = manager_loop_rows(manager_api)
    candidates = []
    for loop in loops:
        if loop["loop_key"] in reviewed_keys or loop["loop_key"] in excluded:
            continue
        candidates.append({"loopKey": loop["loop_key"], "path": loop["path_text"],
            "routeSymbols": json.loads(loop["path_symbols_json"]),
            "routeAddresses": json.loads(loop["path_addresses_json"])})
    return candidates[:limit]


def fill_missing_reviews(
    db: Path,
    runtime_api: str,
    apply: bool,
    limit: int,
    excluded_loop_keys: set[str] | None = None,
    manager_api: str | None = None,
) -> dict[str, Any]:
    """Review every closed loop that has no review yet.

    A loop without a review cannot be broadcast and cannot be counted, so
    the gap has to be closed. The review is a fresh live one, and its
    evidence is materialized into the Manager-authoritative library.
    """
    candidates = missing_review_candidates(db, limit, excluded_loop_keys, manager_api)
    results = []
    evidence_payloads: list[dict[str, Any]] = []
    for candidate in candidates:
        response = request_live_review(db, runtime_api, candidate, apply)
        evidence = live_review_evidence(response, candidate)
        if evidence:
            evidence_payloads.append(evidence)
        results.append({**candidate, "broadcastRequestedByHealer": False,
                        "liveReviewRequest": response,
                        "evidenceCaptured": bool(evidence)})
    materialization = None
    if manager_api and evidence_payloads:
        materialization = materialize_reviews(db, manager_api, evidence_payloads, apply)
    return {"mode": "fill-reviews", "generatedAt": now(), "apply": apply, "dryRun": not apply,
            "policy": "real-time review first; only fresh positive review may trigger broadcast",
            "candidateCount": len(candidates), "results": results,
            "evidenceCount": len(evidence_payloads),
            "materialization": materialization}


def missing_pnl_review_candidates(
    db: Path,
    hours: int,
    limit: int,
    excluded_loop_keys: set[str] | None = None,
    manager_api: str | None = None,
) -> list[dict[str, Any]]:
    excluded = excluded_loop_keys or set()
    latest_by_loop: dict[str, dict[str, Any]] = {}
    for row in review_rows(db, hours, None, manager_api):
        loop_key = review_loop_key(row)
        if loop_key and loop_key not in latest_by_loop:
            latest_by_loop[loop_key] = row
    loops = {row["loop_key"]: row for row in manager_loop_rows(manager_api)}
    # A review is filed under the identity the Manager published for the
    # loop it was measured on. The loop table and the review table share
    # one key space, so the published key decides the association.
    #
    # A review of a different starting point of the same triangle belongs
    # to a different loop and is therefore not a candidate here. That is
    # the intended outcome: it must not be filed under this loop's
    # capacity (MIO-7 / TASK-149 / AC-214).
    candidates = []
    for loop_key, row in latest_by_loop.items():
        # A review is filed under the identity the Manager published for
        # the loop it was measured on. The loop table and the review
        # therefore share one key space, and a review of a different
        # starting point belongs to a different loop and is not a
        # candidate here.
        library_key = loop_key if loop_key in loops else None
        if library_key is None:
            continue
        if loop_key in excluded or pnl(row) is not None:
            continue
        loop = loops[library_key]
        candidates.append({
            "loopKey": loop_key,
            "libraryLoopKey": library_key,
            "path": loop["path_text"],
            "routeSymbols": json.loads(loop["path_symbols_json"]),
            "routeAddresses": json.loads(loop["path_addresses_json"]),
            "historicalReviewId": row.get("id"),
            "historicalMissingReason": row.get("selected_profit_missing_reason"),
        })
    return candidates[:limit]


def _closed_loop_identity_of(payloads: list[Any]) -> dict[str, Any] | None:
    """Return the closed-loop identity the kernel measured a route under.

    The **closed-loop identity** of a reviewed route is computed by the
    closed-loop kernel against the catalog the closed-loop Manager
    publishes.  It is the fact that says whether the route matched that
    catalog and whether the pools it priced are pools the Manager
    published.

    The identity travels with the review evidence so the **Review Manager**
    can store it and publish it.  A reader of the review library then asks
    the Manager for the identity instead of rebuilding it from the route
    addresses, which is what used to make a broken identity
    indistinguishable from an absent one.

    The helper only **reads** what the kernel published.  It never
    derives a verdict: a payload that publishes no identity yields no
    identity at all, so a missing measurement stays missing.
    """
    identity: dict[str, Any] = {}
    for payload in payloads:
        for candidate in _nested_review_objects(payload):
            for key, target in (
                ("closedLoopIdentityStatus", "closedLoopIdentityStatus"),
                ("closedLoopPoolIdentityStatus", "closedLoopPoolIdentityStatus"),
                ("identityMismatch", "identityMismatch"),
                ("closedLoopRepairTarget", "closedLoopRepairTarget"),
            ):
                if target in identity:
                    continue
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    identity[target] = value.strip()
                elif isinstance(value, dict) and value:
                    # The mismatch diagnosis is a structured block.  It is
                    # kept as a JSON string so the Review Manager can store
                    # it without having to know the shape of the kernel's
                    # diagnosis.
                    identity[target] = json.dumps(value, sort_keys=True)
    if not identity:
        return None
    return identity


def _nested_review_objects(value: Any):
    """Yield JSON objects from a runtime response without interpreting ACKs."""
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))


def _complete_signed_pnl(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    numeric = value.get("amount")
    if isinstance(numeric, bool):
        return False
    try:
        finite = math.isfinite(float(numeric))
    except (TypeError, ValueError, OverflowError):
        return False
    return finite and all(
        str(value.get(field) or "").strip()
        for field in ("raw", "currency", "unit", "source")
    )


def kernel_best_amount_calculation(roots: list[Any]) -> dict[str, Any]:
    """Return the best-trade-amount block the Rust kernel published.

    The kernel owns the amount ladder, so it is the only authority on the
    best trade amount and on the difference between "the ladder never ran"
    (a measurement gap) and "the ladder ran and no size paid" (a measured
    zero).  The block is published at the **top level** of the review
    payload under ``bestAmountCalculation`` (and under the snake_case
    spelling ``best_amount_calculation``), next to the probe records.

    The block used to be dropped on the way into the Manager evidence: only
    ``staticPreview`` and ``runArbitrage`` were forwarded, so every Manager
    row lost the measured amount and the healer reported
    ``best_amount_missing`` for loops the kernel had already measured.  The
    measurement therefore has to be carried across explicitly.
    """
    for root in roots:
        for payload in _nested_review_objects(root):
            for key in ("bestAmountCalculation", "best_amount_calculation"):
                block = payload.get(key)
                if isinstance(block, dict) and block:
                    return dict(block)
            # The block may also sit inside the quoted/precheck envelope.
            for envelope_key in ("quote", "precheck", "quoteAnalysis", "quote_analysis"):
                envelope = payload.get(envelope_key)
                if not isinstance(envelope, dict):
                    continue
                for key in ("bestAmountCalculation", "best_amount_calculation"):
                    block = envelope.get(key)
                    if isinstance(block, dict) and block:
                        return dict(block)
    return {}


def live_review_evidence(
    response: dict[str, Any],
    review_candidate: dict[str, Any],
) -> dict[str, Any] | None:
    """Build one complete Review Manager input from a real runtime result.

    ``manual-review`` is an asynchronous RuntimeCore transition. Its normal
    HTTP 200 response only says that the transition was accepted; it is not
    review evidence and must never be materialized. A payload becomes
    reviewable only when it contains a complete ``runArbitrage.signedPnl``.
    The closed-loop identity always comes from the Manager candidate rather
    than an optional runtime presentation field.
    """
    loop_key = str(review_candidate.get("loopKey") or "").strip()
    if not loop_key:
        return None
    roots: list[dict[str, Any]] = []
    for key in ("finalReview", "workerRefresh", "initialReview"):
        wrapper = response.get(key)
        if not isinstance(wrapper, dict):
            continue
        payload = response_payload(wrapper)
        roots.append(payload if payload else wrapper)
    # The completed review is the **result** of the asynchronous transition.
    # The transition response only acknowledges the request, so the finished
    # review is the only source that can carry a measured ``signedPnl``.
    completed = response.get("completedReview")
    if isinstance(completed, dict):
        for key in ("payload", "summary"):
            payload = completed.get(key)
            if isinstance(payload, dict):
                roots.append(payload)
    if not roots:
        roots.append(response)
    for root in roots:
        for payload in _nested_review_objects(root):
            evidence = payload.get("evidence")
            source = evidence if isinstance(evidence, dict) else payload
            run_arbitrage = source.get("runArbitrage") or source.get("run_arbitrage")
            if not isinstance(run_arbitrage, dict):
                continue
            signed_pnl = run_arbitrage.get("signedPnl") or run_arbitrage.get("signed_pnl")
            if not _complete_signed_pnl(signed_pnl):
                continue
            if not isinstance(run_arbitrage.get("ok"), bool) or not isinstance(
                run_arbitrage.get("executable"), bool
            ):
                continue
            normalized_run = dict(run_arbitrage)
            normalized_run["signedPnl"] = signed_pnl
            normalized_run.pop("signed_pnl", None)
            if "blockNumber" not in normalized_run and "block_number" in normalized_run:
                normalized_run["blockNumber"] = normalized_run.pop("block_number")
            run_diagnostics = normalized_run.get("diagnostics")
            if not isinstance(run_diagnostics, list):
                normalized_run["diagnostics"] = []
            static_preview = source.get("staticPreview") or source.get("static_preview")
            diagnostics = source.get("diagnostics")
            reviewed_at = source.get("reviewedAt") or source.get("reviewed_at")
            preview = dict(static_preview) if isinstance(static_preview, dict) else {}
            # The kernel publishes the measured best trade amount at the top
            # level of the review payload, next to the probe ladder.  Without
            # it the Manager row would carry no amount at all and the healer
            # would report ``best_amount_missing`` for a loop the kernel had
            # already measured, so the block is carried into the static
            # preview that the Review Manager reads.
            calculation = kernel_best_amount_calculation(roots)
            if calculation and not isinstance(preview.get("bestAmountCalculation"), dict):
                preview["bestAmountCalculation"] = calculation
            evidence = {
                "loopKey": loop_key,
                "staticPreview": preview,
                "runArbitrage": normalized_run,
                "diagnostics": diagnostics if isinstance(diagnostics, list) else [],
                "reviewedAt": str(reviewed_at or now()),
            }
            # The identity the kernel published travels with the evidence,
            # so the Review Manager can store it and publish it.  Without
            # it the review library could not tell a matched route from a
            # route that priced pools the closed-loop catalog does not
            # publish.
            identity = _closed_loop_identity_of(roots)
            if identity:
                evidence["closedLoopIdentity"] = identity
            return evidence
    return None

def rereview_missing_pnl(
    db: Path,
    runtime_api: str,
    apply: bool,
    hours: int,
    limit: int,
    excluded_loop_keys: set[str] | None = None,
    manager_api: str | None = None,
) -> dict[str, Any]:
    """Replace every review that carries no explicit profit value.

    The replacement is a **fresh live review**, never a zero. When the
    manager API is available, the resulting evidence is also materialized
    into the Manager-authoritative library, because that library is the only
    review source that can be audited against the current closed-loop
    structure.
    """
    candidates = missing_pnl_review_candidates(db, hours, limit, excluded_loop_keys,
                                               manager_api)
    results = []
    evidence_payloads: list[dict[str, Any]] = []
    for candidate in candidates:
        response = request_live_review(db, runtime_api, candidate, apply)
        evidence = live_review_evidence(response, candidate)
        if evidence:
            evidence_payloads.append(evidence)
        results.append({**candidate, "broadcastRequestedByHealer": False,
                        "liveReviewRequest": response,
                        "evidenceCaptured": bool(evidence)})
    materialization = None
    if manager_api and evidence_payloads:
        materialization = materialize_reviews(db, manager_api, evidence_payloads, apply)
    return {"mode": "rereview-missing-pnl", "generatedAt": now(), "apply": apply,
            "dryRun": not apply, "candidateCount": len(candidates), "results": results,
            "evidenceCount": len(evidence_payloads),
            "materialization": materialization,
            "policy": "missing PnL must be replaced by a fresh live review, never by zero"}


def rematerialize_reviews(db: Path, manager_api: str, apply: bool) -> dict[str, Any]:
    """Re-file the stored reviews into the Manager-authoritative library.

    The Manager library replaces its whole review set with the evidence it
    receives, and it stores every field of that evidence verbatim.  A review
    that was filed before the best-trade-amount block was carried across
    therefore keeps an empty static preview forever, and the healer reports
    ``best_amount_missing`` for loops the kernel had already measured.

    This mode rebuilds the evidence from the stored reviews, restores the
    measured best trade amount from the legacy review library where the
    Manager row lost it, and files the result again.  It never re-runs a
    review: the measurement already exists, it was only dropped on the way.
    """
    stored = stored_review_evidence(db, manager_api)
    if not stored:
        return {"mode": "rematerialize-reviews", "generatedAt": now(), "apply": apply,
                "dryRun": not apply, "evidenceCount": 0, "materialization": None,
                "policy": "the Manager library holds no complete review to re-file"}
    materialization = materialize_reviews(db, manager_api, stored, apply)
    return {"mode": "rematerialize-reviews", "generatedAt": now(), "apply": apply,
            "dryRun": not apply, "evidenceCount": len(stored),
            "materialization": materialization,
            "policy": "the stored reviews are re-filed with the measured best trade amount"}


# ---------------------------------------------------------------------------
# The review convergence
#
# The rule the plan sets is that every review has to carry an **explicit**
# profit number. A review without a value is not "zero profit": it is a
# measurement that was never completed, and it has to be replaced by a fresh
# review until it carries one.
#
# The convergence is therefore a loop with a bounded number of rounds. Each
# round asks the runtime for a fresh review of the routes that are still
# missing a value, and the loop stops when
#
# * no route is missing a value any more (converged), or
# * a round produced no progress (stalled: the same routes are still missing
#   a value, so another round would not change anything), or
# * the round budget is exhausted.
#
# The stall detection matters: without it the loop would spin on a route that
# can never be valued, and the supervisor would keep it in its work list
# forever.
REVIEW_CONVERGENCE_MAX_ROUNDS = 3


def _missing_value_identity(item: dict[str, Any]) -> str:
    """Return the comparable identity of one missing-value entry.

    An entry with a published loop key is identified by that key. An entry
    without one is identified by its review id, so two different
    unidentified reviews never collapse into a single work item.
    """
    loop_key = str(item.get("loopKey") or "").strip()
    if loop_key:
        return f"loop:{loop_key}"
    return f"review:{item.get('reviewId')}"


def reviews_missing_a_value(db: Path, hours: int) -> list[dict[str, Any]]:
    """Returns the review routes that still carry no explicit profit value."""
    latest_by_loop: dict[str, dict[str, Any]] = {}
    unassociated: list[dict[str, Any]] = []
    for row in review_rows(db, hours):
        loop_key = review_loop_key(row)
        if not loop_key:
            # A review without a published identity is still a review that
            # may be missing a value. It is counted under its own review
            # id instead of being dropped, so a missing measurement is
            # never silently hidden (MIO-7 / AC-214).
            unassociated.append(row)
            continue
        if loop_key not in latest_by_loop:
            latest_by_loop[loop_key] = row
    missing: list[dict[str, Any]] = []
    for loop_key, row in latest_by_loop.items():
        if pnl(row) is not None:
            continue
        missing.append({
            "loopKey": loop_key,
            "missingReason": str(row.get("selected_profit_missing_reason") or ""),
            "reviewId": row.get("id"),
        })
    for row in unassociated:
        if pnl(row) is not None:
            continue
        missing.append({
            "loopKey": None,
            "unassociated": True,
            "missingReason": str(row.get("selected_profit_missing_reason") or ""),
            "reviewId": row.get("id"),
        })
    return missing


def converge_reviews(
    db: Path,
    runtime_api: str,
    apply: bool,
    hours: int,
    candidate_limit: int,
    max_rounds: int = REVIEW_CONVERGENCE_MAX_ROUNDS,
    manager_api: str | None = None,
) -> dict[str, Any]:
    """Drives the reviews until every route carries an explicit value.

    The loop is bounded and it reports **why** it stopped, so a stalled
    convergence is visible instead of looking like a success.
    """
    rounds: list[dict[str, Any]] = []
    converged = False
    stalled = False
    remaining: list[dict[str, Any]] = []
    for round_index in range(1, max(1, max_rounds) + 1):
        remaining = reviews_missing_a_value(db, hours)
        if not remaining:
            converged = True
            break
        before_keys = {_missing_value_identity(item) for item in remaining}
        result = rereview_missing_pnl(db, runtime_api, apply, hours, candidate_limit,
                                      manager_api=manager_api)
        after = reviews_missing_a_value(db, hours)
        after_keys = {_missing_value_identity(item) for item in after}
        progressed = len(after_keys) < len(before_keys)
        rounds.append({
            "round": round_index,
            "missingBefore": len(before_keys),
            "missingAfter": len(after_keys),
            "progressed": progressed,
            "candidateCount": result.get("candidateCount", 0),
            "result": result,
        })
        if not progressed:
            # The same routes are still missing a value. Another round would
            # ask the runtime the same question and get the same answer, so
            # the convergence is stalled and has to be reported as such.
            stalled = True
            break
    remaining = reviews_missing_a_value(db, hours)
    return {
        "mode": "converge-reviews",
        "generatedAt": now(),
        "apply": apply,
        "dryRun": not apply,
        # A stalled run has **not** converged, even when the loop happened
        # to end with no route left in its work list. The two facts are
        # reported separately, so a stall can never look like a success.
        "converged": bool(converged and not remaining),
        "stalled": stalled,
        "remainingMissingValue": len(remaining),
        "remaining": remaining[:50],
        "rounds": rounds,
        "policy": ("a review without an explicit value is replaced by a fresh review "
                   "until it carries one; a missing value is never read as zero profit"),
    }


# ---------------------------------------------------------------------------
# The broadcast repair
#
# The rule the plan sets is that a failed broadcast is a **defect to repair**,
# not a record to keep. The chain stays authoritative, but a failed attempt
# has to be classified, diagnosed and repaired, and the repair has to be
# driven by the broadcast manager, which owns the attempt table and the
# diagnostics.
#
# The classes are deliberately coarse. The healer does not re-derive the
# Uniswap arithmetic and it does not decide whether a route is worth taking;
# it only decides **what kind of failure** happened, so the right manager can
# be asked to fix it.
# A space, a dash and an underscore are the same separator, so the
# diagnostics and the prose reach the same vocabulary entry.
_SEPARATOR_RE = re.compile(r"[\s_\-]+")


BROADCAST_FAILURE_CLASSES: dict[str, tuple[str, ...]] = {
    # The route could not be quoted: the pool state was missing, stale or
    # unreadable. The pool manager owns that state, so the pool scope has to
    # be repaired and the route reviewed again.
    "pool_state_unavailable": (
        "no_pool_state",
        "pool_state_missing",
        "pool_not_found",
        "unknown_pool",
        "missing_pool",
        "pool_key_unavailable",
        "liquidity_zero",
        "no_liquidity",
        "insufficient_liquidity",
    ),
    # The price moved: the review quoted a market that no longer exists.
    # The review has to run again on the current state.
    "stale_pool_state": (
        "stale",
        "stale_state",
        "state_changed",
        "price_changed",
        "sqrt_price_changed",
        "capacity_changed",
        "expired",
    ),
    # The transaction itself was rejected. The gas, the nonce or the
    # allowance was wrong. A fresh attempt has to be built.
    "transaction_rejected": (
        "revert",
        "execution_reverted",
        "transaction_failed",
        "insufficient_gas",
        "out_of_gas",
        "nonce_too_low",
        "nonce_mismatch",
        "replacement_underpriced",
        "intrinsic_gas_too_low",
        "allowance",
        "insufficient_allowance",
        "unauthorized",
    ),
    # The endpoint was unreachable. Nothing is wrong with the route, so the
    # attempt has to be retried against another endpoint.
    "rpc_unavailable": (
        "rpc_unavailable",
        "timeout",
        "timed_out",
        "connection",
        "network_error",
        "endpoint",
        "rate_limited",
        "too_many_requests",
    ),
    # The route did not pay. This is **not** a defect: the market simply did
    # not offer a profit. It is reported, and it is never "repaired".
    "no_profit": (
        "not_profitable",
        "no_profit",
        "not_profitable_after_costs",
        "below_min_profit",
        "profit_below_floor",
    ),
}


def classify_broadcast_failure(message: Any) -> str:
    """Classifies one broadcast failure message.

    The classification is a **substring match over a fixed vocabulary**. It
    never guesses: an unknown message is reported as ``unknown`` so it is
    visible instead of being silently folded into a known class.

    The message is normalized before the match: the diagnostics arrive in
    snake_case from the Rust side and in prose from the chain, so both
    spellings have to reach the same marker. A space, a dash and an
    underscore are the same separator, and a message is also matched with
    the separators collapsed, so ``out of gas`` and ``out_of_gas`` name the
    same defect.
    """
    text = str(message or "").strip().lower()
    if not text:
        return "unknown"
    collapsed = _SEPARATOR_RE.sub(" ", text)
    for name, markers in BROADCAST_FAILURE_CLASSES.items():
        for marker in markers:
            if marker in text or marker in collapsed:
                return name
            # The marker is also matched with its own separators collapsed,
            # so a prose message matches a snake_case vocabulary entry.
            marker_collapsed = _SEPARATOR_RE.sub(" ", marker)
            if marker_collapsed and marker_collapsed in collapsed:
                return name
    return "unknown"


def broadcast_failure_candidates(
    db: Path,
    hours: int,
    limit: int,
    excluded_attempt_ids: set[str] | None = None,
    manager_api: str | None = None,
) -> list[dict[str, Any]]:
    """Collects the failed broadcast attempts that need a repair.

    Only the attempts that actually **failed** are collected. A pending
    attempt is not a failure: it is a transaction that has not settled yet,
    and repairing it would risk a double spend.

    The attempts are the **Broadcast Manager's** records, so they are read
    through the manager API. The healer never selects from the manager's
    attempt table.
    """
    excluded = excluded_attempt_ids or set()
    published = manager_read_broadcasts(manager_api)
    rows = published.get("attempts") or []
    candidates: list[dict[str, Any]] = []
    for row in rows:
        attempt_id = str(row.get("attemptId") or row.get("attempt_id") or "").strip()
        if not attempt_id or attempt_id in excluded:
            continue
        status = str(row.get("status") or "").strip().lower()
        if status not in ("failed", "failure", "reverted", "error"):
            continue
        evidence_parts = [
            str(row.get(column) or "")
            for column in ("statusReason", "status_reason", "error")
            if row.get(column)
        ]
        for column in ("evidence", "receipt"):
            value = row.get(column)
            if isinstance(value, dict) and value:
                evidence_parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
            elif isinstance(value, str) and value.strip():
                evidence_parts.append(value)
        evidence = " ".join(part for part in evidence_parts if part)
        failure_class = classify_broadcast_failure(evidence or status)
        candidates.append({
            "attemptId": attempt_id,
            "attemptKey": row.get("attemptKey") or row.get("attempt_key"),
            "loopKey": row.get("loopKey") or row.get("loop_key"),
            "status": status,
            "failureClass": failure_class,
            "evidence": evidence[:1000],
            "txHash": row.get("txHash") or row.get("tx_hash"),
            "repairable": failure_class != "unknown",
        })
    return candidates[:limit]


def repair_broadcast_failures(
    db: Path,
    manager_api: str,
    apply: bool,
    hours: int,
    limit: int,
    excluded_attempt_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Repairs the failed broadcast attempts through the broadcast manager.

    The repair is deliberately conservative:

    * a **no_profit** attempt is not a defect. It is counted and left alone.
    * a **pool_state_unavailable** or **stale_pool_state** attempt cannot be
      repaired by the broadcast manager alone: the state has to be fixed
      first. The attempt is therefore reported with the next step, and the
      pool scope repair owns the actual fix.
    * a **transaction_rejected** attempt is finalized as failed with its
      diagnostic, so the failed attempt stops blocking the queue and the next
      fresh review can be broadcast.
    * a **pending** attempt is never touched.

    Nothing is written unless ``apply`` is set, and the whole call is
    idempotent: a second run over the same failed attempt is a no-op.
    """
    candidates = broadcast_failure_candidates(db, hours, limit, excluded_attempt_ids, manager_api)
    outcomes: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for candidate in candidates:
        failure_class = candidate["failureClass"]
        counts[failure_class] = counts.get(failure_class, 0) + 1
        entry = {**candidate}
        if failure_class == "no_profit":
            entry["action"] = "none"
            entry["reason"] = ("the route did not pay; a market outcome is not a defect "
                               "and is never repaired")
            outcomes.append(entry)
            continue
        if failure_class == "unknown":
            entry["action"] = "none"
            entry["reason"] = ("the failure could not be classified; it is reported instead "
                               "of being folded into a known repair")
            outcomes.append(entry)
            continue
        if failure_class in ("pool_state_unavailable", "stale_pool_state"):
            entry["action"] = "defer_to_upstream_repair"
            entry["reason"] = ("the pool state has to be repaired and the route reviewed again "
                               "before another attempt can be broadcast")
            outcomes.append(entry)
            continue
        if failure_class == "rpc_unavailable":
            entry["action"] = "defer_to_runtime_retry"
            entry["reason"] = ("the endpoint was unreachable; the runtime retries against the "
                               "next endpoint, so no attempt is rewritten")
            outcomes.append(entry)
            continue
        # The transaction was rejected: the attempt is terminal. The
        # broadcast manager owns the attempt state, so the finalize call is
        # the only way to close it.
        entry["action"] = "finalize_failed"
        body = {
            "chainId": CHAIN_ID,
            "attempts": [{
                "attemptId": candidate["attemptId"],
                "status": "failed",
                "statusReason": candidate["evidence"][:500] or "broadcast_rejected",
            }],
            "dryRun": not apply,
        }
        response = api_post(manager_api, "/v1/broadcasts/finalize", body)
        entry["finalizeResponse"] = {
            "ok": response_succeeded(response),
            "statusCode": response.get("status"),
        }
        outcomes.append(entry)
    return {
        "mode": "repair-broadcast-failures",
        "generatedAt": now(),
        "apply": apply,
        "dryRun": not apply,
        "candidateCount": len(candidates),
        "failureClasses": counts,
        "results": outcomes,
        "policy": ("a failed broadcast is repaired by the broadcast manager; a market "
                   "outcome is never repaired"),
    }


def tool_entry_failed(entry: dict[str, Any]) -> bool:
    if entry.get("skipped"):
        return False
    result = entry.get("result")
    return isinstance(result, dict) and not result.get("ok", False)


def repair(db: Path, tools: Path, manager_api: str, apply: bool, hours: int) -> dict[str, Any]:
    before, results, dry_run = build_report(db, hours, tools, manager_api), [], not apply
    # The 24h review window currently reads the whole cow_execution_reviews
    # table because the existing scope index puts `category` between the scope
    # columns and `created_at`. The healer owns a read-only connection, so the
    # index has to be created through the pool-manager CLI, which owns the
    # writable schema connection. `--mode check` stays fully read-only.
    results.append(ensure_review_indexes(db, tools, apply))
    token_layer = before["layers"]["tokens"]
    if not token_layer["valid"]:
        token_defect_list = token_layer.get("defects") or []
        targets = resolve_token_targets(token_defect_list)
        if targets:
            # GAP-C: repair the broken symbols one by one.
            results.append(repair_resolve_token(
                db, manager_api, apply, token_defect_list))
        # A defect that lives in the **identity** of an admitted token -- a
        # counterfeit address, an address that is not the official one, or a
        # broken stock provenance -- cannot be fixed by resolving a symbol:
        # the resolver reads the admitted row, it never rewrites it. The
        # batch path is what withdraws a bad admission and re-materializes a
        # token from the chain's own registry.
        batch_only = [item for item in token_defect_list
                      if str(item.get("kind") or "") in BATCH_ONLY_TOKEN_DEFECT_KINDS]
        symbol_less = [item for item in token_defect_list
                       if not item.get("symbol")]
        if batch_only or symbol_less or not targets:
            command = [str(tools / "token-manager.exe"), "materialize-libraries", "--source-db", str(db),
                       "--target-db", str(db), "--registry", str(registry_for_chain(CHAIN_ID)),
                       "--network", NETWORK, "--chain-id", str(CHAIN_ID)]
            if apply:
                command += ["--apply", "--actor", f"arbitrage-healer-{NETWORK}"]
            results.append({"layer": "tokens", "tool": "token-manager",
                            "action": "materialize-libraries",
                            "reason": ("the batch path re-materializes every library "
                                       "from the chain's own registry; it is the only "
                                       "path that can withdraw a bad admission or "
                                       "correct the address of a token"),
                            "batchOnlyDefects": [str(item.get("kind")) for item in batch_only],
                            "symbolLessDefectCount": len(symbol_less),
                            "result": run_tool(command)})
        # A token that still carries no usable symbol or no decimals is
        # **not repairable**: there is no name to give it and no scale to
        # read it with. Such a token is removed from the token table, and
        # the closed loops that named it are removed with it, because a
        # loop that routes through a token the store does not know can
        # never be priced.
        #
        # The prune only ever removes a token whose **essential**
        # metadata is missing. A token that merely lacks a display name
        # or a provenance record stays.
        prune_command = [str(tools / "token-manager.exe"), "prune-incomplete-tokens",
                         "--database", str(db), "--network", NETWORK,
                         "--chain-id", str(CHAIN_ID)]
        if apply:
            prune_command.append("--apply")
        results.append({"layer": "tokens", "tool": "token-manager",
                        "action": "prune-incomplete-tokens",
                        "reason": ("a token without a usable symbol or without "
                                   "decimals cannot be priced or routed through; it "
                                   "is removed together with the candidate rows, the "
                                   "observation rows and the closed loops that named "
                                   "it, so no loop survives that routes through a "
                                   "token the store does not know"),
                        "result": run_tool(prune_command)})
    if not before["layers"]["pools"].get("depth", {}).get("valid", True):
        results.append(repair_pool_depth(db, tools, apply))
    # The baseline audit is the authoritative statement about the store
    # itself: the scope library policy, the migration ledger and the token
    # libraries.  ``repair`` used to reference an undefined name here, so the
    # whole repair path crashed before it could act on a stale scope.
    baseline = audit_database_baseline(db, tools)
    baseline_issues = set(baseline.get("issues") or [])
    results.append({"layer": "databaseBaseline", "tool": "pool-manager",
                    "result": baseline})
    pool_baseline_invalid = any(
        issue.startswith("core_scope_") or issue.startswith("candidate_scope_")
        or issue.startswith("observation_")
        for issue in baseline_issues
    )
    if not before["layers"]["pools"]["valid"] or pool_baseline_invalid:
        command = [str(tools / "pool-manager.exe"), "materialize-scope", "--store", str(db),
                   "--chain-id", str(CHAIN_ID), "--scope", SCOPE, "--apply" if apply else "--dry-run"]
        results.append({"layer": "pools", "tool": "pool-manager", "result": run_tool(command)})
    if before["layers"]["pools"]["valid"] and not before["layers"]["closedLoops"]["valid"]:
        command = [str(tools / "composite-manager.exe"), "materialize-arbitrage", "--db", str(db),
                   "--chain-id", str(CHAIN_ID)] + (["--apply"] if apply else [])
        results.append({"layer": "closedLoops", "tool": "composite-manager", "result": run_tool(command)})
    rows = review_rows(db, hours, 500)
    # The closed-loop catalog is owned by the **closed-loop Manager**, so it
    # is read through the manager API.  The healer never selects from the
    # manager's table: a direct read would bind the healer to the layout it
    # does not own and could show a generation the manager never published.
    known_loops = {
        str(row.get("loopKey") or row.get("loop_key") or "").strip()
        for row in (manager_read_closed_loops(manager_api).get("loops") or [])
    }
    known_loops.discard("")
    hop_pools = closed_loop_hop_pools(db, manager_api)
    matched_rows = []
    for row in rows:
        key = review_loop_key(row)
        if key not in known_loops and hop_pools:
            key = review_loop_key_by_pool(db, row, hop_pools) or key
        if key in known_loops:
            matched_rows.append(row)
    if matched_rows:
        # The newest row of each route wins. A row that carries a measured
        # best trade amount always wins over one that does not, because the
        # measurement is the fact the route is judged by and the Manager row
        # only carries the structure of the review.
        # The route's review is filed against the **freshest measurement**.
        # The legacy row is the measurement, so it is preferred over the
        # Manager row whenever it is at least as fresh: the Manager row only
        # adds the identity and the audit trail, and it carries the very
        # measurement the legacy row holds.
        latest_by_loop = {}
        for row in matched_rows:
            key = review_loop_key(row)
            if key not in known_loops and hop_pools:
                key = review_loop_key_by_pool(db, row, hop_pools) or key
            current = latest_by_loop.get(key)
            if current is None or review_row_is_newer(row, current):
                latest_by_loop[key] = row
        selected_rows = list(latest_by_loop.items())
        evidence = [review_evidence(row, key) for key, row in selected_rows]
        # One closed loop is measured once.  The Review Manager files a
        # review per **loop** and rejects a second evidence block for the
        # same loop, so the evidence has to be deduplicated by its
        # published key.
        #
        # The dedup key is the exact published closed-loop key: the funding
        # anchor plus the directed pool path.  Two different closed loops
        # have two different keys, and neither of them borrows the
        # measurement of the other.
        deduped_evidence: list[dict[str, Any]] = []
        seen_loops: set[str] = set()
        duplicate_loops = 0
        for item in evidence:
            loop_key = str(item.get("loopKey") or "").strip()
            if loop_key in seen_loops:
                duplicate_loops += 1
                continue
            seen_loops.add(loop_key)
            deduped_evidence.append(item)
        evidence = deduped_evidence
        expected_upstream = review_expected_upstream(db, manager_api)
        body = {"chainId": CHAIN_ID, "evidence": evidence, "dryRun": dry_run}
        if expected_upstream:
            body["expectedUpstream"] = expected_upstream
        results.append({"layer": "reviews", "tool": "review-manager-api", "mapped": len(evidence),
            "staleOrUnmatchedSkipped": len(rows) - len(matched_rows),
            "duplicateLoopReviewsCollapsed": len(matched_rows) - len(selected_rows),
            "duplicateLoopsCollapsed": duplicate_loops,
            "missingPnl": sum(item["runArbitrage"]["signedPnl"] is None for item in evidence),
            "expectedUpstream": expected_upstream or None,
            "result": api_post(manager_api, "/v1/reviews/run", body)})
    # The broadcast layer is **delegated**, not skipped. The healer never
    # signs, never invents a nonce and never builds a transaction: a
    # broadcast is only ever produced by the real runtime from a real
    # positive review. What the healer owns is the *repair of a failed
    # attempt*, and that repair is a state transition on an attempt the
    # broadcast manager already owns. The delegate is therefore spelled out
    # here so the report states who acts, instead of hiding the layer.
    results.append({
        "layer": "broadcast",
        "tool": "broadcast-manager-api",
        "delegated": True,
        "skipped": False,
        "actor": "runtime-broadcast-path",
        "repairEndpoint": "POST /v1/broadcasts/finalize",
        "reason": ("广播交易由真实运行时从正净收益复核驱动；编排器只审计真实 attempt "
                   "并对失败尝试做终态化，不伪造 signer/nonce/rpc。"),
    })
    return {"mode": "repair", "generatedAt": now(), "apply": apply, "dryRun": dry_run,
            "before": before, "results": results,
            "after": build_report(db, hours, tools, manager_api) if apply else None}


def ensure_review_indexes(db: Path, tools: Path, apply: bool) -> dict[str, Any]:
    """Ask the pool-manager to create the review-window index idempotently."""
    command = [str(tools / "pool-manager.exe"), "ensure-indexes", "--store", str(db),
               "--chain-id", str(CHAIN_ID)]
    if apply:
        command.append("--apply")
    result = run_tool(command)
    return {"layer": "reviewIndexes", "tool": "pool-manager", "command": " ".join(command),
            "skipped": not apply,
            "reason": None if apply else "read-only mode does not write schema indexes",
            "result": result}


def repair_resolve_token(db: Path, manager_api: str, apply: bool,
                          defects: list[dict[str, Any]]) -> dict[str, Any]:
    """Repair the broken token symbols one by one.

    The batch path (``materialize-libraries``) rewrites all four libraries.
    That is the wrong tool when exactly one symbol is broken: it is slow, it
    rewrites rows that are already correct, and it cannot tell the operator
    which symbol was actually repaired.

    ``POST /v1/tokens/resolve`` resolves exactly one symbol and returns the
    authoritative token record, so the repair is targeted and the result is
    auditable per symbol.

    Three defect kinds are deliberately **not** routed here, because the
    defect lives in the identity of the admitted row and the resolver only
    reads that row:

    * a **counterfeit admission** cannot be repaired by resolving its
      symbol -- the address itself is wrong, so the resolution would return
      the same counterfeit. It has to be withdrawn by the batch path.
    * a **funding address mismatch** is the same case: the official address
      is the identity of the token, so the resolution returns the very
      address that has to be corrected. The batch path re-materializes the
      token from the chain's own allowlist.
    * a **broken stock provenance** is likewise a property of the admitted
      row, not of the symbol.

    A **symbol-less** token cannot be resolved by symbol at all. It has to
    be removed or re-admitted with its symbol.

    All of them are reported as blocked so the operator sees why they were
    not silently skipped.
    """
    targets = resolve_token_targets(defects)
    blocked = [{
        "kind": item.get("kind"),
        "symbol": item.get("symbol"),
        "address": item.get("address"),
        "reason": item.get("reason"),
        "repairPath": "materialize-libraries",
    } for item in defects
        if not item.get("symbol")
        or str(item.get("kind") or "") in BATCH_ONLY_TOKEN_DEFECT_KINDS]
    results: list[dict[str, Any]] = []
    for target in targets:
        body = {"chainId": CHAIN_ID, "symbol": target["symbol"]}
        if apply:
            body["dryRun"] = False
        else:
            body["dryRun"] = True
        response = api_post(manager_api, "/v1/tokens/resolve", body)
        results.append({
            "symbol": target["symbol"],
            "defectKinds": target["defectKinds"],
            "addresses": target["addresses"],
            "result": response,
            "ok": bool(response.get("ok")),
        })
    return {
        "layer": "tokens",
        "tool": "manager-api",
        "action": "resolve_token",
        "endpoint": "POST /v1/tokens/resolve",
        "apply": apply,
        "dryRun": not apply,
        "targetCount": len(targets),
        "targets": targets,
        "results": results,
        "resolvedCount": sum(1 for item in results if item.get("ok")),
        "failedCount": sum(1 for item in results if not item.get("ok")),
        "blockedCount": len(blocked),
        "blocked": blocked,
        "reason": ("every resolvable broken symbol is repaired on its own through the "
                   "token manager; a counterfeit admission or a symbol-less token is "
                   "reported as blocked because it cannot be repaired by resolving a "
                   "symbol"),
    }


def repair_pool_depth(db: Path, tools: Path, apply: bool) -> dict[str, Any]:
    """Replace shallow candidate pools with depth-qualified ones.

    Shallow pools can only produce dust-sized best trade amounts whose profit
    never covers gas. The pool manager owns the candidate pool set, so the
    healer asks it to audit, drop the ineligible pools and re-select eligible
    replacements from the full exchange pool table.
    """
    steps: list[dict[str, Any]] = []
    # The store has to be able to hold the measured capacity before anything
    # can be measured into it. A store created before the capacity columns
    # existed keeps its old shape, because SQLite never alters an existing
    # table, so the depth pass would silently store nothing and the kernel
    # would have no admission input for any pool.
    schema_command = [str(tools / "pool-manager.exe"), "ensure-depth-schema",
                      "--store", str(db), "--chain-id", str(CHAIN_ID)]
    if apply:
        schema_command.append("--apply")
        schema = run_tool(schema_command)
        steps.append({"step": "ensure-depth-schema",
                      "reason": "the store must be able to hold the measured capacity",
                      "result": schema})
    else:
        # ``ensure-depth-schema`` only ever **adds** columns, so it accepts
        # ``--apply`` and nothing else. Passing ``--dry-run`` made the
        # command fail with a usage error, which the report recorded as a
        # failed step even though the dry run was not supposed to write
        # anything.
        #
        # A dry run therefore **inspects** the schema instead of running the
        # writing command. The inspection is read-only: the store is opened
        # by the healer's own read-only helper, so the dry run cannot change
        # anything even when a column is missing.
        missing = missing_depth_capacity_columns(db)
        steps.append({"step": "ensure-depth-schema",
                      "reason": "the store must be able to hold the measured capacity",
                      "dryRun": True,
                      "missingColumns": missing,
                      "skippedBecauseSchemaPresent": not missing,
                      "result": {
                          "ok": True,
                          "returncode": 0,
                          "stdout": json.dumps({"missingColumns": missing,
                                                "store": str(db)}),
                          "stderr": "",
                          "note": ("a dry run never changes the store; the depth "
                                   "schema was inspected instead of written"),
                      }})
    audit = run_tool([str(tools / "pool-manager.exe"), "audit", "--store", str(db),
                      "--chain-id", str(CHAIN_ID)])
    steps.append({"step": "audit", "result": audit})
    audit_payload: dict[str, Any] = {}
    if audit.get("ok"):
        try:
            audit_payload = json.loads(audit.get("stdout") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            audit_payload = {}
    ineligible = list(audit_payload.get("ineligiblePoolIds") or [])
    cleanup_command = [str(tools / "pool-manager.exe"), "cleanup", "--store", str(db),
                       "--chain-id", str(CHAIN_ID)]
    if not apply:
        cleanup_command.append("--dry-run")
    else:
        # ``pool-manager cleanup`` runs the read-only audit unless ``--apply``
        # is given. Without the flag the healer only *looked* at the ineligible
        # pools and never removed them, so the shallow pools survived every
        # repair round and the L2 layer could never converge.
        cleanup_command.append("--apply")
    cleanup = run_tool(cleanup_command)
    steps.append({"step": "cleanup", "ineligiblePoolCount": len(ineligible),
                  "affectedClosedLoopCount": audit_payload.get("affectedClosedLoopCount"),
                  "result": cleanup})
    replacement = pool_depth_replacement_report(db)
    replacements = replacement["keys"]
    # GAP-D: a pool whose depth **reading** is broken has to be
    # re-measured, not replaced. Swapping a pool because its stored TVL is
    # nonsense removes a pool that may be perfectly fine. The depth
    # refresh re-reads the chain and repairs the measurement.
    depth = pool_depth_audit(db)
    # The depth pass has to run when a reading is untrustworthy **or** when a
    # pool was never measured at all. An unmeasured pool has no admission
    # input in the kernel, so the candidate set cannot converge while one is
    # still outstanding.
    remeasure_required = (
        int(depth.get("badReadingPoolCount") or 0) > 0
        or int(depth.get("unmeasuredPoolCount") or 0) > 0
        or int(depth.get("capacityUnknownPoolCount") or 0) > 0
    )
    if remeasure_required:
        depth_command = [str(tools / "pool-manager.exe"), "depth", "--store", str(db),
                         "--chain-id", str(CHAIN_ID), "--depth-scope", "candidate"]
        if apply:
            depth_command.append("--apply")
        steps.append({"step": "remeasure-depth", "reason": "depth_reading_invalid",
                      "badReadingPoolCount": depth.get("badReadingPoolCount"),
                      "unmeasuredPoolCount": depth.get("unmeasuredPoolCount"),
                      "capacityUnknownPoolCount": depth.get("capacityUnknownPoolCount"),
                      "result": run_tool(depth_command)})
    refresh_results = []
    for key_id in replacements:
        command = [str(tools / "pool-manager.exe"), "refresh-key", "--store", str(db),
                   "--chain-id", str(CHAIN_ID), "--key-id", key_id,
                   "--pool-group", SCOPE, "--limit", "50"]
        if apply:
            command.append("--apply")
        refresh_results.append({"keyId": key_id, "result": run_tool(command)})
    steps.append({"step": "refresh-key", "keyCount": len(replacements),
                  "results": refresh_results})
    steps.append({"step": "replacement-assessment", **replacement})
    if replacement["status"] != "replacement_available":
        steps.append({"step": "pool-swap-blocked", "blocked": True,
                      "code": POOL_DEPTH_INSUFFICIENT_CODE,
                      "classification": depth.get("classification"),
                      "repairPath": depth.get("repairPath"),
                      "reason": replacement["reason"],
                      "action": ("pool swap cannot restore a meaningful best trade amount; "
                                 "the token pair must be dropped or a deeper venue added")})
    return {"layer": "pools", "tool": "pool-manager", "action": "repair-pool-depth",
            "apply": apply, "dryRun": not apply, "steps": steps,
            "replacement": replacement,
            "policy": "candidate pools must keep enough on-chain depth to support a "
                      "best trade amount whose profit covers gas"}


def pool_depth_replacement_keys(db: Path) -> list[str]:
    """Return token pairs that can be re-selected from depth-qualified pools."""
    return pool_depth_replacement_report(db)["keys"]


def pool_depth_replacement_report(db: Path) -> dict[str, Any]:
    """Explain whether depth-qualified replacement pools actually exist.

    An empty replacement list is ambiguous: it can mean the healer forgot to
    look, or it can mean the whole market only contains pools that are already
    admitted (or none at all). This report distinguishes those cases so the
    orchestrator can state that the market depth is exhausted rather than
    silently doing nothing.

    The admission criterion is the **pool manager's own depth verdict**, which
    is derived from the on-chain capacity of the pool. A USD TVL band is not
    a valid substitute: the USD valuation is denominated in the chain's own
    quote asset, so on a chain whose quote is not the USD (the testnet values
    are denominated in native ETH) the band rejects every pool and the healer
    would report an exhausted market while hundreds of depth-qualified pools
    are waiting in the exchange table. Re-deriving the criterion here is also
    what let the two components drift apart, so the verdict set is shared
    with the depth audit.
    """
    adequate = ",".join(f"'{status}'" for status in sorted(ADEQUATE_DEPTH_STATUSES))
    eligible = all_rows(db,
        "SELECT COUNT(DISTINCT e.token0 || ':' || e.token1) pair_count "
        "FROM v4_exchange_pools e "
        "JOIN v4_exchange_pool_liquidity_snapshots s "
        "  ON s.network=e.network AND s.chain_id=e.chain_id AND s.protocol=e.protocol "
        " AND s.pool_id=e.pool_id "
        "WHERE e.network=? AND e.chain_id=? AND e.protocol='uniswap_v4' "
        "AND e.fee>0 AND e.fee<=? AND e.dynamic_fee=0 AND e.special_fee=0 "
        "AND lower(COALESCE(e.hooks,'')) IN ('', '0x0000000000000000000000000000000000000000') "
        "AND s.has_liquidity=1 "
        f"AND COALESCE(s.depth_status,'') IN ({adequate}) ",
        (NETWORK, CHAIN_ID, MAX_CANDIDATE_FIXED_FEE))
    # A pool can be rejected by two independent policies: the shared fixed-fee
    # ceiling, or the depth band. The two produce different operational
    # consequences, so they must not be reported as one status: a fee
    # rejection means the market has pools but none of them is admissible at
    # all, while a depth rejection means the admissible pools are too thin.
    capacity_rows = all_rows(db,
        "SELECT COUNT(DISTINCT e.token0 || ':' || e.token1) pair_count "
        "FROM v4_exchange_pools e "
        "WHERE e.network=? AND e.chain_id=? AND e.protocol='uniswap_v4' "
        "AND e.fee>0 AND e.fee<=? AND e.dynamic_fee=0 AND e.special_fee=0 "
        "AND lower(COALESCE(e.hooks,'')) IN ('', '0x0000000000000000000000000000000000000000') "
        "AND EXISTS (SELECT 1 FROM v4_exchange_pool_liquidity_snapshots s "
        "  WHERE s.network=e.network AND s.chain_id=e.chain_id AND s.protocol=e.protocol "
        "    AND s.pool_id=e.pool_id AND s.has_liquidity=1)",
        (NETWORK, CHAIN_ID, MAX_CANDIDATE_FIXED_FEE))
    capacity_pair_count = int((capacity_rows[0] if capacity_rows else {}).get("pair_count") or 0)
    eligible_pair_count = int((eligible[0] if eligible else {}).get("pair_count") or 0)
    keys: list[str] = []
    rows = all_rows(db, "SELECT DISTINCT e.token0 token0, e.token1 token1 "
        "FROM v4_exchange_pools e "
        "JOIN v4_exchange_pool_liquidity_snapshots s "
        "  ON s.network=e.network AND s.chain_id=e.chain_id AND s.protocol=e.protocol "
        " AND s.pool_id=e.pool_id "
        "WHERE e.network=? AND e.chain_id=? AND e.protocol='uniswap_v4' "
        "AND e.fee>0 AND e.fee<=? AND e.dynamic_fee=0 AND e.special_fee=0 "
        "AND lower(COALESCE(e.hooks,'')) IN ('', '0x0000000000000000000000000000000000000000') "
        "AND s.has_liquidity=1 "
        f"AND COALESCE(s.depth_status,'') IN ({adequate}) "
        "AND (SELECT COUNT(*) FROM dex_candidate_pools d "
        "     WHERE d.network=e.network AND d.chain_id=e.chain_id AND d.pool_id=e.pool_id)=0 "
        "LIMIT 20",
        (NETWORK, CHAIN_ID, MAX_CANDIDATE_FIXED_FEE))
    for row in rows:
        left, right = str(row.get("token0") or ""), str(row.get("token1") or "")
        if len(left) == 42 and len(right) == 42 and left != right:
            keys.append(f"{left.lower()}:{right.lower()}")
    if keys:
        status = "replacement_available"
        reason = "depth-qualified pools outside the current candidate set can replace shallow pools"
    elif eligible_pair_count > 0:
        status = "market_depth_exhausted"
        reason = ("every depth-qualified pool is already admitted as a candidate; "
                  "the shallow pools have no market replacement")
    elif capacity_pair_count > 0:
        # Admissible pools exist, but every one of them is too thin. The
        # market cannot be repaired by swapping a pool.
        status = "no_depth_qualified_pool_exists"
        reason = ("the market contains capacity-qualified pools, but none of them reaches "
                  "the minimum depth band; no pool swap can restore a meaningful best "
                  "trade amount")
    else:
        # No pool is admissible at all: the shared fixed-fee ceiling or the
        # hook rule excluded every pool in the market.
        status = "no_capacity_qualified_pool_exists"
        reason = ("the whole market contains no pool that satisfies the shared fixed-fee "
                  "ceiling and the hook rule; the depth band is therefore unreachable too")
    return {
        "status": status,
        "reason": reason,
        "code": POOL_DEPTH_INSUFFICIENT_CODE if status != "replacement_available" else None,
        "depthCriterion": "pool_manager_capacity_verdict",
        "adequateDepthStatuses": sorted(ADEQUATE_DEPTH_STATUSES),
        "depthQualifiedPairCount": eligible_pair_count,
        "replacementKeyCount": len(keys),
        "keys": keys,
    }


def save(prefix: str, report: dict[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{prefix}_chain{CHAIN_ID}_{stamp()}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def supervise(
    db: Path,
    tools: Path,
    manager_api: str,
    runtime_api: str,
    apply: bool,
    hours: int,
    interval: int,
    max_samples: int | None,
    candidate_limit: int,
    runtime_port: int,
    startup_timeout: int,
    restart_limit: int,
    leave_runtime_running: bool,
) -> Path:
    SUPERVISOR_DIR.mkdir(parents=True, exist_ok=True)
    session = stamp()
    journal_path = SUPERVISOR_DIR / f"supervisor_{session}.jsonl"
    runtime_log_dir = SUPERVISOR_DIR / f"runtime_{session}"
    process: subprocess.Popen[Any] | None = None
    runtime_owned = False
    launches = 0
    sample = 0
    stopping = False
    requested_review_loops: set[str] = set()
    rereviewed_positive_loops: set[str] = set()
    rereviewed_missing_pnl_loops: set[str] = set()
    # The attempts this supervisor already finalized. The set makes the
    # broadcast repair idempotent across cycles: an attempt that was closed
    # in an earlier cycle is not closed again, and it never blocks the
    # queue on a later cycle.
    repaired_attempt_ids: set[str] = set()
    # TASK-130: the stable-achievement criterion is accumulated across the
    # healing cycles. A cycle that breaks any condition resets the run, so
    # the verdict can never be based on a single lucky sample.
    stability = StabilityTracker()

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    previous_handlers = {}
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signal_name] = signal.getsignal(signal_name)
        signal.signal(signal_name, request_stop)

    def append_event(event: dict[str, Any]) -> None:
        event.setdefault("timestamp", now())
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    try:
        initial_health = runtime_business_health(runtime_api)
        if initial_health.get("ok"):
            append_event({"event": "runtime_adopted", "runtimeApi": runtime_api, "health": initial_health})
        else:
            process, log_paths = start_runtime(runtime_port, runtime_log_dir)
            runtime_owned = True
            launches += 1
            append_event({"event": "runtime_started", "pid": process.pid, "logs": log_paths})
            readiness = wait_for_runtime(runtime_api, process, startup_timeout)
            append_event({"event": "runtime_readiness", "pid": process.pid, "result": readiness})
            if not readiness.get("ok"):
                raise RuntimeError(f"mainnet runtime failed readiness: {readiness}")

        while not stopping and (max_samples is None or sample < max_samples):
            health = runtime_business_health(runtime_api)
            if not health.get("ok"):
                append_event({"event": "runtime_unhealthy", "health": health,
                              "returncode": process.poll() if process is not None else None})
                if launches >= max(1, restart_limit):
                    append_event({"event": "runtime_restart_exhausted", "launches": launches})
                    break
                stop_runtime(process)
                process, log_paths = start_runtime(runtime_port, runtime_log_dir)
                runtime_owned = True
                launches += 1
                readiness = wait_for_runtime(runtime_api, process, startup_timeout)
                append_event({"event": "runtime_restarted", "pid": process.pid, "logs": log_paths,
                              "launch": launches, "readiness": readiness})
                if not readiness.get("ok"):
                    if launches >= max(1, restart_limit):
                        break
                    continue

            try:
                before = build_report(db, hours, tools, manager_api)
                actions = healing_actions(before)
                outcomes: list[dict[str, Any]] = []
                # GAP-E: the upstream gate covers the layers that must be
                # sound before a review can be trusted. The broadcast layer
                # is the convergence target, never the gate opener.
                upstream_valid = upstream_layers_valid(before)
                # GAP-C: the token layer is the first layer of the cascade,
                # so a broken token has to be repaired before anything
                # downstream is even looked at.
                token_broken = not (before["layers"].get("tokens") or {}).get("valid")
                if not upstream_valid or token_broken or "repair_reviews" in actions \
                        or "resolve_token" in actions:
                    outcomes.append({"action": "manager_repair",
                                     "result": repair(db, tools, manager_api, apply, hours)})
                if upstream_valid:
                    # The convergence replaces the single pass: the reviews
                    # are driven until every route carries an explicit
                    # value, or until the loop is provably stalled.
                    missing_pnl_result = converge_reviews(
                        db, runtime_api, apply, hours, candidate_limit,
                        manager_api=manager_api)
                    if missing_pnl_result["remainingMissingValue"] == 0 and rereviewed_missing_pnl_loops:
                        rereviewed_missing_pnl_loops.clear()
                        missing_pnl_result = converge_reviews(
                            db, runtime_api, apply, hours, candidate_limit,
                            manager_api=manager_api)
                    rereviewed_missing_pnl_loops.update(
                        str(item["loopKey"])
                        for item in missing_pnl_result.get("remaining", [])
                        if item.get("loopKey")
                    )
                    outcomes.append({"action": "converge_reviews",
                                     "result": missing_pnl_result})
                    fill_result = fill_missing_reviews(
                        db, runtime_api, apply, candidate_limit, requested_review_loops,
                        manager_api=manager_api)
                    if fill_result["candidateCount"] == 0 and requested_review_loops:
                        requested_review_loops.clear()
                        fill_result = fill_missing_reviews(
                            db, runtime_api, apply, candidate_limit, requested_review_loops,
                            manager_api=manager_api)
                    requested_review_loops.update(
                        str(item["loopKey"])
                        for item in fill_result.get("results", [])
                        if item.get("loopKey")
                    )
                    outcomes.append({"action": "fill_current_loop_reviews", "result": fill_result})
                    for key_id in pool_repair_keys(fill_result):
                        command = [
                            str(tools / "pool-manager.exe"), "refresh-key",
                            "--store", str(db), "--chain-id", str(CHAIN_ID),
                            "--key-id", key_id, "--pool-group", "PAIR", "--limit", "5",
                        ]
                        if apply:
                            command.append("--apply")
                        outcomes.append({
                            "action": "repair_candidate_pool_key",
                            "keyId": key_id,
                            "tool": "pool-manager",
                            "result": run_tool(command),
                        })
                    rereview_result = rereview_historical_positive(
                        db, runtime_api, apply, candidate_limit, rereviewed_positive_loops,
                        manager_api=manager_api)
                    if rereview_result["candidateCount"] == 0 and rereviewed_positive_loops:
                        rereviewed_positive_loops.clear()
                        rereview_result = rereview_historical_positive(
                            db, runtime_api, apply, candidate_limit, rereviewed_positive_loops,
                            manager_api=manager_api)
                    rereviewed_positive_loops.update(
                        str(item["loopKey"])
                        for item in rereview_result.get("results", [])
                        if item.get("loopKey")
                    )
                    outcomes.append({"action": "rereview_historical_positive_candidates",
                                     "result": rereview_result})
                    # GAP-A: the broadcast repair used to be dead code --
                    # `repair_broadcast_failures` was fully implemented but
                    # never called from this loop. It now runs, but only
                    # once the review layer above it is sound and produced a
                    # positive net result, so a failed attempt is never
                    # "repaired" while the real cause is still upstream.
                    if "repair_broadcast_failures" in actions:
                        broadcast_repair = repair_broadcast_failures(
                            db, manager_api, apply, hours, candidate_limit,
                            excluded_attempt_ids=repaired_attempt_ids)
                        outcomes.append({"action": "repair_broadcast_failures",
                                         "result": broadcast_repair})
                        for entry in broadcast_repair.get("results") or []:
                            attempt_id = str(entry.get("attemptId") or "")
                            if attempt_id and entry.get("action") == "finalize_failed":
                                repaired_attempt_ids.add(attempt_id)
                after = build_report(db, hours, tools, manager_api)
                after_status = cascade_layer_status(after)
                before_status = cascade_layer_status(before)
                stability_state = stability.observe(after)
                append_event({
                    "event": "healing_cycle",
                    "stability": stability_state,
                    "stable": stability_state["stable"],
                    "sample": sample,
                    "apply": apply,
                    "runtimePid": process.pid if process is not None and process.poll() is None else None,
                    "runtimeOwned": runtime_owned,
                    "actions": actions,
                    "before": before,
                    "outcomes": outcomes,
                    "after": after,
                    "cascadeBefore": before_status,
                    "cascadeAfter": after_status,
                    "broadcastPolicy": "fresh_positive_review_only",
                    "manualBroadcastInvoked": False,
                })
                print(
                    f"[{after['generatedAt']}] sample={sample} actions={','.join(actions)} "
                    f"blockers={','.join(after['blockers']) or 'none'} "
                    f"positive={after['layers']['reviews']['legacy']['stats']['positive']} "
                    f"broadcasts={after['layers']['broadcast']['legacy']['total']} "
                    f"stableWindows={stability_state['consecutiveWindows']}"
                    f"/{stability_state['requiredWindows']} "
                    f"stable={stability_state['stable']}",
                    flush=True,
                )
            except Exception as error:
                append_event({
                    "event": "healing_cycle_error",
                    "sample": sample,
                    "errorType": type(error).__name__,
                    "error": str(error),
                })
                print(
                    f"[{now()}] sample={sample} cycle_error={type(error).__name__}: {error}",
                    flush=True,
                )
            sample += 1
            if not stopping and (max_samples is None or sample < max_samples):
                deadline = time.monotonic() + max(1, interval)
                while not stopping and time.monotonic() < deadline:
                    time.sleep(max(0.01, min(1, deadline - time.monotonic())))
    finally:
        if runtime_owned and not leave_runtime_running:
            stop_runtime(process)
            append_event({"event": "runtime_stopped", "pid": process.pid if process is not None else None})
        for signal_name, handler in previous_handlers.items():
            signal.signal(signal_name, handler)
    return journal_path


def watch(db: Path, hours: int, interval: int, max_samples: int | None,
          tools: Path | None = None, manager_api: str | None = None) -> Path:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    path, sample = WATCH_DIR / f"watch_{stamp()}.jsonl", 0
    # TASK-130: the watch loop is where the stable-achievement criterion is
    # accumulated. A single healthy sample is not stability, so the tracker
    # has to survive across samples and reset whenever a window breaks.
    tracker = StabilityTracker()
    while max_samples is None or sample < max_samples:
        report = build_report(db, hours, tools, manager_api)
        stability = tracker.observe(report)
        snapshot = {"timestamp": report["generatedAt"], "blockers": report["blockers"],
            "safeToAutoBroadcast": report["safeToAutoBroadcast"],
            "reviewPnl": report["layers"]["reviews"]["legacy"]["stats"],
            "broadcast": report["layers"]["broadcast"], "closedLoops": report["layers"]["closedLoops"],
            "stability": stability}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot, ensure_ascii=False, default=str) + "\n")
        stats = snapshot["reviewPnl"]
        print(f"[{snapshot['timestamp']}] sample={sample} sum={stats['sum']} positive={stats['positive']} "
              f"missing={stats['missingValue']} blockers={','.join(snapshot['blockers']) or 'none'} "
              f"stableWindows={stability['consecutiveWindows']}/{stability['requiredWindows']} "
              f"stable={stability['stable']}")
        sample += 1
        if max_samples is None or sample < max_samples:
            time.sleep(interval)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Robinhood 4663 主网套利闭环自愈编排器")
    parser.add_argument(
        "--mode",
        choices=(
            "check", "repair", "watch", "rereview",
            "fill-reviews", "fill-missing-pnl", "rematerialize-reviews",
            "supervise",
        ),
        default="check",
    )
    parser.add_argument("--chain-id", type=int, default=4663)
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--tools", type=Path, default=DEFAULT_TOOLS)
    parser.add_argument("--manager-api", default=os.getenv("MANAGER_API_BASE", "http://127.0.0.1:8091"))
    parser.add_argument("--runtime-api", default=os.getenv("MAINNET_RUNTIME_API", "http://127.0.0.1:5000"))
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--max-samples", type=int)
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument("--runtime-port", type=int, default=5000)
    parser.add_argument("--startup-timeout", type=int, default=120)
    parser.add_argument("--restart-limit", type=int, default=3)
    parser.add_argument("--observation-hours", type=float, default=0)
    parser.add_argument("--pnl-interval-seconds", type=float, default=300)
    parser.add_argument("--leave-runtime-running", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        activate_scope(args.chain_id)
    except ValueError as error:
        parser.error(str(error))
    if args.database is None:
        args.database = database_for_chain(args.chain_id)
    if not args.database.exists():
        parser.error(f"database does not exist: {args.database}")
    # Each chain owns exactly one SQLite database file. A request that points
    # at another chain's file is refused here rather than silently auditing
    # the wrong chain.
    try:
        args.database = assert_chain_database(args.database, args.chain_id)
    except ValueError as error:
        parser.error(str(error))
    if args.mode == "check":
        report = build_report(args.database, args.window_hours, args.tools, args.manager_api)
        print_report(report)
        print(f"report={save('mainnet_arbitrage_healer_check', report)}")
    elif args.mode == "repair":
        report = repair(args.database, args.tools, args.manager_api, args.apply, args.window_hours)
        print(f"apply={args.apply} results={len(report['results'])} "
              f"report={save('mainnet_arbitrage_healer_repair', report)}")
    elif args.mode == "watch":
        print(f"watch={watch(args.database, args.window_hours, args.interval, args.max_samples, args.tools, args.manager_api)}")
    elif args.mode == "rereview":
        report = rereview_historical_positive(
            args.database, args.runtime_api, args.apply, args.candidate_limit,
            manager_api=args.manager_api)
        print(f"apply={args.apply} candidates={report['candidateCount']} "
              f"report={save('mainnet_arbitrage_healer_rereview', report)}")
    elif args.mode == "fill-reviews":
        report = fill_missing_reviews(
            args.database, args.runtime_api, args.apply, args.candidate_limit,
            manager_api=args.manager_api)
        print(f"apply={args.apply} candidates={report['candidateCount']} "
              f"report={save('mainnet_arbitrage_healer_fill_reviews', report)}")
    elif args.mode == "fill-missing-pnl":
        # A review that carries no explicit profit value is a measurement
        # that was never completed. It is replaced by a fresh live review
        # until it carries one -- never by a zero.
        report = rereview_missing_pnl(
            args.database, args.runtime_api, args.apply,
            args.window_hours, args.candidate_limit,
            manager_api=args.manager_api)
        print(f"apply={args.apply} candidates={report['candidateCount']} "
              f"report={save('mainnet_arbitrage_healer_fill_missing_pnl', report)}")
    elif args.mode == "rematerialize-reviews":
        # The Manager library replaces its whole review set with the evidence
        # it receives.  A row that was filed before the best-trade-amount
        # block was carried across keeps an empty static preview, so the
        # stored reviews are re-filed with the measurement restored.
        report = rematerialize_reviews(
            args.database, args.manager_api, args.apply)
        print(f"apply={args.apply} evidence={report['evidenceCount']} "
              f"report={save('mainnet_arbitrage_healer_rematerialize_reviews', report)}")
    else:
        path = supervise(
            args.database, args.tools, args.manager_api, args.runtime_api, args.apply,
            args.window_hours, args.interval, args.max_samples, args.candidate_limit,
            args.runtime_port, args.startup_timeout, args.restart_limit,
            args.leave_runtime_running,
            args.observation_hours, args.pnl_interval_seconds,
        )
        print(f"supervisor={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

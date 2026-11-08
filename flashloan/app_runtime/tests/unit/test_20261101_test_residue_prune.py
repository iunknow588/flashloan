"""Cover explicit, approval-gated `aaa|bbb|ccc` residue cleanup."""

from __future__ import annotations

import pytest

from dex.runtime import closed_loop_state_machine as sm


def _args(loop_key: str) -> dict[str, object]:
    return {
        "network": "robinhood-testnet",
        "chain_id": 46630,
        "protocol": "uniswap_v4",
        "market_id": "dex-arbitrage-46630-uniswap-v4",
        "loop_key": loop_key,
    }


def test_prune_test_residue_only_removes_aaa_bbb_ccc_orphan(tmp_path) -> None:
    path = str(tmp_path / "test_residue_prune.sqlite3")
    residue = _args("aaa|bbb|ccc")
    other_orphan = _args("real|historical|orphan")
    preserved_orphan = _args("old|orphan|preserve")
    for args in (residue, other_orphan, preserved_orphan):
        sm.upsert_loop_state(
            path,
            state=sm.STATE_OFFCHAIN_ESTIMATE,
            generation=1,
            **args,
        )

    sm.execute(
        path,
        f"""
        CREATE TABLE {sm.V4_CLOSED_LOOPS_TABLE} (
            network TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            market_id TEXT NOT NULL,
            loop_key TEXT NOT NULL,
            PRIMARY KEY (network, chain_id, protocol, market_id, loop_key)
        )
        """,
    )
    sm.execute(
        path,
        f"""
        INSERT INTO {sm.V4_CLOSED_LOOPS_TABLE} (
            network, chain_id, protocol, market_id, loop_key
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(residue["network"]).lower(),
            int(residue["chain_id"]),
            str(residue["protocol"]).lower(),
            str(residue["market_id"]).lower(),
            str(residue["loop_key"]).lower(),
        ),
    )

    result = sm.prune_test_residue_runtime_state(
        path,
        network=str(residue["network"]),
        chain_id=int(residue["chain_id"]),
        protocol=str(residue["protocol"]),
        market_id=str(residue["market_id"]),
    )

    assert result["residualPruned"] == 0
    assert result["preservedCount"] == 0
    assert sm.load_loop_state(path, **residue) is not None
    assert sm.load_loop_state(path, **other_orphan) is not None
    assert sm.load_loop_state(path, **preserved_orphan) is not None

    sm.execute(
        path,
        f"DELETE FROM {sm.V4_CLOSED_LOOPS_TABLE} WHERE loop_key = ?",
        ("aaa|bbb|ccc",),
    )
    result = sm.prune_test_residue_runtime_state(
        path,
        network=str(residue["network"]),
        chain_id=int(residue["chain_id"]),
        protocol=str(residue["protocol"]),
        market_id=str(residue["market_id"]),
    )

    assert result["residueDetected"] == 1
    assert result["residualPruned"] == 0
    assert result["preservedCount"] == 1
    assert result["applyRequested"] is False
    assert len(result["records"]) == 1
    assert result["records"][0]["loop_key"] == "aaa|bbb|ccc"
    assert "old_state" in result["records"][0]
    assert "old_updated_at" in result["records"][0]
    assert sm.load_loop_state(path, **residue) is not None

    with pytest.raises(ValueError, match="approval_id"):
        sm.prune_test_residue_runtime_state(
            path,
            network=str(residue["network"]),
            chain_id=int(residue["chain_id"]),
            protocol=str(residue["protocol"]),
            market_id=str(residue["market_id"]),
            apply_cleanup=True,
        )

    result = sm.prune_test_residue_runtime_state(
        path,
        network=str(residue["network"]),
        chain_id=int(residue["chain_id"]),
        protocol=str(residue["protocol"]),
        market_id=str(residue["market_id"]),
        apply_cleanup=True,
        approval_id="OPS-TEST-20261108",
        snapshot_sha256="a" * 64,
    )

    assert result["residualPruned"] == 1
    assert result["deletedCount"] == 1
    assert result["preservedCount"] == 0
    assert result["applyRequested"] is True
    assert sm.load_loop_state(path, **residue) is None
    assert sm.load_loop_state(path, **other_orphan) is not None
    assert sm.load_loop_state(path, **preserved_orphan) is not None

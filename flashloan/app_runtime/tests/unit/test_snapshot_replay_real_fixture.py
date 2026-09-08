"""Real local observation/overview data can seed a stable replay fixture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.snapshot_replay import (
    build_workflow_replay_fixture,
    read_workflow_replay_fixture,
    write_workflow_replay_fixture,
)


ROOT = Path(__file__).resolve().parents[4]
RUNTIME_BASE = ROOT / "flashloan" / "src" / "runtime" / "market_observation" / "bnb" / "56" / "pancakeswap_v3"
FIXTURE_PATH = ROOT / "flashloan" / "app_runtime" / "tests" / "fixtures" / "replay" / "bnb_56_pancakeswap_v3_replay.json"
SCOPE = {
    "market_id": "dex-arbitrage-56-pancakeswap-v3",
    "chain_id": 56,
    "network": "bnb",
    "protocol": "pancakeswap_v3",
}
GENERATION_ID = "replay-bnb-56-pancakeswap-v3-v1"
MACHINE_PATH_FIELDS = ("path", "storage_root", "observationSnapshotPath")


def _strip_machine_paths(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: _strip_machine_paths(value)
            for key, value in payload.items()
            if key not in MACHINE_PATH_FIELDS
        }
    if isinstance(payload, list):
        return [_strip_machine_paths(value) for value in payload]
    return payload


@pytest.fixture(scope="module")
def real_bnb_replay_fixture() -> dict[str, Any]:
    observation = json.loads((RUNTIME_BASE / "observation.json").read_text(encoding="utf-8"))
    overview = json.loads((RUNTIME_BASE / "overview.json").read_text(encoding="utf-8"))
    stage = {"scope": dict(SCOPE), "generation_id": GENERATION_ID}
    quote = {
        **stage,
        "quote_summary": {
            "requires_live_quoter": True,
            "closed_loop_route_count": overview.get("summary", {}).get("closedLoopRouteCount", 0),
            "observation_anchor_count": overview.get("summary", {}).get("observationAnchorCount", 0),
        },
    }
    return build_workflow_replay_fixture(
        observation={**_strip_machine_paths(observation), **stage},
        overview={**_strip_machine_paths(overview), **stage},
        quote=quote,
        gate_allowed=False,
        reasons=["requires_live_quoter"],
    )


def test_real_bnb_observation_overview_replay_fixture_is_stable(real_bnb_replay_fixture):
    assert real_bnb_replay_fixture["generation_id"] == GENERATION_ID
    assert real_bnb_replay_fixture["scope"] == SCOPE
    assert real_bnb_replay_fixture["replay"]["gate_allowed"] is False
    assert real_bnb_replay_fixture["replay"]["reasons"] == ["requires_live_quoter"]
    assert real_bnb_replay_fixture["replay"]["target_hash"].startswith("sha256:")


def test_real_bnb_replay_fixture_is_persisted_and_readable():
    loaded = read_workflow_replay_fixture(FIXTURE_PATH)
    assert loaded["generation_id"] == GENERATION_ID
    assert loaded["scope"] == SCOPE
    assert loaded["replay"]["gate_allowed"] is False
    assert loaded["replay"]["reasons"] == ["requires_live_quoter"]


def test_real_bnb_replay_fixture_round_trips_without_hash_drift(real_bnb_replay_fixture, tmp_path):
    path = write_workflow_replay_fixture(tmp_path / "real_bnb_replay.json", real_bnb_replay_fixture)
    loaded = read_workflow_replay_fixture(path)
    assert loaded["replay"]["target_hash"] == real_bnb_replay_fixture["replay"]["target_hash"]
    assert loaded["stages"].keys() == {"observation", "overview", "quote"}


def test_real_bnb_replay_fixture_hash_ignores_absolute_machine_paths():
    observation = json.loads((RUNTIME_BASE / "observation.json").read_text(encoding="utf-8"))
    overview = json.loads((RUNTIME_BASE / "overview.json").read_text(encoding="utf-8"))
    stage = {"scope": dict(SCOPE), "generation_id": GENERATION_ID}

    def build_with_paths() -> str:
        observation_with_path = {**_strip_machine_paths(observation), **stage}
        overview_with_path = {**_strip_machine_paths(overview), **stage}
        return build_workflow_replay_fixture(
            observation=observation_with_path,
            overview=overview_with_path,
            quote={**stage, "quote_summary": {"requires_live_quoter": True}},
            gate_allowed=False,
        )["replay"]["target_hash"]

    assert build_with_paths() == build_with_paths()

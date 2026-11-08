"""Tests for directional closed-loop control projection in the overview UI."""

from __future__ import annotations

from typing import Any

import pytest

from web.data_routes import manager_overview_payload as overview


ANCHOR = "0x" + ("1" * 40)
Y = "0x" + ("2" * 40)
Z = "0x" + ("3" * 40)


def test_overview_projects_directional_loop_keys_with_shared_worker_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        overview,
        "_closed_loop_control_snapshot",
        lambda chain_id: {
            "available": True,
            "workers": [{"group_key": "aaa|bbb|ccc", "state": "idle"}],
            "executionTasks": [],
            "reviewQueue": {},
            "executionQueue": {},
        },
    )
    routes: list[dict[str, Any]] = [
        {
            "routeSymbols": ["AAA", "BBB", "CCC", "AAA"],
            "routeAddresses": [ANCHOR, Y, Z, ANCHOR],
        },
        {
            "routeSymbols": ["AAA", "CCC", "BBB", "AAA"],
            "routeAddresses": [ANCHOR, Z, Y, ANCHOR],
        },
    ]

    overview._attach_closed_loop_control(routes, chain_id=46630)

    forward = routes[0]["closedLoopControl"]
    reverse = routes[1]["closedLoopControl"]
    assert forward["loopKey"] == f"{ANCHOR}:{Y}:{Z}"
    assert reverse["loopKey"] == f"{ANCHOR}:{Z}:{Y}"
    assert forward["directionKey"] == "aaa|bbb|ccc"
    assert reverse["directionKey"] == "aaa|ccc|bbb"
    assert forward["groupKey"] == reverse["groupKey"] == "aaa|bbb|ccc"
    assert forward["workerGroupKey"] == "aaa|bbb|ccc"

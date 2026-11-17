"""Tests for directional closed-loop control projection in the overview UI."""

from __future__ import annotations

from typing import Any

import pytest

from web.data_routes import manager_overview_payload as overview


ANCHOR = "0x" + ("1" * 40)
Y = "0x" + ("2" * 40)
Z = "0x" + ("3" * 40)


def test_overview_projects_directional_loop_keys_with_directional_worker_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A resident worker serves exactly one direction: the native task builder
    # rejects a group that mixes directions
    # (``mixed_direction_routes_not_allowed`` in
    # ``dex.runtime.rust_closed_loop.build_group_task_config``).  The
    # overview therefore has to look the worker up by the *directional* group
    # key, so the reverse route does not inherit the forward worker's state.
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
    # The group key is the worker identity, so it keeps the direction.
    assert forward["groupKey"] == "aaa|bbb|ccc"
    assert reverse["groupKey"] == "aaa|ccc|bbb"
    assert forward["workerGroupKey"] == "aaa|bbb|ccc"
    assert reverse["workerGroupKey"] == "aaa|ccc|bbb"

    # Only the forward route has a resident worker; the reverse route must not
    # borrow its state.
    assert forward["review"]["state"] == "idle"
    assert reverse["review"]["state"] == "missing"

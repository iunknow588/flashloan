from __future__ import annotations

from pathlib import Path

import pytest


def test_worktree_rust_extension_is_preferred_and_has_runtime_core_views() -> None:
    from core.rust_extension import load_rust_extension, rust_extension_candidates

    worktree_build = Path(__file__).resolve().parents[3] / "rs-src" / "target" / "release"
    if not any(path.parent == worktree_build for path in rust_extension_candidates()):
        pytest.skip("worktree Rust extension build is not present")

    module = load_rust_extension()

    assert module is not None
    assert hasattr(module, "runtime_core_manager_view")
    assert hasattr(module, "runtime_core_command")

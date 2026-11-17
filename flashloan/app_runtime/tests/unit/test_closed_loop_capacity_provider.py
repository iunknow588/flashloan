"""The folded single-tick capacity published by the closed-loop Manager.

The Manager computes the folded capacity with the global kernel object and
publishes it on the loop row. The runtime consumes that published value
instead of computing a bound of its own, because a runtime-local bound is
not a single-tick bound.

The **catalog** that carries the capacity lives in the Rust in-memory
object of the runtime: it is read once, when the runtime initializes, and
every later processing step works on that object. There is therefore no
Python capacity provider any more, and this module pins the guarantees
against the implementation that actually owns the value.

The properties that matter:

* a capacity is looked up by the **ordered token path**, so it can only
  ever match the loop it was computed for;
* a route the Manager does not know keeps **no** capacity (a coverage
  gap, not a measured zero);
* the catalog is **chain-scoped**, so the catalog of one chain can never
  be applied to another chain;
* the capacity is **not** read per review: the review path takes it from
  the in-memory object.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
RS_SRC = REPO_ROOT / "flashloan" / "rs-src" / "src"
SRC_ROOT = REPO_ROOT / "flashloan" / "src"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_there_is_no_python_capacity_provider_any_more() -> None:
    """MIO-5: Python must not fetch the capacity and hand it back.

    The provider that did that was a second authority: the runtime asked
    the manager API for a number and then pushed it into the route the
    worker was about to calculate with. The capacity now travels inside
    the in-memory object, so the provider is gone.
    """
    assert not (SRC_ROOT / "dex" / "runtime" / "closed_loop_capacity.py").exists(), (
        "a Python capacity provider exists again. The capacity belongs to "
        "the in-memory object of the runtime, not to a Python client."
    )
    offenders = [
        str(path)
        for path in SRC_ROOT.rglob("*.py")
        if "closed_loop_capacity" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert not offenders, (
        "MIO-5 violation: a module still refers to the Python capacity "
        "provider:\n" + "\n".join(offenders)
    )


def test_the_capacity_lives_in_the_rust_in_memory_catalog() -> None:
    """MIO-2 / MIO-7: the catalog is held by the in-memory object."""
    worker_pool = _read(RS_SRC / "worker_pool.rs")
    assert "closed_loops: ClosedLoopCatalog" in worker_pool, (
        "the in-memory context object does not hold the closed-loop catalog"
    )
    identity = _read(RS_SRC / "closed_loop_identity.rs")
    assert "single_tick_capacity_anchor_raw" in identity, (
        "the catalog does not carry the folded single-tick capacity"
    )
    assert "pub fn catalog_from_rows" in identity, (
        "the catalog is not built from the Manager rows"
    )


def test_the_capacity_is_looked_up_by_the_ordered_token_path() -> None:
    """MIO-7: only the loop it was computed for may match."""
    identity = _read(RS_SRC / "closed_loop_identity.rs")
    match = re.search(r"pub fn identity_for\(.*?\n    \}\n", identity, re.S)
    assert match, "the ordered-path lookup must exist"
    body = match.group(0)
    assert "take(3)" in body, "the lookup must use the ordered token path"
    assert "by_symbol_path.get" in body, "the lookup must be exact"


def test_a_route_without_a_published_capacity_keeps_no_capacity() -> None:
    """MIO-7: a coverage gap is not a measured zero."""
    identity = _read(RS_SRC / "closed_loop_identity.rs")
    assert "Option<u128>" in identity, (
        "the capacity must be optional, so an unpublished capacity stays "
        "unpublished"
    )
    worker_pool = _read(RS_SRC / "worker_pool.rs")
    match = re.search(
        r"fn apply_closed_loop_identity\(.*?\n\}\n", worker_pool, re.S
    )
    assert match, "the identity application must exist"
    body = match.group(0)
    assert "if let Some(capacity)" in body, (
        "an unpublished capacity must not overwrite the route"
    )


def test_the_catalog_read_is_chain_scoped() -> None:
    """MIO-8: the catalog of one chain may not reach another chain."""
    reader = _read(RS_SRC / "sqlite_read.rs")
    match = re.search(
        r"pub fn load_manager_closed_loop_rows\(.*?\n\}\n", reader, re.S
    )
    assert match, "the manager closed-loop reader must exist"
    body = match.group(0)
    assert "network = ?1" in body, "the read must filter on the network"
    assert "chain_id = ?2" in body, "the read must filter on the chain id"


def test_the_capacity_is_not_read_per_review() -> None:
    """MIO-1: SQLite is read at initialization, not per processing step."""
    worker_pool = _read(RS_SRC / "worker_pool.rs")
    match = re.search(r"fn run_native_review\(.*?\n\}\n", worker_pool, re.S)
    assert match, "the native review entry point must exist"
    body = match.group(0)
    assert "load_manager_closed_loop_rows(" not in body, (
        "MIO-1 violation: the review path reads the Manager library per "
        "review. Hold the catalog in the in-memory object instead."
    )

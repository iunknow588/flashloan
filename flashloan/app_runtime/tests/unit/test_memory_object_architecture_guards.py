"""Architecture guards for the in-memory-object processing constraint.

The system has exactly one legal data flow::

    SQLite (read once at initialization)
        -> global in-memory object (kept current by the WSS monitor)
        -> every processing step reads the in-memory object
        -> results travel through a queue
        -> the queue target writes back to SQLite

The rules are published as ``MIO-1..MIO-8`` in
``plan/开发计划_20261113.md`` section 4.1. These tests turn the rules into
executable checks, so a violation fails the build instead of waiting for
a human review.

The checks are deliberately narrow: they look for the *specific* shapes
that are known to break the rule, rather than trying to prove a negative
about the whole tree.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "flashloan" / "src"
RS_SRC = REPO_ROOT / "flashloan" / "rs-src" / "src"


def _python_files() -> list[Path]:
    return [path for path in SRC_ROOT.rglob("*.py") if "__pycache__" not in path.parts]


def _rust_files() -> list[Path]:
    return [path for path in RS_SRC.rglob("*.rs") if "target" not in path.parts]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _function_nodes(tree: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes.append(node)
    return nodes


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


# ---------------------------------------------------------------------------
# MIO-5 / MIO-6: Python must not rebuild the closed-loop identity
# ---------------------------------------------------------------------------


def test_python_does_not_assemble_the_closed_loop_identity() -> None:
    """MIO-7: the loop key belongs to the closed-loop Manager.

    The Manager may publish several loops for one symbol path, so a key
    that Python assembles from the route addresses can name the wrong loop.
    Python must consume the key the kernel published.
    """
    offenders: list[str] = []
    for path in _python_files():
        text = _read(path)
        # A route may carry the key through untouched; what is forbidden is
        # building it out of the route addresses.
        for match in re.finditer(
            r"""loopKey["']\]\s*=\s*["']?:?\.?join\(""",
            text,
        ):
            offenders.append(f"{path}: {match.group(0)}")
        for match in re.finditer(
            r"""loopKey["']\]\s*=\s*["']:["']\.join\(""",
            text,
        ):
            offenders.append(f"{path}: {match.group(0)}")
    assert not offenders, (
        "MIO-7 violation: Python assembles the closed-loop identity. "
        "Consume the kernel-published key instead:\n" + "\n".join(offenders)
    )


def test_python_does_not_call_the_manager_api_for_the_closed_loop_capacity() -> None:
    """MIO-5: Python must not fetch business state to feed the worker.

    The capacity belongs to the in-memory object of the runtime. A Python
    client that reads it from the manager API and hands it back to the
    worker turns Python into the second authority.
    """
    offenders: list[str] = []
    for path in _python_files():
        text = _read(path)
        if "/v1/closed-loops" in text and (
            "ManagerApiClient" in text or "get_json" in text
        ):
            offenders.append(str(path))
    assert not offenders, (
        "MIO-5 violation: Python reads the closed-loop catalog from the "
        "manager API. The runtime must serve it from its in-memory object:\n"
        + "\n".join(offenders)
    )


def test_python_does_not_derive_the_signed_pnl_of_a_review() -> None:
    """MIO-6: the signed profit of a review is a kernel conclusion.

    A review is filed under the numbers the contract call produced. A
    Python-side derivation can disagree with them, and the reviewer then
    publishes a profit that was never measured.
    """
    offenders: list[str] = []
    for path in _python_files():
        text = _read(path)
        if re.search(r"""["']signedPnl["']\s*:""", text) and re.search(
            r"""def _native_signed_pnl|def _signed_pnl|signed_pnl\s*=\s*\{""",
            text,
        ):
            offenders.append(str(path))
    assert not offenders, (
        "MIO-6 violation: Python derives the signed profit of a review. "
        "The kernel must publish it:\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# MIO-1: the processing path must not query the database per review
# ---------------------------------------------------------------------------


def test_the_rust_review_path_does_not_read_the_closed_loop_library_per_review() -> None:
    """MIO-1: SQLite is read at initialization, not per processing step.

    The closed-loop catalog belongs to the in-memory object, so the review
    path must not open the Manager library for every route it reviews.
    """
    worker_pool = (RS_SRC / "worker_pool.rs").read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse("")  # placeholder to keep the type checker happy
    del tree
    # The review entry point may not call the catalog reader directly.
    match = re.search(
        r"fn run_native_review\(.*?\n\}\n",
        worker_pool,
        re.S,
    )
    assert match, "the native review entry point must exist"
    body = match.group(0)
    assert "read_closed_loop_catalog(" not in body, (
        "MIO-1 violation: the review path reads the closed-loop library per "
        "review. Hold the catalog in the in-memory object instead."
    )
    assert "load_manager_closed_loop_rows(" not in body, (
        "MIO-1 violation: the review path reads the Manager library per "
        "review. Hold the catalog in the in-memory object instead."
    )


def test_the_closed_loop_catalog_lives_in_the_in_memory_object() -> None:
    """MIO-2: the catalog is held by the in-memory context object."""
    worker_pool = (RS_SRC / "worker_pool.rs").read_text(encoding="utf-8", errors="ignore")
    assert "closed_loops: ClosedLoopCatalog" in worker_pool, (
        "MIO-2 violation: the in-memory context object does not hold the "
        "closed-loop catalog"
    )


# ---------------------------------------------------------------------------
# MIO-8: the chain boundary is never crossed
# ---------------------------------------------------------------------------


def test_the_manager_closed_loop_read_is_chain_scoped() -> None:
    """MIO-8: a chain-scoped read must name both the network and the chain."""
    reader = (RS_SRC / "sqlite_read.rs").read_text(encoding="utf-8", errors="ignore")
    match = re.search(
        r"pub fn load_manager_closed_loop_rows\(.*?\n\}\n",
        reader,
        re.S,
    )
    assert match, "the manager closed-loop reader must exist"
    body = match.group(0)
    assert "network = ?1" in body, "the read must filter on the network"
    assert "chain_id = ?2" in body, "the read must filter on the chain id"


# ---------------------------------------------------------------------------
# MIO-3 / MIO-7: the kernel publishes the profit, decimals live in memory
# ---------------------------------------------------------------------------


def test_the_kernel_publishes_the_human_amount_of_the_signed_profit() -> None:
    """MIO-7: the signed profit amount is a kernel conclusion.

    The Review Manager requires the human amount next to the raw one. The
    kernel publishes both, so the review can never carry a profit that was
    derived somewhere else.
    """
    worker_pool = (RS_SRC / "worker_pool.rs").read_text(encoding="utf-8", errors="ignore")
    match = re.search(
        r"fn apply_selected_route_overrides\(.*?\n\}\n",
        worker_pool,
        re.S,
    )
    assert match, "the selected-route override must exist"
    body = match.group(0)
    assert '"amount"' in body, (
        "MIO-7 violation: the kernel does not publish the human amount of "
        "the signed profit"
    )
    assert "signed_raw_to_amount(" in body, (
        "MIO-7 violation: the kernel does not scale the raw profit by the "
        "decimals of the borrow token"
    )


def test_the_kernel_publishes_the_signed_profit_inside_the_run_arbitrage_object() -> None:
    """MIO-5: the Review Manager reads the nested object.

    The manager DTO reads ``runArbitrage.executable`` and
    ``runArbitrage.signedPnl``. The kernel therefore publishes the values
    there, so the review is filed with the numbers the contract produced.
    """
    worker_pool = (RS_SRC / "worker_pool.rs").read_text(encoding="utf-8", errors="ignore")
    match = re.search(
        r"fn apply_selected_route_overrides\(.*?\n\}\n",
        worker_pool,
        re.S,
    )
    assert match, "the selected-route override must exist"
    body = match.group(0)
    assert 'nested_run["signedPnl"]' in body, (
        "MIO-5 violation: the kernel does not publish the signed profit "
        "inside the runArbitrage object"
    )
    assert 'nested_run["executable"]' in body, (
        "MIO-5 violation: the kernel does not publish the executable flag "
        "inside the runArbitrage object"
    )


def test_the_token_decimals_live_in_the_in_memory_object() -> None:
    """MIO-3: the decimals of a token are part of the memory object.

    The kernel scales the raw profit by the decimals of the borrow token.
    Those decimals are read once, at initialization, and held by the
    in-memory context object.
    """
    worker_pool = (RS_SRC / "worker_pool.rs").read_text(encoding="utf-8", errors="ignore")
    assert "token_decimals: TokenDecimalsRegistry" in worker_pool, (
        "MIO-3 violation: the in-memory context object does not hold the "
        "token decimals"
    )
    assert "struct TokenDecimalsRegistry" in worker_pool, (
        "MIO-3 violation: the token decimals registry does not exist"
    )


def test_the_token_decimals_read_is_chain_scoped() -> None:
    """MIO-8: the decimals of one chain may not be applied to another."""
    reader = (RS_SRC / "sqlite_read.rs").read_text(encoding="utf-8", errors="ignore")
    match = re.search(
        r"pub fn load_chain_token_decimals_rows\(.*?\n\}\n",
        reader,
        re.S,
    )
    assert match, "the token decimals reader must exist"
    body = match.group(0)
    assert "network = ?1" in body, "the read must filter on the network"
    assert "chain_id = ?2" in body, "the read must filter on the chain id"


def test_the_review_path_does_not_read_the_decimals_per_review() -> None:
    """MIO-1: the decimals are read at initialization, not per review."""
    worker_pool = (RS_SRC / "worker_pool.rs").read_text(encoding="utf-8", errors="ignore")
    match = re.search(
        r"fn run_native_review\(.*?\n\}\n",
        worker_pool,
        re.S,
    )
    assert match, "the native review entry point must exist"
    body = match.group(0)
    assert "load_chain_token_decimals_rows(" not in body, (
        "MIO-1 violation: the review path reads the token decimals per "
        "review. Hold them in the in-memory object instead."
    )


# ---------------------------------------------------------------------------
# MIO-6: no layer may become the second authority
# ---------------------------------------------------------------------------


def test_no_python_layer_assembles_a_loop_key_out_of_the_route_addresses() -> None:
    """MIO-6: the closed-loop identity has exactly one authority.

    The healer used to rebuild the key from the first three route
    addresses. The Manager may publish several loops for one symbol path,
    so such a key can name the wrong loop.
    """
    offenders: list[str] = []
    for path in list(_python_files()) + [
        REPO_ROOT / "scripts" / "mainnet_arbitrage_healer.py"
    ]:
        if not path.exists():
            continue
        text = _read(path)
        for match in re.finditer(
            r"""loopKey["']\]\s*=\s*["']:["']\.join\(""",
            text,
        ):
            offenders.append(f"{path}: {match.group(0)}")
    assert not offenders, (
        "MIO-6 violation: a layer assembles the closed-loop identity from "
        "the route addresses. Consume the Manager-published key instead:\n"
        + "\n".join(offenders)
    )


def test_python_consumes_the_kernel_published_profit_without_deriving_it() -> None:
    """MIO-6: Python reads the profit, it does not rebuild it."""
    bridge = (SRC_ROOT / "dex" / "runtime" / "rust_closed_loop.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert 'nested_run_arbitrage.get("signedPnl")' in bridge, (
        "MIO-6 violation: the bridge does not consume the kernel-published "
        "signed profit"
    )
    assert "def _native_signed_pnl" not in bridge, (
        "MIO-6 violation: the bridge derives the signed profit itself"
    )


def test_the_plan_documents_the_memory_object_constraint() -> None:
    """The constraint is published, so a reviewer can check it by reading."""
    plan = (REPO_ROOT / "plan" / "开发计划_20261113.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    for rule in ("MIO-1", "MIO-2", "MIO-3", "MIO-4", "MIO-5", "MIO-6", "MIO-7", "MIO-8"):
        assert rule in plan, f"the plan does not publish {rule}"
    assert "内存对象处理约束" in plan, "the plan has no memory-object section"
    # The review evidence shape has to be published too, because it is the
    # contract between the kernel and the review manager.
    assert "signedPnl.amount" in plan, (
        "the plan does not publish the signed profit amount requirement"
    )


def test_the_readme_summarizes_the_memory_object_constraint() -> None:
    """The README carries the summary table of the constraint."""
    readme = (REPO_ROOT / "plan" / "README.md").read_text(
        encoding="utf-8", errors="ignore"
    )
    assert "内存对象处理约束" in readme, "the README has no memory-object section"
    for rule in ("MIO-1", "MIO-5", "MIO-6", "MIO-7", "MIO-8"):
        assert rule in readme, f"the README does not summarize {rule}"


# ---------------------------------------------------------------------------
# The token layer: a token without essential metadata is removed
# ---------------------------------------------------------------------------


TOKEN_PRUNE = REPO_ROOT / "flashloan" / "tools" / "token_manager" / "src" / "prune.rs"


def test_the_token_manager_owns_the_removal_of_incomplete_tokens() -> None:
    """The token layer owns the repair, and the removal of what cannot be repaired.

    A token without a usable symbol or without decimals cannot be priced
    and cannot be routed through. The token manager is the layer that owns
    that decision, so it is the layer that performs it.
    """
    assert TOKEN_PRUNE.exists(), "the token manager has no prune module"
    text = TOKEN_PRUNE.read_text(encoding="utf-8", errors="ignore")
    assert "pub fn prune_incomplete_tokens" in text, (
        "the token manager does not expose the prune of incomplete tokens"
    )
    assert "prune-incomplete-tokens" in (
        REPO_ROOT / "flashloan" / "tools" / "token_manager" / "src" / "main.rs"
    ).read_text(encoding="utf-8", errors="ignore"), (
        "the token manager has no prune command"
    )


def test_only_the_essential_metadata_removes_a_token() -> None:
    """The prune is narrow on purpose.

    It removes a token whose **symbol** or whose **decimals** are missing
    or unusable. Every other field is optional, so a token that merely
    lacks a display name stays.
    """
    text = TOKEN_PRUNE.read_text(encoding="utf-8", errors="ignore")
    match = re.search(r"fn incomplete_reasons\(.*?\n\}\n", text, re.S)
    assert match, "the completeness rule must exist"
    body = match.group(0)
    for essential in ("MissingSymbol", "PlaceholderSymbol", "MissingDecimals"):
        assert essential in body, (
            f"the completeness rule does not consider {essential}"
        )
    # The optional fields must not appear in the rule at all.
    for optional in ("token_name", "token_kind", "is_native", "is_stable"):
        assert optional not in body, (
            f"the completeness rule treats {optional} as essential, which it "
            "is not"
        )


def test_the_prune_removes_the_dependants_of_the_pruned_token() -> None:
    """A loop that routes through a removed token cannot survive it."""
    text = TOKEN_PRUNE.read_text(encoding="utf-8", errors="ignore")
    for table in (
        "v4_closed_loops",
        "v4_closed_loop_hops",
        "v4_closed_loop_optimizations",
        "v4_exchange_pools",
        "dex_candidate_anchor_tokens",
        "market_observation_yz_pairs",
    ):
        assert table in text, (
            f"the prune does not clean up {table}, so a row that names a "
            "removed token would survive it"
        )


def test_the_prune_respects_the_chain_boundary() -> None:
    """A store of one chain is never pruned as if it belonged to another."""
    text = TOKEN_PRUNE.read_text(encoding="utf-8", errors="ignore")
    assert "ScopeMismatch" in text, "the prune does not check the chain scope"

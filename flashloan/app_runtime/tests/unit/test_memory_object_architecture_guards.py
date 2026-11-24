"""Architecture guards for the in-memory-object processing constraint.

The system has exactly one legal data flow::

    SQLite (read once at initialization)
        -> global in-memory object (kept current by the WSS monitor)
        -> every processing step reads the in-memory object
        -> results travel through a queue
        -> the queue target writes back to SQLite

The rules are published as ``MIO-1..MIO-8`` in
``plan/池子重构方案/开发计划_20261123.md`` section 3.1. These tests turn the rules into
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
SCRIPTS_ROOT = REPO_ROOT / "scripts"


def _python_files() -> list[Path]:
    return [path for path in SRC_ROOT.rglob("*.py") if "__pycache__" not in path.parts]


def _script_files() -> list[Path]:
    """Return every Python file of the repo-level CLI/ops area.

    The healer and the migration planners live here, so a violation in this
    tree is exactly as damaging as one in ``flashloan/src``. The scope is
    deliberately the whole tree: shrinking it would only hide the offender.
    """
    if not SCRIPTS_ROOT.exists():
        return []
    return [
        path
        for path in SCRIPTS_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def _guarded_python_files() -> list[Path]:
    """Return every Python file subject to the MIO identity guards."""
    return _python_files() + _script_files()


def _active_development_plan() -> Path:
    """Return the active development-plan document.

    The plan is renamed whenever the active version advances, so pinning
    one file name here would break the guard on every version bump. The
    active document is the one in the active plan directory whose name
    carries ``开发计划``; a frozen snapshot under ``plan/archive/`` or
    ``plan/back/`` never matches.

    The active plan directory is ``plan/tasks/P-01-池子重构/docs/``: the
    three documents that used to sit directly in ``plan/`` were frozen
    into ``plan/back/`` and their outstanding work was merged into the
    documents that live in the plan directory.
    """
    plan_root = REPO_ROOT / "plan"
    candidates = sorted(
        path
        for path in plan_root.glob("tasks/*/docs/*开发计划*.md")
        if path.is_file()
    )
    if not candidates:
        raise AssertionError(
            "no active development plan found in plan/; the MIO rules are "
            "published there and the guard cannot verify them"
        )
    return candidates[-1]


def _parsed(path: Path) -> ast.Module | None:
    try:
        return ast.parse(_read(path), filename=str(path))
    except SyntaxError:
        return None


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


# The rotation capability that the closed-loop Manager owns. A Python layer
# that calls one of these can make a *different* closed loop look like the
# one it means, and with it that loop's capacity, pool set and threshold.
FORBIDDEN_ROTATION_CALLS = frozenset(
    {
        "canonical_directed_cycle_key",
        "directed_cycle_rotations",
        "directed_cycle_keys_match",
        "directed_cycle_identity",
    }
)

# The runtime state table used to own a private rotation namespace.  It is
# removed: a closed loop is the funding anchor plus the directed pool path,
# so no layer may expand a key into its rotations or canonicalize it.
FORBIDDEN_ROTATION_ALIASES = frozenset(
    {
        "_state_row_alias_rotations",
        "_state_row_alias_canonical",
    }
)


def _rotation_merge_call_sites(path: Path) -> list[str]:
    """Return every AST site that merges a key with its rotations."""
    tree = _parsed(path)
    if tree is None:
        return []
    sites: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name in FORBIDDEN_ROTATION_ALIASES:
            sites.append(f"{path}:{node.lineno}: call to {name}()")
    return sites


def _forbidden_rotation_call_sites(path: Path) -> list[str]:
    """Return every AST site that calls a forbidden rotation helper."""
    tree = _parsed(path)
    if tree is None:
        return []
    sites: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name in FORBIDDEN_ROTATION_CALLS:
            sites.append(f"{path}:{node.lineno}: call to {name}()")
    return sites


def _defined_names(path: Path) -> set[str]:
    """Return every function and class name a module defines."""
    tree = _parsed(path)
    if tree is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _imported_names(path: Path) -> set[str]:
    """Return every name a module imports."""
    tree = _parsed(path)
    if tree is None:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


# The names a closed-loop identity may be published under. Exactly one of
# them is the evidence field; the others are the table columns and the
# kernel field. A layer that reads several of them for one identity is
# guessing, and guessing is what produced the borrowed-identity bug.
IDENTITY_PUBLICATION_NAMES = frozenset(
    {"loopKey", "loop_key", "closedLoopKey", "libraryLoopKey"}
)


def _identity_publication_read_sites(path: Path) -> list[str]:
    """Return loops that explicitly try several identity field names."""
    tree = _parsed(path)
    if tree is None:
        return []
    sites: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id not in {"key", "field", "name"}:
            continue
        if not isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)):
            continue
        literals = {
            element.value
            for element in node.iter.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
        matched = literals & IDENTITY_PUBLICATION_NAMES
        if len(matched) > 1:
            sites.append(
                f"{path}:{node.lineno}: reads several identity names "
                f"{sorted(matched)}"
            )
    return sites


def _kernel_identity_field_read_sites(path: Path) -> list[str]:
    """Return every site that *reads* the kernel identity field name.

    The kernel publishes the closed-loop identity as ``closedLoopKey``. The
    bridge layer is the single place that may read it and republish it as
    ``loopKey``. A mention in a docstring is documentation, not a
    translation, so only a real subscript or ``get`` call counts.
    """
    tree = _parsed(path)
    if tree is None:
        return []
    sites: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            index = node.slice
            if (
                isinstance(index, ast.Constant)
                and index.value == "closedLoopKey"
            ):
                sites.append(f"{path}:{node.lineno}: reads closedLoopKey[]")
        elif isinstance(node, ast.Call):
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "get"):
                continue
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and arg.value == "closedLoopKey"
                ):
                    sites.append(
                        f"{path}:{node.lineno}: reads .get('closedLoopKey')"
                    )
    return sites


def _route_address_join_sites(path: Path) -> list[str]:
    """Return key-shaped joins whose source or destination is address data."""
    tree = _parsed(path)
    if tree is None:
        return []
    addressish = re.compile(
        r"route_?addresses?|path_?addresses?|routeAddress|pathAddress",
        re.IGNORECASE,
    )
    identityish = re.compile(r"loop.?key|identity|closed.?loop", re.IGNORECASE)
    joins: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "join":
            separator = node.func.value
            if isinstance(separator, ast.Constant) and separator.value in {":", "|"}:
                joins.append(node)
    offenders: list[str] = []
    for join in joins:
        source_names = {
            node.id
            for node in ast.walk(join)
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(join)
            if isinstance(node, ast.Attribute)
        }
        if not any(addressish.search(name) for name in source_names):
            continue
        parents: dict[ast.AST, ast.AST] = {}
        tree = _parsed(path)
        if tree is None:
            continue
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        parent = parents.get(join)
        if isinstance(parent, ast.Call):
            func_name = (
                parent.func.id
                if isinstance(parent.func, ast.Name)
                else parent.func.attr
                if isinstance(parent.func, ast.Attribute)
                else ""
            )
            if identityish.search(func_name):
                offenders.append(f"{path}:{join.lineno}: address join passed to {func_name}()")
                continue
        cursor: ast.AST = join
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, (ast.Assign, ast.AnnAssign)):
                target_text = ast.dump(cursor)
                if identityish.search(target_text):
                    offenders.append(f"{path}:{join.lineno}: address join assigned to identity")
                break
            if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                break
    return offenders


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
    plan = _read(_active_development_plan())
    for rule in ("MIO-1", "MIO-2", "MIO-3", "MIO-4", "MIO-5", "MIO-6", "MIO-7", "MIO-8"):
        assert rule in plan, f"the plan does not publish {rule}"
    assert "内存对象处理约束" in plan, "the plan has no memory-object section"
    # The review evidence shape has to be published too, because it is the
    # contract between the kernel and the review manager.
    assert "signedPnl.amount" in plan, (
        "the plan does not publish the signed profit amount requirement"
    )


def test_plan_root_keeps_exactly_the_three_active_documents() -> None:
    """The plan root exposes one plan directory and no loose backlog.

    The three documents used to sit directly in ``plan/``. They were
    frozen into ``plan/back/`` and their outstanding work was merged
    into the plan directory, which is now the active plan directory.
    The plan root therefore holds no loose backlog document any more;
    leaving one there would create a second source of truth for it.

    The two documents the plan root **does** hold are the index of the
    task registry and the index of the plan itself.  Both are indexes,
    not backlogs: they point at the active plan directory and hold no
    outstanding work of their own.
    """
    plan_root = REPO_ROOT / "plan"
    active_files = {path.name for path in plan_root.iterdir() if path.is_file()}
    assert active_files == {"D-001-TASK_REGISTRY.md", "README.md"}

    plan_dir = plan_root / "tasks" / "P-01-池子重构" / "docs"
    assert plan_dir.is_dir(), "the active plan directory is missing"
    documents = {path.name for path in plan_dir.glob("*.md")}
    assert {
        "D-013-开发计划_20261123.md",
        "D-014-未完成任务清单_20261123.md",
        "D-011-check_list_20261123.md",
    } <= documents, "the plan directory lost one of the three active documents"


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


# ---------------------------------------------------------------------------
# MIO-6 / MIO-7, strengthened: the closed-loop identity has one authority
# ---------------------------------------------------------------------------


def test_no_python_layer_calls_the_rotation_capability() -> None:
    """MIO-7: the rotation capability must not exist in any Python layer.

    A closed loop is the **funding anchor plus the directed pool path**.
    The intermediate tokens of one route are not necessarily funding tokens
    of another route, so a path that starts at an intermediate token is a
    *different* closed loop with its own identity, its own capacity, its
    own pool set and its own review threshold.

    The rotation helpers made a loop borrow the identity -- and therefore
    the capacity, the pool set and the review threshold -- of a *different*
    closed loop that happened to start at another hop of the same triangle.

    The Manager owns the identity of every loop and publishes it.  A Python
    layer that rotates is a second authority, so the capability is removed
    from the shared identity module and no layer may define or call it.

    The scan covers ``flashloan/src`` **and** the whole ``scripts`` tree:
    the healer lives in ``scripts``, and a violation there is exactly as
    damaging as one in the runtime.
    """
    offenders: list[str] = []
    for path in _guarded_python_files():
        offenders.extend(_forbidden_rotation_call_sites(path))
        defined = _defined_names(path)
        for name in FORBIDDEN_ROTATION_CALLS:
            if name in defined:
                offenders.append(f"{path}: defines {name}")
    assert not offenders, (
        "MIO-7 violation: a Python layer uses the rotation capability. The "
        "closed-loop key is the funding anchor plus the directed pool path "
        "and is published by the Manager:\n"
        + "\n".join(offenders)
    )


def test_no_python_layer_imports_a_rotation_capability() -> None:
    """MIO-7: the rotation helpers must not even be importable."""
    offenders: list[str] = []
    for path in _guarded_python_files():
        imported = _imported_names(path)
        for name in FORBIDDEN_ROTATION_CALLS:
            if name in imported:
                offenders.append(f"{path}: imports {name}")
    assert not offenders, (
        "MIO-7 violation: a Python layer imports a rotation helper. Consume "
        "the Manager-published identity instead:\n" + "\n".join(offenders)
    )


def test_the_shared_identity_module_exposes_no_rotation_capability() -> None:
    """MIO-7: the identity module itself must be rotation-free."""
    module = SRC_ROOT / "dex" / "runtime" / "closed_loop_identity.py"
    assert module.exists(), "the shared identity module is missing"
    text = _read(module)
    for name in FORBIDDEN_ROTATION_CALLS:
        assert f"def {name}(" not in text, (
            f"MIO-7 violation: dex.runtime.closed_loop_identity still defines "
            f"{name}()"
        )
        assert f'"{name}"' not in text, (
            f"MIO-7 violation: dex.runtime.closed_loop_identity still exports "
            f"{name}"
        )


def test_no_python_layer_merges_a_loop_with_its_rotation() -> None:
    """MIO-7: no layer may expand a key into the rotations of that key.

    The runtime state table used to own a private rotation namespace: the
    three same-direction rotations of one directed triangle were merged onto
    one concrete row, so the state of one closed loop could be served to
    another one.  The merge had no logical basis, because the rotation
    starts at an intermediate token and is therefore a different closed
    loop.

    The runtime state table now uses the same exact lookup as the Manager
    identity: the published key, spelled exactly as it was published.
    """
    offenders: list[str] = []
    for path in _guarded_python_files():
        defined = _defined_names(path)
        for name in FORBIDDEN_ROTATION_ALIASES:
            if name in defined:
                offenders.append(f"{path}: defines {name}")
        offenders.extend(_rotation_merge_call_sites(path))
    assert not offenders, (
        "MIO-7 violation: a Python layer merges a closed loop with one of "
        "its rotations. The closed-loop key is the funding anchor plus the "
        "directed pool path and it is never rotated:\n"
        + "\n".join(offenders)
    )


def test_the_runtime_state_machine_resolves_state_keys_exactly() -> None:
    """MIO-7: the runtime state machine has no rotation namespace.

    The state machine may only look a state row up by its exact published
    key.  The canonical-rotation helpers it used to own are gone, so the
    rotation capability cannot be reintroduced through the state table.
    """
    state_machine = SRC_ROOT / "dex" / "runtime" / "closed_loop_state_machine.py"
    assert state_machine.exists(), "the closed-loop state machine is missing"
    text = _read(state_machine)
    for name in FORBIDDEN_ROTATION_ALIASES:
        assert f"def {name}(" not in text, (
            f"MIO-7 violation: the runtime state machine defines {name}() "
            "again. The closed-loop key is the funding anchor plus the "
            "directed pool path; it is never rotated."
        )
        assert f'"{name}"' not in text, (
            f"MIO-7 violation: the runtime state machine still exports "
            f"{name}"
        )


def test_no_python_layer_reads_several_identity_field_names_for_one_identity() -> None:
    """MIO-7: one identity is published under one name per source.

    The bridge layer translates the kernel field ``closedLoopKey`` into the
    evidence field ``loopKey`` exactly once. A consumer that reads several
    names for one identity is guessing between them, which is what allowed a
    review to be filed under a loop the Manager never published.
    """
    offenders: list[str] = []
    for path in _guarded_python_files():
        offenders.extend(_identity_publication_read_sites(path))
    assert not offenders, (
        "MIO-7 violation: a layer reads several identity field names for one "
        "identity. The bridge translates the kernel field once:\n"
        + "\n".join(offenders)
    )


def test_the_bridge_is_the_single_translation_point() -> None:
    """MIO-7: exactly one module translates the kernel identity field."""
    bridge = SRC_ROOT / "dex" / "runtime" / "rust_closed_loop.py"
    text = _read(bridge)
    assert 'result.get("closedLoopKey")' in text, (
        "the bridge does not consume the kernel-published identity"
    )
    assert '"loopKey": loop_key' in text, (
        "the bridge does not publish the identity as loopKey"
    )

    # No other module may *read* the kernel field name. A docstring that
    # merely mentions it is not a translation, so the check is AST based
    # rather than a plain text scan.
    offenders: list[str] = []
    for path in _guarded_python_files():
        if path == bridge:
            continue
        offenders.extend(_kernel_identity_field_read_sites(path))
    assert not offenders, (
        "MIO-7 violation: another layer translates the kernel identity field. "
        "The bridge is the single translation point:\n" + "\n".join(offenders)
    )


def test_the_identity_is_never_assembled_from_the_route_addresses() -> None:
    """MIO-7: the identity is consumed, never assembled.

    This is the AST form of the older regex guard. The regex only saw
    assignments of the shape ``loopKey = ":".join(...)``; it did not see a
    join that is passed as a function argument, and it did not see a join
    that is stored under a different variable name first. The AST form
    catches the whole shape: a ``join`` over an address-bearing collection
    that ends up in an identity-ish position.
    """
    offenders: list[str] = []
    for path in _guarded_python_files():
        offenders.extend(_route_address_join_sites(path))
    assert not offenders, (
        "MIO-7 violation: a layer assembles the closed-loop identity out of "
        "the route addresses. Consume the Manager-published key instead:\n"
        + "\n".join(offenders)
    )

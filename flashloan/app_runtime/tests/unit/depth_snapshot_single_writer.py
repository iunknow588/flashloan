# -*- coding: utf-8 -*-
"""The single-writer guard for the depth snapshot table (TASK-172 / AC-221).

The defect this guard closes
---------------------------
``v4_exchange_pool_liquidity_snapshots`` used to have **two** writers:

* the Rust evidence channel (``rs-src/src/evidence.rs``) wrote the price
  columns -- ``tick``, ``sqrt_price_x96``, ``liquidity`` -- and
* the Rust pool manager (``tools/pool_manager/src/lib.rs``) wrote the
  capacity columns and the single-tick bounds.

Neither writer knew about the other, so the two of them **interleaved their
columns inside one row**.  A row then carried the price of one on-chain
moment next to the boundary of another one, and every consumer of the
boundary read a number that did not belong to the price next to it.

The fix is the single-writer rule: the depth snapshot table has exactly one
**materialising** writer, the Rust pool manager, and that writer writes the
**whole row in one statement**, so a row always describes exactly one
on-chain moment.

Two kinds of statement, two kinds of rule
-----------------------------------------
The table is written by two different kinds of SQL, and they are not the
same kind of thing:

``materialising``
    ``INSERT`` / ``UPDATE`` / ``REPLACE``.  These put content into a row.
    Only the pool manager may do this, because a materialising writer
    decides what a row says, and that is exactly what went wrong.

``removing``
    ``DELETE``.  A removal that names the rows it removes
    (``has_liquidity = 0`` and the like) is housekeeping: it takes a row
    out of the table without saying anything about what the row contained.
    A removal that names only the *scope* takes **every** row of that
    scope out.  That is a different thing: it is the pool manager's
    materialised state being wiped by a channel that cannot write it back.
    So a scope-wide removal is forbidden outside the pool manager.

Why this lives in a guard and not in a comment
-----------------------------------------------
A comment is not a rule.  The next change that adds a second writer would
not read the comment, but it **will** fail this guard.  The guard therefore
has to answer four questions, and it has to answer them for every language
in the repository:

1. Is the pool manager the only Rust module that materialises the table?
2. Does no Python module materialise the table?
3. Does no module wipe the whole scope outside the pool manager?
4. Is the guard itself able to fail?  A check that cannot go red proves
   nothing, so the negative assertions feed the very same matcher a
   deliberately offending snippet.

The matcher is deliberately **narrow**: it only recognises SQL write verbs
that are immediately followed by the table name.  A read (``SELECT``), a
``PRAGMA`` and a table name inside a comment are not writes, and the guard
must not report them -- otherwise the guard would be noise and would be
switched off.
"""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]

#: The table whose writer count is pinned to one.
DEPTH_SNAPSHOT_TABLE = "v4_exchange_pool_liquidity_snapshots"

#: The one module that is allowed to materialise the table, and the one
#: module that is allowed to wipe a scope of it.
POOL_MANAGER_WRITER = "flashloan/tools/pool_manager/src/lib.rs"

#: The Rust roots that are scanned for writers.
RUST_ROOTS = (
    "flashloan/tools",
    "flashloan/rs-src",
)

#: The Python roots that are scanned for writers.  Python is a consumer of
#: the published snapshot; it never materialises one.
PYTHON_ROOTS = (
    "flashloan/src",
    "flashloan/app_runtime",
)

_QUOTE = r"[`\"\[]?"
_TABLE_NAME = _QUOTE + DEPTH_SNAPSHOT_TABLE + r"[`\"\]]?"
_QUALIFIED = r"(?:" + _QUOTE + r"\w+" + _QUOTE + r"\s*\.\s*)?"

#: A SQL statement that **materialises** a row: it puts content into the
#: table.  These are the statements that create the drift, because a
#: materialising writer decides what a row says.
_MATERIALISING_STATEMENT = re.compile(
    r"(?:\bINSERT\s+(?:OR\s+\w+\s+)?INTO\b"
    r"|\bUPDATE\b"
    r"|\bREPLACE\s+INTO\b)"
    r"\s+" + _QUALIFIED + _TABLE_NAME,
    re.IGNORECASE,
)

#: A SQL statement that **removes** a row.
_REMOVING_STATEMENT = re.compile(
    r"\bDELETE\s+FROM\b\s+" + _QUALIFIED + _TABLE_NAME,
    re.IGNORECASE,
)

#: The predicate that makes a removal **row scoped**.
#:
#: A removal that names the rows it removes (``has_liquidity = 0``,
#: ``has_liquidity IS NULL``, ``pool_id IN (...)``) is housekeeping: it
#: takes a row out of the table without saying anything about what the row
#: contained.  A removal that names only the *scope* takes **every** row of
#: that scope out, which is a different thing entirely.
_ROW_SCOPED_PREDICATE = re.compile(
    r"\b(?:has_liquidity\s*(?:=|IS)|pool_id\s+IN|liquidity_read_at\s*=|"
    r"materialization_batch\s*=|tick\s*=|sqrt_price_x96\s*=)",
    re.IGNORECASE,
)


def _rust_files():
    """Every Rust source file of the repository, build output excluded."""
    for root in RUST_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.rs"):
            if "target" in path.parts:
                continue
            yield path


def _is_test_file(path):
    """Whether a file is a test rather than a production module.

    A test builds its own fixture database, so a test that inserts a
    snapshot row is not a second writer -- it is a test double.  Two
    shapes count as a test: a ``tests`` directory (the Rust integration
    test layout) and a ``test_`` prefixed module (the Python layout).
    """
    parts = path.parts
    if "tests" in parts or "test" in parts:
        return True
    return path.name.startswith("test_")


def _python_files():
    """Every Python source file of the repository, caches excluded."""
    for root in PYTHON_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
                continue
            yield path


def _relative(path):
    return path.relative_to(REPO_ROOT).as_posix()


def _production_region(text):
    """Return the part of a Rust file that is not a test.

    A test builds its own fixture database, so a test that inserts a
    snapshot row is not a second writer -- it is a test double.  The
    ``#[cfg(test)]`` module is therefore cut off before the scan.

    The cut is taken at the *first* ``#[cfg(test)] mod tests`` marker.  A
    bare ``mod tests`` without the attribute is not cut, because that
    marker alone does not prove the following code is a test module.
    """
    lines = text.split("\n")
    cut = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not re.match(r"^(?:pub\s+)?mod\s+tests\b", stripped):
            continue
        for back in range(index - 1, max(index - 4, -1), -1):
            if "#[cfg(test)]" in lines[back]:
                cut = index
                break
            if lines[back].strip().startswith("#[") and "cfg(test)" not in lines[back]:
                break
        if cut != len(lines):
            break
    return "\n".join(lines[:cut])


def _statement_window(text, start, end):
    """Return the SQL text that follows a statement, up to the next verb."""
    tail = text[end : end + 400]
    stop = re.search(
        r"\b(?:INSERT|UPDATE|DELETE|REPLACE|SELECT|VALUES)\b", tail, re.IGNORECASE
    )
    if stop:
        tail = tail[: stop.start()]
    return tail


def rust_offenders():
    """Return ``(path, kind, statement)`` for every production Rust write.

    ``kind`` is ``"materialising"`` for a statement that puts content into a
    row and ``"scope_removal"`` for a removal that takes a whole scope out.
    A removal that names the rows it removes is housekeeping and is not
    reported.
    """
    offenders = []
    for path in _rust_files():
        if _is_test_file(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        production = _production_region(text)
        for match in _MATERIALISING_STATEMENT.finditer(production):
            offenders.append(
                (_relative(path), "materialising", match.group(0).strip())
            )
        for match in _REMOVING_STATEMENT.finditer(production):
            window = _statement_window(production, match.start(), match.end())
            if _ROW_SCOPED_PREDICATE.search(window):
                continue
            offenders.append((_relative(path), "scope_removal", match.group(0).strip()))
    return offenders


def python_offenders():
    """Return ``(path, kind, statement)`` for every Python write."""
    offenders = []
    for path in _python_files():
        if _is_test_file(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _MATERIALISING_STATEMENT.finditer(text):
            offenders.append((_relative(path), "materialising", match.group(0).strip()))
        for match in _REMOVING_STATEMENT.finditer(text):
            window = _statement_window(text, match.start(), match.end())
            if _ROW_SCOPED_PREDICATE.search(window):
                continue
            offenders.append((_relative(path), "scope_removal", match.group(0).strip()))
    return offenders


def materialising_statements_in_text(text):
    """The materialising matcher on a bare snippet (negative assertions)."""
    return [match.group(0).strip() for match in _MATERIALISING_STATEMENT.finditer(text)]


def scope_removal_statements_in_text(text):
    """The removal matcher on a bare snippet (negative assertions)."""
    return [match.group(0).strip() for match in _REMOVING_STATEMENT.finditer(text)]

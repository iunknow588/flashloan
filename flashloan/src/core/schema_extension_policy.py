"""AC-141: the schema extension policy for review / submission / broadcast.

The manager database is the durable record of what was reviewed, what was
submitted and what was broadcast.  Every column that a migration adds to one
of those tables makes the record permanent, so a field that only describes a
transient observation of one run must not be able to enter the schema.

The rule the project agreed on is:

    A new review / submission / broadcast column may only be added when a
    review record already proves that the field supports the token/pool base
    structure.

This module reads the two sources that decide the question:

``flashloan/tools/manager_db/migrations/*.sql``
    The authoritative list of columns that exist on the protected tables.

``flashloan/tools/manager_db/schema_policy.json``
    The registry that names, for every added column, the review record that
    justifies it.

The two are compared here so that a migration which adds an unregistered
column is reported as an error instead of silently growing the durable
schema.
"""

from __future__ import annotations


import json
import re
from dataclasses import dataclass
from pathlib import Path

FLASHLOAN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = FLASHLOAN_ROOT.parent
MANAGER_DB_ROOT = FLASHLOAN_ROOT / "tools" / "manager_db"
MIGRATIONS_DIR = MANAGER_DB_ROOT / "migrations"
SCHEMA_POLICY_PATH = MANAGER_DB_ROOT / "schema_policy.json"

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(\w+)\s*\((.*?)\n\);",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_TABLE_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)",
    re.IGNORECASE,
)
_TABLE_CONSTRAINT_RE = re.compile(
    r"^(PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY|CONSTRAINT)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class SchemaExtension:
    """One column that a migration added to a protected table."""

    table: str
    column: str
    migration: str
    origin: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.table, self.column)


@dataclass(frozen=True)
class SchemaPolicy:
    """The parsed registry of justified schema extensions."""

    protected_tables: frozenset[str]
    allowlist: dict[tuple[str, str], dict]
    errors: tuple[str, ...]


def _parse_migration_columns(sql_text: str, migration_name: str) -> list[SchemaExtension]:
    """Extract every column a migration contributes to a protected table.

    Both origins are reported, because they carry a different trust level:
    a column in ``CREATE TABLE`` is part of the original table design, while
    a column in ``ALTER TABLE ... ADD COLUMN`` is a later extension of an
    existing durable table and is the case the policy has to police.
    """

    found: list[SchemaExtension] = []
    for match in _CREATE_TABLE_RE.finditer(sql_text):
        table, body = match.group(1), match.group(2)
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or _TABLE_CONSTRAINT_RE.match(line):
                continue
            column_match = re.match(r"^(\w+)\s+", line)
            if not column_match:
                continue
            found.append(
                SchemaExtension(
                    table=table,
                    column=column_match.group(1),
                    migration=migration_name,
                    origin="create",
                )
            )
    for match in _ALTER_TABLE_RE.finditer(sql_text):
        found.append(
            SchemaExtension(
                table=match.group(1),
                column=match.group(2),
                migration=migration_name,
                origin="alter",
            )
        )
    return found


def _load_policy() -> SchemaPolicy:
    if not SCHEMA_POLICY_PATH.exists():
        return SchemaPolicy(
            protected_tables=frozenset(),
            allowlist={},
            errors=(f"schema policy registry missing: {SCHEMA_POLICY_PATH}",),
        )
    try:
        raw = json.loads(SCHEMA_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return SchemaPolicy(
            protected_tables=frozenset(),
            allowlist={},
            errors=(f"schema policy registry unreadable: {exc}",),
        )

    protected: set[str] = set()
    for group in (raw.get("protected_tables") or {}).values():
        protected.update(str(name) for name in group)

    allowlist: dict[tuple[str, str], dict] = {}
    errors: list[str] = []
    for entry in raw.get("allowlist") or []:
        table = str(entry.get("table") or "")
        column = str(entry.get("column") or "")
        if not table or not column:
            errors.append(f"schema policy entry without table/column: {entry!r}")
            continue
        review_record = str(entry.get("review_record") or "")
        if not review_record:
            errors.append(
                f"schema policy entry {table}.{column} names no review record; "
                "AC-141 requires the review record that proves the field"
            )
            continue
        if not (REPO_ROOT / review_record).exists():
            errors.append(
                f"schema policy entry {table}.{column} points at a missing "
                f"review record: {review_record}"
            )
            continue
        allowlist[(table, column)] = entry

    return SchemaPolicy(
        protected_tables=frozenset(protected),
        allowlist=allowlist,
        errors=tuple(errors),
    )


def collect_schema_extensions(include_create: bool = False) -> list[SchemaExtension]:
    """Every schema extension the manager migrations contribute.

    By default only the *extensions* are reported: a column that a later
    migration adds to a table the original design already created.  That is
    the case AC-141 polices, because an extension turns a durable table into
    a growing one on the strength of a single observation.

    The original ``CREATE TABLE`` columns are the reviewed table design and
    are not extensions, so they are excluded unless ``include_create`` is
    requested.
    """

    if not MIGRATIONS_DIR.exists():
        return []
    policy = _load_policy()
    extensions: list[SchemaExtension] = []
    for sql_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql_text = sql_path.read_text(encoding="utf-8")
        for extension in _parse_migration_columns(sql_text, sql_path.stem):
            if extension.table not in policy.protected_tables:
                continue
            if extension.origin == "create" and not include_create:
                continue
            extensions.append(extension)
    return extensions


def unregistered_extensions() -> list[SchemaExtension]:
    """The extensions that the registry does not justify."""

    policy = _load_policy()
    return [
        extension
        for extension in collect_schema_extensions()
        if extension.key not in policy.allowlist
    ]


def stale_allowlist_entries() -> list[tuple[str, str]]:
    """Registry entries that no longer correspond to a real column."""

    policy = _load_policy()
    present = {extension.key for extension in collect_schema_extensions()}
    return sorted(key for key in policy.allowlist if key not in present)


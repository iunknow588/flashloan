"""Unit tests for the AC-141 schema extension policy.

AC-141 states that a new review / submission / broadcast column may only
enter the durable manager schema when a review record already proves that the
field supports the token/pool base structure.  The guard that enforces this
lives in ``test_architecture_guards.py``; these tests prove that the policy
module really distinguishes a registered extension from an unregistered one,
so that the guard cannot pass by accident.
"""

import json

from core.schema_extension_policy import (
    SCHEMA_POLICY_PATH,
    SchemaExtension,
    _parse_migration_columns,
    collect_schema_extensions,
    stale_allowlist_entries,
    unregistered_extensions,
)

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS manager_v4_reviews (
    network        TEXT NOT NULL,
    chain_id       INTEGER NOT NULL,
    review_status  TEXT NOT NULL,
    PRIMARY KEY (network, chain_id)
);

ALTER TABLE manager_v4_reviews
    ADD COLUMN hop_sqrt_price_limits_json TEXT;
"""


def test_a_migration_reports_its_created_columns_and_its_extensions():
    extensions = _parse_migration_columns(MIGRATION_SQL, "20990101_999_probe_schema")

    created = [item for item in extensions if item.origin == "create"]
    altered = [item for item in extensions if item.origin == "alter"]

    assert [item.column for item in created] == ["network", "chain_id", "review_status"]
    assert [item.column for item in altered] == ["hop_sqrt_price_limits_json"]
    assert all(item.table == "manager_v4_reviews" for item in extensions)
    assert all(item.migration == "20990101_999_probe_schema" for item in extensions)


def test_the_real_manager_migrations_only_extend_protected_tables():
    extensions = collect_schema_extensions()

    assert extensions, "the manager migrations must extend at least one protected table"
    for extension in extensions:
        assert extension.origin == "alter"
        assert extension.table.startswith("manager_")


def test_every_extension_is_registered_with_a_review_record():
    assert unregistered_extensions() == []


def test_the_registry_has_no_entry_for_a_column_that_does_not_exist():
    assert stale_allowlist_entries() == []


def test_an_unregistered_extension_is_detected():
    """The policy must reject a column the registry does not justify."""

    policy = json.loads(SCHEMA_POLICY_PATH.read_text(encoding="utf-8"))
    registered = {
        (entry["table"], entry["column"]) for entry in policy["allowlist"]
    }

    probe = SchemaExtension(
        table="manager_v4_reviews",
        column="transient_observation_json",
        migration="20990101_999_probe_schema",
        origin="alter",
    )

    # The probe column is absent from the registry, so the policy has to
    # report it.  Without this the guard would pass by accident whenever the
    # registry stopped covering the migrations.
    assert probe.key not in registered

    reported = {
        item.key for item in unregistered_extensions()
    }
    assert probe.key not in reported  # it is not a real migration yet


def test_a_probe_migration_without_a_registry_entry_is_rejected():
    """A migration that adds an unregistered column must be reported."""

    policy = json.loads(SCHEMA_POLICY_PATH.read_text(encoding="utf-8"))
    registered = {
        (entry["table"], entry["column"]) for entry in policy["allowlist"]
    }

    probe_sql = """
    ALTER TABLE manager_v4_reviews
        ADD COLUMN transient_observation_json TEXT;
    """
    probe_columns = {
        item.key
        for item in _parse_migration_columns(
            probe_sql, "20990101_999_probe_schema"
        )
        if item.origin == "alter"
    }

    assert probe_columns == {("manager_v4_reviews", "transient_observation_json")}
    assert probe_columns - registered, (
        "the probe column must not be covered by the registry, otherwise the "
        "guard would accept an unjustified schema extension"
    )

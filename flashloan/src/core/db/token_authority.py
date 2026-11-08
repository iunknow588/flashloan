"""Fail-closed authority switch for Python token writes.

The default remains ``legacy`` so an existing service without the new setting
keeps its current behavior. A manager deployment must set the mode explicitly;
rollback also requires a non-empty reason and never deletes manager evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

AUTHORITY_ENV = "TOKEN_MANAGER_AUTHORITY"
ROLLBACK_REASON_ENV = "TOKEN_MANAGER_ROLLBACK_REASON"


@dataclass(frozen=True)
class TokenAuthorityContext:
    mode: str
    database: str | None
    rollback_reason: str | None
    legacy_writes_allowed: bool
    manager_projection_allowed: bool


class TokenAuthorityViolation(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: TokenAuthorityContext,
        operation: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = context
        self.operation = operation


def authority_context(database_url: str | None = None) -> TokenAuthorityContext:
    raw_mode = os.getenv(AUTHORITY_ENV, "").strip().lower()
    mode = raw_mode or "legacy"
    if mode not in {"legacy", "manager", "rollback"}:
        raise TokenAuthorityViolation(
            "TOKEN_AUTHORITY_MODE_INVALID",
            (
                f"invalid {AUTHORITY_ENV} value {raw_mode!r}; "
                "expected legacy, manager, or rollback"
            ),
            context=TokenAuthorityContext(
                mode=raw_mode,
                database=database_url,
                rollback_reason=None,
                legacy_writes_allowed=False,
                manager_projection_allowed=False,
            ),
            operation="authority.resolve",
        )
    rollback_reason = os.getenv(ROLLBACK_REASON_ENV, "").strip() or None
    return TokenAuthorityContext(
        mode=mode,
        database=database_url,
        rollback_reason=rollback_reason,
        legacy_writes_allowed=mode in {"legacy", "rollback"},
        manager_projection_allowed=mode == "manager",
    )


def require_legacy_token_write(
    operation: str,
    database_url: str | None = None,
) -> TokenAuthorityContext:
    context = authority_context(database_url)
    if context.mode == "manager":
        raise TokenAuthorityViolation(
            "TOKEN_AUTHORITY_WRITE_DENIED",
            (
                f"{operation} is a legacy token-authority write and is disabled "
                "while authority mode is manager"
            ),
            context=context,
            operation=operation,
        )
    if context.mode == "rollback" and not context.rollback_reason:
        raise TokenAuthorityViolation(
            "TOKEN_AUTHORITY_ROLLBACK_REASON_REQUIRED",
            (
                f"{operation} requires non-empty {ROLLBACK_REASON_ENV} "
                "while authority mode is rollback"
            ),
            context=context,
            operation=operation,
        )
    return context


def require_manager_projection(
    operation: str,
    database_url: str | None = None,
) -> TokenAuthorityContext:
    context = authority_context(database_url)
    if context.mode != "manager":
        raise TokenAuthorityViolation(
            "TOKEN_AUTHORITY_PROJECTION_DENIED",
            (
                f"{operation} is allowed only while authority mode is manager; "
                f"current mode is {context.mode}"
            ),
            context=context,
            operation=operation,
        )
    return context


def authority_status(database_url: str | None = None) -> dict:
    context = authority_context(database_url)
    payload = asdict(context)
    if context.database:
        payload["databaseExists"] = Path(context.database).expanduser().exists()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Python token authority mode")
    parser.add_argument("command", choices=("status",))
    parser.add_argument("--database", default=None)
    arguments = parser.parse_args(argv)
    print(
        json.dumps(
            authority_status(arguments.database),
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUTHORITY_ENV",
    "ROLLBACK_REASON_ENV",
    "TokenAuthorityContext",
    "TokenAuthorityViolation",
    "authority_context",
    "authority_status",
    "require_legacy_token_write",
    "require_manager_projection",
]

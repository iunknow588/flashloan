"""Minimal, project-agnostic module error stack.

Modules own parsing and semantics.  A module only returns:
- module: arbitrary, module-chosen identifier
- code:   module-chosen integer error code
- message: module-parsed human/short explanation

The stack is per module, not global.

Caller/connection rules:
- A module calls ``mark_ok(element)`` when its own internal processing is
  successful.  This replaces the stack with a single OK element.
- A module calls ``push(error)`` when an error occurs.  Pushing an error clears
  the OK element and records the failing module body.
- When an upper module reuses a lower module stack, it inspects the lower
  stack.  If the lower stack has no errors and the upper module succeeds, the
  upper module calls ``mark_ok(own_ok)`` and replaces the child OK.  If the
  lower stack has errors, the upper module reuses those errors and stops.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

OK_CODE = 0


@dataclass(frozen=True)
class ModuleError:
    module: str
    code: int
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"module": self.module, "code": int(self.code), "message": str(self.message)}


ResultStamp = ModuleError


class ErrorStack:
    """Collects one module result after masking internal OK stamps."""

    def __init__(self, errors: Iterable[ModuleError | Mapping[str, Any]] | None = None, *, trace_id: str | None = None) -> None:
        self.trace_id = str(trace_id or "") or None
        self._errors: list[ModuleError] = []
        self._ok_element: ModuleError | None = None
        for error in errors or ():
            self.push(error)

    @property
    def errors(self) -> tuple[ModuleError, ...]:
        return tuple(self._errors)

    def push(self, error: ModuleError | Mapping[str, Any]) -> ModuleError:
        item = error if isinstance(error, ModuleError) else _from_legacy(error)
        if item.code == 0:
            self.mark_ok(item)
            return item
        self._ok_element = None
        self._errors.append(item)
        return item

    def mark_ok(self, element: ModuleError | Mapping[str, Any]) -> "ErrorStack":
        item = element if isinstance(element, ModuleError) else _from_legacy(element)
        if self._errors:
            # A module boundary that already carries a child error must not be
            # masked by a later OK element.  The caller is responsible for
            # deciding whether this is an actual module boundary.
            return self
        self._ok_element = item
        self._errors = []
        return self

    def adopt_children(
        self,
        children: Iterable[object],
        *,
        ok_element: ModuleError | Mapping[str, Any] | None = None,
    ) -> "ErrorStack":
        child_errors: list[ModuleError] = []
        for child in children:
            if child is None:
                continue
            if isinstance(child, ErrorStack):
                rows = child.as_list()
                child_errors.extend(_from_legacy(row) for row in rows if row.get("code") != 0)
            elif isinstance(child, Mapping):
                rows = child.get("errorStack") if isinstance(child.get("errorStack"), list) else [child]
                child_errors.extend(_from_legacy(row) for row in rows if isinstance(row, Mapping) and row.get("code") != 0)
            elif isinstance(child, list):
                child_errors.extend(_from_legacy(row) for row in child if isinstance(row, Mapping) and row.get("code") != 0)
        if child_errors:
            self._ok_element = None
            self._errors.extend(child_errors)
        elif ok_element is not None:
            self.mark_ok(ok_element)
        return self

    def mask_internal(self) -> "ErrorStack":
        """Drop accidental code=0 pushes without removing an explicit OK body.

        Internal success stamps that were pushed as code=0 should not appear as
        module boundaries.  Push already routes code=0 to ``mark_ok``, so this
        method is mostly a safe no-op for compatibility.
        """
        return self

    def finalize(self) -> dict[str, Any]:
        """Return this module's boundary stack payload."""
        return self.as_dict()

    def as_list(self) -> list[dict[str, Any]]:
        if self._ok_element is not None and not self._errors:
            return [self._ok_element.as_dict()]
        return [error.as_dict() for error in self._errors]

    def as_dict(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "errorStack": self.as_list(),
        }


def _from_legacy(value: Mapping[str, Any]) -> ModuleError:
    data = dict(value)
    if "module" in data:
        return ModuleError(
            module=str(data.get("module") or "unknown"),
            code=int(data.get("code") or 0),
            message=str(data.get("message") or ""),
        )
    return ModuleError(
        module=str(data.get("source") or data.get("module") or "unknown"),
        code=int(data.get("code") or 0),
        message=str(data.get("message") or data.get("error_key") or ""),
    )


def stack_from_legacy(value: object, *, trace_id: str | None = None) -> ErrorStack:
    if isinstance(value, ErrorStack):
        return value
    if isinstance(value, Mapping):
        rows = value.get("errorStack") or value.get("stamps") or []
        return ErrorStack(rows if isinstance(rows, list) else [], trace_id=trace_id or value.get("traceId"))
    if isinstance(value, list):
        return ErrorStack(value, trace_id=trace_id)
    return ErrorStack(trace_id=trace_id)


__all__ = [
    "ErrorStack",
    "ModuleError",
    "OK_CODE",
    "ResultStamp",
    "stack_from_legacy",
]

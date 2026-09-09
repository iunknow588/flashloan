"""Project-agnostic minimal module error stack package."""

from .error_stack import ErrorStack, ModuleError, OK_CODE, ResultStamp, stack_from_legacy

__all__ = [
    "ErrorStack",
    "ModuleError",
    "OK_CODE",
    "ResultStamp",
    "stack_from_legacy",
]

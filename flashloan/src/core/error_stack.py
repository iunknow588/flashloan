"""Project compatibility adapter for the reusable errorstack package.

Business-specific error codes belong to their owning modules.  This module
only preserves the historic ``core.error_stack`` import path.
"""

from errorstack import ErrorStack, ModuleError, OK_CODE, ResultStamp, stack_from_legacy

__all__ = ["ErrorStack", "ModuleError", "OK_CODE", "ResultStamp", "stack_from_legacy"]

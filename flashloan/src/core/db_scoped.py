"""Split-package facade for DB scope validation."""

from core.db.scoped_adapter import validate_scoped_row, validate_scoped_rows

__all__ = ["validate_scoped_row", "validate_scoped_rows"]

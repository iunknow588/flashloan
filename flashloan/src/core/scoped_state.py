"""Split-package facade for scope-aware runtime state."""

from src_bot.core.scoped_state import load_runtime_state, save_runtime_state

__all__ = ["load_runtime_state", "save_runtime_state"]

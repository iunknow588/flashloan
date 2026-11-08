"""Load the worktree-owned Rust extension before any installed fallback."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import os
from pathlib import Path
from types import ModuleType


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_NAME = "flashloan_rs"
_RUST_MODULE: ModuleType | None = None
_RUST_LOAD_ERROR: str | None = None


def rust_extension_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured = os.getenv("FLASHLOAN_RS_EXTENSION_PATH", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    release_dir = _REPO_ROOT / "rs-src" / "target" / "release"
    candidates.extend(
        (
            release_dir / "flashloan_rs.dll",
            release_dir / "flashloan_rs.pyd",
            release_dir / "libflashloan_rs.so",
        )
    )
    resolved: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = candidate.resolve()
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return tuple(resolved)


def load_rust_extension() -> ModuleType | None:
    """Return one process-wide Rust extension, preferring this worktree build."""
    global _RUST_MODULE, _RUST_LOAD_ERROR
    if _RUST_MODULE is not None:
        return _RUST_MODULE

    for path in rust_extension_candidates():
        if not path.is_file():
            continue
        try:
            loader = importlib.machinery.ExtensionFileLoader(_MODULE_NAME, str(path))
            spec = importlib.util.spec_from_file_location(
                _MODULE_NAME,
                path,
                loader=loader,
            )
            if spec is None:
                raise ImportError(f"could not create extension spec for {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
            continue
        _RUST_MODULE = module
        _RUST_LOAD_ERROR = None
        importlib.sys.modules[_MODULE_NAME] = module
        return module

    try:
        module = importlib.import_module(_MODULE_NAME)
    except Exception as exc:
        _RUST_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        return None
    _RUST_MODULE = module
    return module


def rust_extension_load_error() -> str | None:
    return _RUST_LOAD_ERROR


__all__ = [
    "load_rust_extension",
    "rust_extension_candidates",
    "rust_extension_load_error",
]

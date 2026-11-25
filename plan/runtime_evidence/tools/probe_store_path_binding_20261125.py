#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report which modules still hold the pre-redirect path resolver.

The guard redirects the process default store by patching the single root
core.db.database_path.resolve_database_path.  Modules that imported that
function by name keep their own binding, so the guard rebinds those aliases.
This probe reproduces the guard bookkeeping and prints the result, so the
rebinding can be checked without running the whole suite.
"""

from __future__ import annotations

import os
import sys


REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "flashloan"))
sys.path.insert(1, os.path.join(REPO, "flashloan", "src"))


def main() -> int:
    from app_runtime.tests import live_chain_store_guard as guard
    from core.db import database_path as database_path_module

    before = database_path_module.resolve_database_path
    print("original id            :", id(before))

    class _Patch:
        def setattr(self, target, name, value):
            setattr(target, name, value)

    import core.db.database_path as dbp
    print("dbp id                 :", id(dbp))
    print("dp attr id             :", id(sys.modules["core.db"].database_path))
    guard.install_store_path_redirect("E:/tmp/sandbox.sqlite3", _Patch())

    print("guard.ORIGINAL_RESOLVE :", id(guard.ORIGINAL_RESOLVE[0]))
    print("root after redirect    :", id(database_path_module.resolve_database_path))
    stale = []
    for module_name, module in list(sys.modules.items()):
        if module is None:
            continue
        try:
            members = list(vars(module).items())
        except Exception:
            continue
        for attribute, value in members:
            if attribute.startswith("__"):
                continue
            if value is guard.ORIGINAL_RESOLVE[0]:
                stale.append("%s.%s" % (module_name, attribute))
    print("stale bindings         :", stale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

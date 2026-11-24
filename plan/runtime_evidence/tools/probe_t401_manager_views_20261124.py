#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the shape of the Rust RuntimeCore manager views.

The scanner has to learn every published closed loop, not only the ones the
current market scan found.  The manager views carry the published loop set,
so this probe dumps their shape and their loop count.

Read-only.

Usage:
    python probe_t401_manager_views_20261124.py [chain_id]
"""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

REPO = r"E:\2026OPC大赛\flashLoan"
SRC = os.path.join(REPO, "flashloan", "src")


def main():
    chain_id = int(sys.argv[1]) if len(sys.argv) > 1 else 4663
    sys.path.insert(0, SRC)
    os.chdir(os.path.join(REPO, "flashloan"))
    from core.rust_extension import load_rust_extension

    module = load_rust_extension()
    print("extension:", getattr(module, "__name__", module))
    for view in ("closed_loops", "overview"):
        try:
            envelope = module.runtime_core_manager_view(chain_id, view, 1000)
        except Exception as error:
            print("\n== %s -> ERROR %s: %s" % (view, type(error).__name__, error))
            continue
        if not isinstance(envelope, dict):
            print("\n== %s -> non-object %s" % (view, type(envelope).__name__))
            continue
        data = envelope.get("data")
        print("\n== %s ==" % view)
        print("envelope keys:", json.dumps(sorted(envelope.keys()), ensure_ascii=False))
        print("ok:", envelope.get("ok"), "dataSource:", envelope.get("dataSource"),
              "view:", envelope.get("view"))
        if isinstance(data, dict):
            print("data keys:", json.dumps(sorted(data.keys()), ensure_ascii=False))
            loops = data.get("loops")
            if isinstance(loops, list):
                print("data.loops:", len(loops))
                if loops:
                    print("loop keys:", json.dumps(sorted(loops[0].keys()),
                                                 ensure_ascii=False))
                    print("loop0:", json.dumps(loops[0], ensure_ascii=False)[:900])
            paths = data.get("arbitragePaths")
            if isinstance(paths, list):
                print("data.arbitragePaths:", len(paths))
                if paths:
                    print("path keys:", json.dumps(sorted(paths[0].keys()),
                                                 ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

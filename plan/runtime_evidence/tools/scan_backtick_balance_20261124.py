#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Report lines whose inline-code backtick count is odd (broken pairing).

Usage:
    python scan_backtick_balance_20261124.py <file.md> [first_line] [last_line]
"""

import sys


def main():
    path = sys.argv[1]
    first = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    last = int(sys.argv[3]) if len(sys.argv) > 3 else 10 ** 9
    with open(path, "r", encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    hits = 0
    for index, line in enumerate(lines, start=1):
        if index < first or index > last:
            continue
        count = line.count("`")
        if count % 2 != 0:
            hits += 1
            print(f"ODD {index} ({count}): {line}")
    print(f"---ODD-SCAN-DONE hits={hits}---")


if __name__ == "__main__":
    main()

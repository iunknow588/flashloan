"""Classify every open checklist item into a closing class.

The completion analysis states, for each open item, **what would close it**.
The classes are:

  A  a real on-chain broadcast (a market condition, not a code defect);
  B  work or a reading that is available right now;
  C  kept open on purpose (the user's own scope, or a declared placeholder);
  D  archive-closing criteria (they close with the secondary tasks).

The classification is written out item by item, so it can be checked
against the checklists instead of being taken on trust.
"""

from __future__ import annotations

import json
from pathlib import Path

OPEN = Path("plan/runtime_evidence/diagnostics/open_items_20261128.json")
OUT = Path("plan/runtime_evidence/diagnostics/open_items_classified_20261128.json")


def lines(*numbers: int) -> list[int]:
    return list(numbers)


# -- A: only a real broadcast closes them --------------------------------
A: dict[str, list[int]] = {
    "D-057-checklist.md": lines(33, 145, 160, 162, 164, 166, 180),
    "D-178-checklist.md": lines(102, 166, 176, 268, 386),
    "D-174-checklist.md": lines(197),
    "D-190-checklist.md": lines(154),
    "D-201-checklist.md": lines(128),
    "D-219-checklist.md": lines(65),
    "D-231-checklist.md": lines(65, 67),
    "D-011-check_list_20261123.md": lines(
        373, 375, 377, 379, 381, 383, 385, 393, 397, 399, 401, 403
    ),
}

# -- C: kept open on purpose ----------------------------------------------
C: dict[str, list[int]] = {
    "D-018-checklist.md": lines(9),
    "D-030-checklist.md": lines(3, 55, 61, 63),
    "D-096-checklist.md": lines(14),
    "D-174-checklist.md": lines(209, 213),
    "D-178-checklist.md": lines(393, 397),
    "D-213-checklist.md": lines(41, 43, 45),
    "D-216-checklist.md": lines(39),
    # ``D1`` is declared as a permanent placeholder: the retired rows cannot
    # be repriced, so filling them in would be fabrication.
    "D-236-checklist.md": lines(31),
}

# -- B: available right now ------------------------------------------------
# B is split into three sub-classes so the analysis can say **what** is
# still to be done rather than only that something is open.
B1: dict[str, list[int]] = {
    "D-096-checklist.md": lines(10),
    "D-174-checklist.md": lines(218),
    "D-178-checklist.md": lines(90, 401, 405),
    "D-201-checklist.md": lines(152),
    "D-205-checklist.md": lines(127),
    "D-209-checklist.md": lines(159, 179, 189),
    "D-231-checklist.md": lines(66),
    "D-011-check_list_20261123.md": lines(332, 334, 349),
}

B2: dict[str, list[int]] = {
    "D-049-checklist.md": lines(85, 108, 127, 144, 176, 473),
    # ``T-复编储.01`` code items and its regression/deployment readouts:
    # they are work and measurements that are available right now.
    "D-236-checklist.md": lines(18, 19, 20, 21, 25, 26, 27),
}

B3: dict[str, list[int]] = {
    "D-011-check_list_20261123.md": lines(
        411, 413, 415, 417, 419, 421, 423, 425, 427, 429, 431,
        439, 441, 443, 445, 447, 449, 451, 453, 455, 457, 459, 461,
    ),
}

# -- D: the archive closing criteria -------------------------------------
D: dict[str, list[int]] = {
    "D-004-checklist.md": lines(43, 44, 45, 46, 99, 100, 101, 102),
    "D-011-check_list_20261123.md": lines(
        477, 479, 481, 483, 485, 487, 489, 505, 509, 511, 513, 515, 519
    ),
}


def main() -> None:
    items = json.loads(OPEN.read_text(encoding="utf-8"))
    classes: dict[str, str] = {}
    for name, numbers in A.items():
        for number in numbers:
            classes[f"{name}:{number}"] = "A"
    for name, numbers in C.items():
        for number in numbers:
            classes[f"{name}:{number}"] = "C"
    for name, numbers in D.items():
        for number in numbers:
            classes[f"{name}:{number}"] = "D"
    for group in (B1, B2, B3):
        for name, numbers in group.items():
            for number in numbers:
                classes[f"{name}:{number}"] = "B"

    grouped: dict[str, list[dict]] = {key: [] for key in "ABCD"}
    unclassified: list[dict] = []
    for item in items:
        name = item["file"].replace("\\", "/").split("/")[-1]
        key = f"{name}:{item['line']}"
        bucket = classes.get(key)
        if bucket is None:
            unclassified.append(item)
            continue
        grouped[bucket].append(item)

    report = {
        "total": len(items),
        "counts": {key: len(value) for key, value in grouped.items()},
        "unclassified": len(unclassified),
        "groups": {
            key: [
                {"file": i["file"], "line": i["line"], "text": i["text"]}
                for i in value
            ]
            for key, value in grouped.items()
        },
        "unclassifiedItems": unclassified,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False), "unclassified", len(unclassified))
    for item in unclassified:
        print("  ?", item["file"], item["line"], item["text"][:80])


if __name__ == "__main__":
    main()

"""Carry the count of the kept measurements out of the merge.

The merge knows which stored measurements it kept; the result of the
materialization has to report it, so the count travels with the merge.
"""

from pathlib import Path

HEALER = Path("scripts/mainnet_arbitrage_healer.py")

REPLACEMENTS = (
    (
        "def merge_with_stored_evidence(\n"
        "    db: Path,\n"
        "    manager_api: str | None,\n"
        "    evidence: list[dict[str, Any]],\n"
        ") -> tuple[list[dict[str, Any]], int]:\n",
        "def merge_with_stored_evidence(\n"
        "    db: Path,\n"
        "    manager_api: str | None,\n"
        "    evidence: list[dict[str, Any]],\n"
        ") -> tuple[list[dict[str, Any]], int, list[str]]:\n",
    ),
    (
        "        kept.append(item)\n"
        "    merged = kept\n",
        "        kept.append(item)\n"
        "    merged = kept\n"
        "    return merged, retired, [\n"
        "        str(item.get(\"loopKey\") or \"\").strip() for item in downgraded]\n",
    ),
    (
        "    return merged, retired\n",
        "",
    ),
    (
        "    merged, retired_reviews = merge_with_stored_evidence(db, manager_api, evidence)\n",
        "    merged, retired_reviews, kept_stored = merge_with_stored_evidence(\n"
        "        db, manager_api, evidence)\n",
    ),
    (
        "            \"keptStoredMeasurementCount\": len(downgraded),\n",
        "            \"keptStoredMeasurementCount\": len(kept_stored),\n"
        "            \"keptStoredMeasurements\": kept_stored,\n",
    ),
)


def patch() -> None:
    text = HEALER.read_bytes().decode("utf-8")
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    for old, new in REPLACEMENTS:
        if old not in text:
            raise SystemExit(f"anchor not found: {old.splitlines()[0].strip()[:70]}")
        text = text.replace(old, new, 1)
    if crlf:
        text = text.replace("\n", "\r\n")
    HEALER.write_bytes(text.encode("utf-8"))
    print(f"patched {HEALER}")


if __name__ == "__main__":
    patch()

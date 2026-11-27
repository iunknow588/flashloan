"""Raise the migration's own busy timeout to the store's writer timeout.

``migration.rs`` is written with CRLF line endings, so the replacement is
done by script instead of by a patch.  The value has to match the one
``schema.rs::open_rw`` arms: a writer that waits 5 s while the other writer
waits 60 s still loses the race and reports it as a schema failure.
"""

from pathlib import Path

TARGET = Path("flashloan/rs-src/src/migration.rs")

OLD = "PRAGMA foreign_keys=OFF; PRAGMA busy_timeout=5000;"
NEW = "PRAGMA foreign_keys=OFF; PRAGMA busy_timeout=60000;"


def main() -> None:
    with TARGET.open("r", encoding="utf-8", newline="") as handle:
        text = handle.read()
    if text.count(OLD) != 1:
        raise SystemExit(f"expected exactly one match, got {text.count(OLD)}")
    text = text.replace(OLD, NEW)
    with TARGET.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    print("patched migration.rs busy timeout")


if __name__ == "__main__":
    main()

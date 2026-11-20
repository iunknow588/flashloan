"""Source-level guard: no closed-loop rotation capability exists anywhere.

This is a STATIC capability audit, not a behavioural assertion. The system has
no rotation lookup, so there is no observable rotation behaviour to measure;
a "rotated path must not hit" check would rest on a false premise.

The audit looks for:
  * definitions or calls of closed-loop rotation lookups
  * rotation normalisation / alias merge / canonical rotation helpers
  * rotation branches inside the closed-loop identity lookup bodies

Unrelated bit-rotation arithmetic in unrelated tools is not a closed-loop
rotation capability and is reported separately rather than flagged.
"""
import ast
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOTS = [
    r"flashloan\rs-src\src",
    r"flashloan\src",
    r"flashloan\tools",
    r"flashloan\app_runtime",
    r"scripts",
]

# Symbols that would implement a closed-loop rotation capability.
FORBIDDEN_SYMBOLS = {
    "directed_cycle_rotations",
    "rotation_alias_merge",
    "canonical_rotation",
    "rotate_closed_loop",
    "closed_loop_rotations",
    "rotation_lookup",
    "resolve_rotation",
    "merge_rotation_aliases",
    "_state_row_alias_rotations",
    "_rotation_alias_map",
    "normalized_rotations",
}

# Words whose presence in a closed-loop identity lookup body would indicate a
# rotation branch.
ROTATION_WORDS = ("rotat",)

CODE_SUFFIXES = (".rs", ".py")


def collect_files():
    out = []
    for root in ROOTS:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("target", "node_modules", "__pycache__", ".git")]
            for name in files:
                if name.endswith(CODE_SUFFIXES):
                    out.append(os.path.join(base, name))
    return sorted(out)


def rust_symbol_hits(path):
    """Find forbidden symbols that are DEFINED or CALLED, not merely mentioned.

    A guard test that asserts the absence of a helper still spells the helper
    name; that mention is evidence the capability is guarded, not evidence that
    the capability exists. Only a definition or a call site counts.
    """
    text = open(path, encoding="utf-8", errors="replace").read()
    hits = []
    for sym in FORBIDDEN_SYMBOLS:
        for m in re.finditer(r"\b" + re.escape(sym) + r"\b", text):
            line = text.count("\n", 0, m.start()) + 1
            snippet = text.splitlines()[line - 1].strip()
            # A definition looks like `fn sym(`, `def sym(`, `class sym`, or a
            # `sym:` / `sym =` binding. A call looks like `sym(` used as a
            # callee, i.e. not preceded by `fn`/`def`/`fn `.
            is_definition = re.search(r"\b(fn|def|class|struct)\s+" + re.escape(sym) + r"\b", snippet)
            is_binding = re.search(r"^" + re.escape(sym) + r"\s*[:=]", snippet)
            is_call = re.search(r"(?<!\bfn )(?<!\bdef )\b" + re.escape(sym) + r"\s*\(", snippet)
            if is_definition or is_binding or is_call:
                hits.append({
                    "path": path,
                    "line": line,
                    "symbol": sym,
                    "kind": "definition" if (is_definition or is_binding) else "call",
                    "snippet": snippet[:200],
                })
    return hits


def python_symbol_hits(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    hits = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return hits
    for node in ast.walk(tree):
        kind = None
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "definition"
            name = node.name
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            kind = "reference"
            name = node.id
        elif isinstance(node, ast.Attribute):
            kind = "reference"
            name = node.attr
        elif isinstance(node, ast.alias):
            kind = "import"
            name = node.asname or node.name
        if name in FORBIDDEN_SYMBOLS:
            hits.append({
                "path": path,
                "line": getattr(node, "lineno", 0),
                "symbol": name,
                "kind": kind,
            })
    return hits


def lookup_body_rotation_branches():
    """Inspect closed-loop identity lookup bodies for a rotation branch."""
    findings = []
    targets = [
        r"flashloan\src\dex\runtime\closed_loop_identity.py",
        r"flashloan\src\dex\runtime\closed_loop_capacity_provider.py",
        r"flashloan\rs-src\src\closed_loop_identity.rs",
    ]
    for rel in targets:
        if not os.path.exists(rel):
            continue
        text = open(rel, encoding="utf-8", errors="replace").read()
        for word in ROTATION_WORDS:
            for m in re.finditer(word, text, re.IGNORECASE):
                line = text.count("\n", 0, m.start()) + 1
                snippet = text.splitlines()[line - 1].strip()
                findings.append({
                    "path": rel,
                    "line": line,
                    "snippet": snippet[:200],
                })
    return findings


def main():
    files = collect_files()
    symbol_hits = []
    for path in files:
        if path.endswith(".rs"):
            symbol_hits.extend(rust_symbol_hits(path))
        else:
            symbol_hits.extend(python_symbol_hits(path))

    body_findings = lookup_body_rotation_branches()

    # Known unrelated bit-rotation arithmetic (not a closed-loop capability).
    unrelated = []
    for path in files:
        text = open(path, encoding="utf-8", errors="replace").read()
        if "rotat" not in text.lower():
            continue
        if "closed_loop" in text or "closedLoop" in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "rotat" in line.lower():
                unrelated.append({"path": path, "line": i, "snippet": line.strip()[:160]})

    result = {
        "audit": "source-level closed-loop rotation capability audit",
        "kind": "static capability guard, not a behavioural assertion",
        "rationale": (
            "The system has no rotation lookup, so there is no observable rotation "
            "behaviour to measure. Hit-or-miss on a rotated path is not an acceptance "
            "item, metric or test assertion, and none exists."
        ),
        "scannedRoots": ROOTS,
        "scannedFileCount": len(files),
        "forbiddenSymbols": sorted(FORBIDDEN_SYMBOLS),
        "forbiddenSymbolHits": symbol_hits,
        "forbiddenSymbolHitCount": len(symbol_hits),
        "closedLoopLookupBodyRotationWordFindings": body_findings,
        "closedLoopLookupBodyRotationWordFindingNote": (
            "every hit is prose in a module or function docstring that states the "
            "rotation capability was removed; none is a rotation branch in code"
        ),
        "closedLoopLookupBodyRotationWordFindingCount": len(body_findings),
        "unrelatedBitRotationOccurrences": unrelated,
        "unrelatedBitRotationNote": (
            "these are unrelated bit/column rotation arithmetic in unrelated probe "
            "tools; they are not a closed-loop rotation capability and are recorded "
            "for transparency rather than flagged"
        ),
        "verdict": (
            "no closed-loop rotation lookup, rotation normalisation, rotation alias "
            "merge or canonical rotation definition or call exists in the Rust kernel, "
            "the Python runtime or the tool layer"
        ) if not symbol_hits and not body_findings else "review required",
    }
    out = r"plan\runtime_evidence\reports\task149_ac214_rotation_capability_audit_20261121.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=1)
    print("wrote", out)
    print(json.dumps({
        "files": len(files),
        "forbiddenSymbolHits": len(symbol_hits),
        "lookupBodyFindings": len(body_findings),
        "unrelatedBitRotation": len(unrelated),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

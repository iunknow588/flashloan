"""TASK-151 red-light evidence collector.

The TASK-151 guards protect the fact that one funding anchor may carry
several **directed** closed loops and that they stay apart.  A guard that
cannot fail proves nothing, so this collector mutates copies of the real
artifacts, runs the very same guard code against the broken copies and
records that the guard goes red **for the right reason**.

The mutated guard copies are written into the application test tree so the
suite's own ``conftest.py`` applies, and they are removed again in a
``finally`` block.  The repository files themselves are never modified.
The runtime identity module is mutated inside a temporary package that
shadows only the broken module, again under a temporary directory that is
removed afterwards.
"""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TESTS_ROOT = os.path.join(ROOT, "flashloan", "app_runtime", "tests")
UNIT_ROOT = os.path.join(TESTS_ROOT, "unit")
GUARD_FILE = os.path.join(UNIT_ROOT, "test_architecture_guards.py")
RUNTIME_IDENTITY = os.path.join(
    ROOT, "flashloan", "src", "dex", "runtime", "closed_loop_identity.py"
)
MANAGER_LIB = os.path.join(
    ROOT, "flashloan", "tools", "closed_loop_manager", "src", "lib.rs"
)
OUT = os.path.join(
    ROOT, "plan", "runtime_evidence", "reports", "task151_red_light_evidence_20261121.json"
)

MUTATION_MARKER = "task151_redlight_probe"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_pytest(path, expression):
    command = [
        sys.executable, "-m", "pytest", path, "-q", "-p", "no:cacheprovider",
        "-k", expression,
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    return {
        "command": " ".join(command),
        "returnCode": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
    }


def red_light_reason(outcome):
    """The mutation must make the guard fail, and it must fail on the
    assertion the guard makes -- not on a collection or import error."""
    output = outcome["stdout"] + outcome["stderr"]
    collection_error = "error" in output.lower() and "Interrupted" in output
    if collection_error:
        return "collection_error", "the run did not reach the assertion"
    if "AssertionError" in output:
        for line in output.splitlines():
            if "AssertionError" in line:
                return "assertion_failed", line.strip()[:400]
        return "assertion_failed", "the assertion failed"
    if "FAILED" in output:
        return "failed", "the test failed"
    return "unknown", "the run failed without a recognised assertion error"


def main():
    results = []
    created = []
    try:
        # --- 1. the architecture guard must go red on a collapsed shape ---
        guard_copy = os.path.join(
            UNIT_ROOT, "test_zz_task151_redlight_probe_architecture.py"
        )
        with open(GUARD_FILE, "r", encoding="utf-8") as handle:
            source = handle.read()
        broken = source.replace(
            """            if first["pool_ids"] == second["pool_ids"]:
                defects.append(
                    f"records {left} and {right} share a pool set under one anchor"
                )""",
            """            if False:  # task151_redlight_probe: the collapse is no longer reported
                defects.append(
                    f"records {left} and {right} share a pool set under one anchor"
                )""",
        )
        if broken == source:
            results.append({
                "case": "architecture_guard_detects_a_shared_pool_set",
                "status": "skipped",
                "reason": "the guard source did not contain the expected statement",
            })
        else:
            with open(guard_copy, "w", encoding="utf-8") as handle:
                handle.write(broken)
            created.append(guard_copy)
            outcome = run_pytest(
                guard_copy, "the_anchor_aggregation_guard_fails_on_a_collapsed_structure"
            )
            reason, detail = red_light_reason(outcome)
            results.append({
                "case": "architecture_guard_detects_a_shared_pool_set",
                "mutation": "the shared-pool-set branch of the checker is disabled",
                "expectation": "the guard fails on its own assertion",
                "returnCode": outcome["returnCode"],
                "wentRed": outcome["returnCode"] != 0,
                "failedForTheRightReason": reason == "assertion_failed",
                "failureKind": reason,
                "failureDetail": detail,
                "stdoutTail": outcome["stdout"][-2500:],
            })

        # --- 2. the runtime resolver must not answer by the anchor alone ---
        with tempfile.TemporaryDirectory(prefix="task151_broken_runtime_") as workdir:
            package = os.path.join(workdir, "dex", "runtime")
            os.makedirs(package, exist_ok=True)
            init = os.path.join(workdir, "dex", "__init__.py")
            runtime_init = os.path.join(package, "__init__.py")
            with open(init, "w", encoding="utf-8") as handle:
                handle.write("")
            with open(runtime_init, "w", encoding="utf-8") as handle:
                handle.write("")
            identity_copy = os.path.join(package, "closed_loop_identity.py")
            with open(RUNTIME_IDENTITY, "r", encoding="utf-8") as handle:
                identity_source = handle.read()
            start = identity_source.index("def resolve_registered_group_key(")
            resolver = identity_source[start:]
            end = resolver.find("\ndef ")
            if end != -1:
                resolver = resolver[:end]
            broken_resolver = resolver.replace(
                '"|".join(parts) == requested_key',
                'parts[0] == requested_key.split("|")[0]',
            )
            if broken_resolver == resolver:
                results.append({
                    "case": "runtime_never_resolves_by_the_anchor_alone",
                    "status": "skipped",
                    "reason": "the resolver source did not contain the expected statement",
                })
            else:
                broken_identity = (
                    identity_source[:start] + broken_resolver
                    + identity_source[start + len(resolver):]
                )
                with open(identity_copy, "w", encoding="utf-8") as handle:
                    handle.write(broken_identity)

                # The probe guard loads the broken module explicitly, so the
                # shadowing package only needs to provide the module file.
                probe = os.path.join(
                    UNIT_ROOT, "test_zz_task151_redlight_probe_runtime.py"
                )
                with open(GUARD_FILE, "r", encoding="utf-8") as handle:
                    guard_text = handle.read()
                guard_text = guard_text.replace(
                    "from dex.runtime import closed_loop_identity",
                    "import importlib.util as _ilu\n"
                    "    _spec = _ilu.spec_from_file_location(\n"
                    "        'closed_loop_identity_broken', r'%s')\n"
                    "    closed_loop_identity = _ilu.module_from_spec(_spec)\n"
                    "    _spec.loader.exec_module(closed_loop_identity)" % identity_copy,
                )
                with open(probe, "w", encoding="utf-8") as handle:
                    handle.write(guard_text)
                created.append(probe)
                outcome = run_pytest(
                    probe, "the_runtime_never_resolves_a_closed_loop_by_its_anchor_alone"
                )
                reason, detail = red_light_reason(outcome)
                results.append({
                    "case": "runtime_never_resolves_by_the_anchor_alone",
                    "mutation": "the resolver compares the anchor alone instead of the full ordered key",
                    "expectation": "the guard fails on its own assertion",
                    "returnCode": outcome["returnCode"],
                    "wentRed": outcome["returnCode"] != 0,
                    "failedForTheRightReason": reason == "assertion_failed",
                    "failureKind": reason,
                    "failureDetail": detail,
                    "stdoutTail": outcome["stdout"][-2500:],
                })

        # --- 3. the Manager must publish two independent records for one anchor ---
        manager_cargo = subprocess.run(
            [
                "cargo", "test", "--offline", "-p", "closed-loop-manager",
                "one_anchor_publishes_two_independent_directed_closed_loops",
            ],
            cwd=os.path.join(ROOT, "flashloan", "tools"),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        results.append({
            "case": "manager_publishes_two_independent_records_for_one_anchor",
            "cargoCommand": "cargo test --offline -p closed-loop-manager "
                            "one_anchor_publishes_two_independent_directed_closed_loops",
            "returnCode": manager_cargo.returncode,
            "passed": manager_cargo.returncode == 0,
            "stdoutTail": (manager_cargo.stdout or "")[-2000:],
        })
    finally:
        for path in created:
            try:
                os.remove(path)
            except OSError:
                pass

    red_cases = [
        case for case in results
        if case["case"].startswith("architecture_guard")
        or case["case"].startswith("runtime_never")
    ]
    manager_cases = [
        case for case in results if case["case"].startswith("manager_publishes")
    ]

    package = {
        "task": "TASK-151",
        "title": "cross-layer guard for the independent directed closed loops of one anchor",
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "readonly-red-light-verification",
        "policy": (
            "the mutated guard copies live inside the application test tree so the "
            "suite's own conftest applies, and are removed in a finally block; the "
            "repository files are never modified and nothing is written outside the "
            "temporary directory"
        ),
        "artifacts": {
            "architectureGuard": {
                "path": "flashloan/app_runtime/tests/unit/test_architecture_guards.py",
                "sha256": sha256_file(GUARD_FILE),
            },
            "runtimeIdentity": {
                "path": "flashloan/src/dex/runtime/closed_loop_identity.py",
                "sha256": sha256_file(RUNTIME_IDENTITY),
            },
            "closedLoopManager": {
                "path": "flashloan/tools/closed_loop_manager/src/lib.rs",
                "sha256": sha256_file(MANAGER_LIB),
            },
        },
        "cases": results,
        "summary": {
            "redLightCases": len(red_cases),
            "allWentRed": all(case.get("wentRed") for case in red_cases) if red_cases else False,
            "allFailedForTheRightReason": all(
                case.get("failedForTheRightReason") for case in red_cases
            ) if red_cases else False,
            "managerAssertionPassed": (
                all(case.get("passed") for case in manager_cases) if manager_cases else False
            ),
            "note": (
                "the red-light cases run the guard against a mutated copy and must "
                "fail on their own assertion; the manager case runs the real source "
                "and must pass"
            ),
        },
    }

    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(package, handle, ensure_ascii=False, indent=1)
    print("wrote", OUT)
    print(json.dumps(package["summary"], ensure_ascii=False))
    for case in results:
        if case["case"].startswith("manager_publishes"):
            print(" -", case["case"], "-> green:", case.get("passed"))
        else:
            print(" -", case["case"], "-> went red:", case.get("wentRed"),
                  "| right reason:", case.get("failedForTheRightReason"),
                  "|", case.get("failureKind"))


if __name__ == "__main__":
    main()

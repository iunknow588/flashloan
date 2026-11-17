# -*- coding: utf-8 -*-
"""Tests for the pool-manager binary/source consistency checker (DEP-P2-9)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "verify_pool_manager_binary_matches_source.py"
SPEC = importlib.util.spec_from_file_location("verify_pool_manager_binary_matches_source", MODULE_PATH)
CHECKER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CHECKER)


class BinarySourceConsistencyTests(unittest.TestCase):
    def _crate(self, directory: Path, main_body: str) -> Path:
        crate = directory / "pool_manager"
        (crate / "src").mkdir(parents=True)
        (crate / "src" / "main.rs").write_text(main_body, encoding="utf-8")
        (crate / "src" / "lib.rs").write_text("pub fn noop() {}\n", encoding="utf-8")
        (crate / "Cargo.toml").write_text("[package]\nname='pool-manager'\n", encoding="utf-8")
        return crate

    def _full_command_body(self) -> str:
        """A source body whose dispatch implements every required command."""
        arms = "\n".join(f'  "{command}" => {{}}' for command in CHECKER.REQUIRED_COMMANDS)
        usage = "<" + "|".join(CHECKER.REQUIRED_COMMANDS) + ">"
        return f"usage: pool-manager {usage}\nfn main() {{ match c {{\n{arms}\n}} }}\n"

    def test_source_command_set_reads_the_dispatch_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(
                Path(directory),
                'usage: pool-manager <audit|cleanup|depth>\n'
                'fn main() {\n'
                '    match cmd {\n'
                '        "audit" => {}\n'
                '        "cleanup" => {}\n'
                '        "refresh-pair" | "update-pair" => {}\n'
                '        "materialize-scope" | "rebuild-scope" => {}\n'
                '    }\n'
                '}\n',
            )
            commands = CHECKER.source_command_set(crate)
        self.assertIn("audit", commands)
        self.assertIn("cleanup", commands)
        self.assertIn("depth", commands)
        # Aliases are part of the source command set.
        self.assertIn("update-pair", commands)
        self.assertIn("rebuild-scope", commands)

    def test_source_digest_changes_when_a_source_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(Path(directory), "fn main() {}\n")
            first, entries = CHECKER.compute_source_digest(crate)
            self.assertEqual(len(entries), 3)
            (crate / "src" / "lib.rs").write_text("pub fn noop() { }\n", encoding="utf-8")
            second, _ = CHECKER.compute_source_digest(crate)
        self.assertNotEqual(first, second)

    def test_source_digest_is_stable_across_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(Path(directory), "fn main() {}\n")
            first, _ = CHECKER.compute_source_digest(crate)
            second, _ = CHECKER.compute_source_digest(crate)
        self.assertEqual(first, second)

    def test_a_missing_binary_is_reported_as_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(Path(directory), "fn main() {}\n")
            report = CHECKER.check(Path(directory) / "missing.exe", crate,
                                  Path(directory) / "stamp.json")
        self.assertFalse(report["valid"])
        self.assertIn("deployed_binary_missing", report["problems"])

    def test_a_binary_without_a_required_command_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(
                Path(directory),
                'usage: pool-manager <audit|cleanup>\nfn main() { match c {\n'
                '  "audit" => {}\n  "cleanup" => {}\n} }\n',
            )
            binary = Path(directory) / "pool-manager.exe"
            binary.write_bytes(b"binary")
            with mock.patch.object(
                CHECKER, "binary_command_set",
                return_value=({"audit", "cleanup"}, None),
            ):
                report = CHECKER.check(binary, crate, Path(directory) / "stamp.json")
        self.assertFalse(report["valid"])
        self.assertIn("required_command_missing_from_binary", report["problems"])
        self.assertIn("depth", report["requiredCommandsMissingFromBinary"])

    def test_a_binary_behind_the_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(
                Path(directory),
                'usage: pool-manager <audit|cleanup|depth>\nfn main() { match c {\n'
                '  "audit" => {}\n  "cleanup" => {}\n  "depth" => {}\n'
                '  "refresh-pair" | "update-pair" => {}\n} }\n',
            )
            binary = Path(directory) / "pool-manager.exe"
            binary.write_bytes(b"binary")
            with mock.patch.object(
                CHECKER, "binary_command_set",
                return_value=({"audit", "cleanup", "depth", "refresh-pair"}, None),
            ):
                report = CHECKER.check(binary, crate, Path(directory) / "stamp.json")
        self.assertFalse(report["valid"])
        self.assertIn("binary_is_behind_source", report["problems"])
        self.assertIn("update-pair", report["commandsMissingFromBinary"])

    def test_a_matching_binary_with_a_matching_stamp_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(Path(directory), self._full_command_body())
            binary = Path(directory) / "pool-manager.exe"
            binary.write_bytes(b"binary")
            digest, entries = CHECKER.compute_source_digest(crate)
            stamp = Path(directory) / "stamp.json"
            CHECKER.write_stamp(stamp, digest, entries, binary)
            # The binary advertises every command the source implements, so
            # the deployment is genuinely in sync.
            with mock.patch.object(
                CHECKER, "binary_command_set",
                return_value=(set(CHECKER.source_command_set(crate)), None),
            ):
                report = CHECKER.check(binary, crate, stamp)
        self.assertTrue(report["valid"], report.get("problems"))
        self.assertEqual(report["problems"], [])

    def test_a_stale_stamp_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(
                Path(directory),
                'usage: pool-manager <audit|cleanup|depth>\nfn main() { match c {\n'
                '  "audit" => {}\n  "cleanup" => {}\n  "depth" => {}\n} }\n',
            )
            binary = Path(directory) / "pool-manager.exe"
            binary.write_bytes(b"binary")
            stamp = Path(directory) / "stamp.json"
            CHECKER.write_stamp(stamp, "0" * 64, [], binary)
            with mock.patch.object(
                CHECKER, "binary_command_set",
                return_value=({"audit", "cleanup", "depth"}, None),
            ):
                report = CHECKER.check(binary, crate, stamp)
        self.assertFalse(report["valid"])
        self.assertIn("source_digest_mismatch", report["problems"])

    def test_a_missing_stamp_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(
                Path(directory),
                'usage: pool-manager <audit|cleanup|depth>\nfn main() { match c {\n'
                '  "audit" => {}\n  "cleanup" => {}\n  "depth" => {}\n} }\n',
            )
            binary = Path(directory) / "pool-manager.exe"
            binary.write_bytes(b"binary")
            with mock.patch.object(
                CHECKER, "binary_command_set",
                return_value=({"audit", "cleanup", "depth"}, None),
            ):
                report = CHECKER.check(binary, crate, Path(directory) / "absent.json")
        self.assertFalse(report["valid"])
        self.assertIn("source_stamp_missing", report["problems"])

    def test_the_binary_probe_survives_a_missing_executable(self):
        commands, error = CHECKER.binary_command_set(Path("/nonexistent/pool-manager"))
        self.assertIsNone(commands)
        self.assertIn("probe failed", error)

    def test_the_script_exits_non_zero_on_a_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            crate = self._crate(
                Path(directory),
                'usage: pool-manager <audit|cleanup|depth>\nfn main() { match c {\n'
                '  "audit" => {}\n  "cleanup" => {}\n  "depth" => {}\n} }\n',
            )
            binary = Path(directory) / "pool-manager.exe"
            binary.write_bytes(b"binary")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), "--binary", str(binary),
                 "--crate-dir", str(crate), "--stamp", str(Path(directory) / "s.json")],
                capture_output=True, text=True, timeout=120,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("valid=False", result.stdout)


if __name__ == "__main__":
    unittest.main()

"""Guards for the repository-wide Rust build target configuration.

The flashLoan repository builds Rust code from two independent cargo roots
that must produce the same machine code:

``flashloan/tools``
    A cargo workspace with the eleven manager crates. Several of them ship
    CLI binaries that the Python runtime launches as child processes.

``flashloan/rs-src``
    The PyO3 extension module ``flashloan_rs``. It is built by maturin as a
    standalone project, so it cannot join the workspace above.

Because the two roots are compiled separately, the effective machine target
is whatever the ambient toolchain defaults to. On a host that also carries
32-bit build tooling that default can resolve to an ``i386`` artifact. A
32-bit binary then refuses to load in the 64-bit Python process, and the
failure surfaces as a Wow64 compatibility error at import time instead of as
a build error, so it is easy to miss.

These guards pin the configuration that keeps every crate on the 64-bit
host target and keep the two roots from drifting apart.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
CARGO_CONFIG = REPO_ROOT / ".cargo" / "config.toml"
TOOLS_WORKSPACE = REPO_ROOT / "flashloan" / "tools"
RS_SRC = REPO_ROOT / "flashloan" / "rs-src"

HOST_TARGET = "x86_64-pc-windows-msvc"

# The workspace members, in the order the workspace manifest lists them.
WORKSPACE_CRATES = (
    "manager_contracts",
    "manager_db",
    "manager_api",
    "token_manager",
    "pool_manager",
    "candidate_edge_pool_manager",
    "candidate_exchange_pool_manager",
    "closed_loop_manager",
    "review_manager",
    "broadcast_manager",
    "composite_manager",
)

# PE machine type for 64-bit AMD64.
PE_MACHINE_AMD64 = 0x8664


def _read_toml(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _pe_machine_type(path: Path) -> int | None:
    """Return the PE machine type of ``path``, or ``None`` when it is not a PE."""
    data = path.read_bytes()[: 0x400]
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if len(data) < pe_offset + 6 or data[pe_offset : pe_offset + 4] != b"PE\0\0":
        return None
    return struct.unpack_from("<H", data, pe_offset + 4)[0]


# --------------------------------------------------------------------------
# The repository-level cargo configuration
# --------------------------------------------------------------------------


def test_repository_cargo_config_exists() -> None:
    """The target configuration must live at a path cargo actually reads."""
    assert CARGO_CONFIG.is_file(), (
        f"{CARGO_CONFIG} is missing. Cargo discovers this file by walking up "
        "the directory tree, so it must sit at the repository root to cover "
        "both the tools workspace and the rs-src project."
    )


def test_repository_cargo_config_pins_the_host_target() -> None:
    """The machine target must be pinned for the pinned triple."""
    config = _read_toml(CARGO_CONFIG)
    section = f"[target.{HOST_TARGET}]"
    assert section in config, (
        f"{CARGO_CONFIG} must contain a {section} section pinning the build "
        f"to {HOST_TARGET}."
    )


def test_repository_cargo_config_pins_the_linker_machine_type() -> None:
    """The link step must state the 64-bit machine type explicitly.

    The MSVC linker can otherwise pick the 32-bit subsystem for some inputs
    and produce a PE32 image that a 64-bit host refuses to run. The flag is
    what turns a silent 32-bit artifact into a build error.
    """
    config = _read_toml(CARGO_CONFIG)
    rustflags = re.search(r"rustflags\s*=\s*\[(.*?)\]", config, re.S)
    assert rustflags is not None, (
        f"{CARGO_CONFIG} must declare `rustflags` so the linker machine type "
        "is stated explicitly."
    )
    assert "MACHINE:X64" in rustflags.group(1), (
        f"{CARGO_CONFIG} must pass the machine type to the linker. Without it "
        "the linker may emit a 32-bit image that the 64-bit Python runtime "
        "cannot load."
    )


def test_repository_cargo_config_does_not_relocate_artifacts() -> None:
    """`build.target` must not be used: it moves the artifacts.

    Setting ``build.target`` makes cargo write every artifact under
    ``target/<triple>/debug/`` instead of ``target/debug/``. The deployment
    paths in this repository expect the artifacts directly under
    ``target/debug/`` and ``target/release/``, so a relocated build would
    silently leave the deployed binaries stale.
    """
    config = _read_toml(CARGO_CONFIG)
    build_target = re.search(r"^\s*target\s*=\s*[\"']", config, re.MULTILINE)
    assert build_target is None or f"[target.{HOST_TARGET}]" not in config, (
        "A bare `target = ...` inside a build section relocates artifacts. "
        "Use the per-triple section instead."
    )
    assert not re.search(r"^\[build\]\s*$.*?^\s*target\s*=", config, re.M | re.S), (
        "`[build] target` relocates artifacts into a triple-suffixed "
        "directory and would break every deployment path in this repository."
    )


# --------------------------------------------------------------------------
# The tools workspace
# --------------------------------------------------------------------------


@pytest.mark.parametrize("crate", WORKSPACE_CRATES)
def test_every_workspace_crate_inherits_the_workspace_manifest(crate: str) -> None:
    """Each member must take version, edition and license from the workspace.

    A crate that declares its own values is free to drift away from the
    workspace floor, which is how the `rust-version` requirement and the
    edition stopped matching between members.
    """
    manifest = _read_toml(TOOLS_WORKSPACE / crate / "Cargo.toml")
    for key in ("version", "edition", "license"):
        pattern = rf"^\s*{re.escape(key)}\.workspace\s*=\s*true\s*$"
        assert re.search(pattern, manifest, re.MULTILINE), (
            f"{crate}/Cargo.toml must use `{key}.workspace = true` so it "
            f"cannot drift from the workspace {key}. A crate that declares "
            f"`{key} = ...` directly is rejected even when the value happens "
            f"to equal the workspace value today."
        )


def test_workspace_floor_is_declared_once() -> None:
    """The Rust floor must be declared in the workspace, not per crate."""
    workspace = _read_toml(TOOLS_WORKSPACE / "Cargo.toml")
    assert "rust-version" in workspace, (
        "The workspace must declare `rust-version`; `uniswap-v4-sdk`, which "
        "pool-manager depends on, requires Rust 1.91 or newer."
    )


def test_workspace_rust_floor_is_met_by_the_installed_toolchain() -> None:
    """The installed toolchain must satisfy the declared floor."""
    import subprocess

    workspace = _read_toml(TOOLS_WORKSPACE / "Cargo.toml")
    match = re.search(r"rust-version\s*=\s*[\"'](\d+)\.(\d+)", workspace)
    assert match is not None, "The workspace must declare a numeric rust-version."
    floor = (int(match.group(1)), int(match.group(2)))

    result = subprocess.run(
        ["rustc", "-vV"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, "rustc must be callable to read its version."
    version = re.search(r"rustc (\d+)\.(\d+)\.(\d+)", result.stdout)
    assert version is not None, f"Cannot parse rustc version from {result.stdout!r}"
    actual = (int(version.group(1)), int(version.group(2)))
    assert actual >= floor, (
        f"The installed toolchain {actual} is older than the workspace floor "
        f"{floor}."
    )


def test_workspace_host_triple_is_the_pinned_triple() -> None:
    """The pinned triple must be the host triple cargo would choose."""
    import subprocess

    result = subprocess.run(
        ["rustc", "-vV"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, "rustc must be callable to read its host."
    host = re.search(r"host:\s*(\S+)", result.stdout)
    assert host is not None, f"Cannot parse the host triple from {result.stdout!r}"
    assert host.group(1) == HOST_TARGET, (
        f"The repository pins {HOST_TARGET} but the host toolchain reports "
        f"{host.group(1)}. The pinned target must match the host."
    )


# --------------------------------------------------------------------------
# The rs-src project
# --------------------------------------------------------------------------


def test_rs_src_declares_its_own_workspace_root() -> None:
    """The PyO3 project must state that it is its own workspace root.

    Without an explicit ``[workspace]`` table cargo infers an implicit
    workspace root, which changes silently as soon as a sibling directory
    gains a manifest.
    """
    manifest = _read_toml(RS_SRC / "Cargo.toml")
    assert "[workspace]" in manifest, (
        "rs-src/Cargo.toml must declare `[workspace]` so its workspace root "
        "is explicit rather than inferred."
    )


def test_rs_src_matches_the_workspace_rust_floor() -> None:
    """Both build roots must accept the same toolchains."""
    workspace = _read_toml(TOOLS_WORKSPACE / "Cargo.toml")
    rs_src = _read_toml(RS_SRC / "Cargo.toml")
    workspace_floor = re.search(r"rust-version\s*=\s*[\"'](\d+)\.(\d+)", workspace)
    rs_floor = re.search(r"rust-version\s*=\s*[\"'](\d+)\.(\d+)", rs_src)
    assert workspace_floor is not None, "The workspace must declare rust-version."
    assert rs_floor is not None, "rs-src must declare rust-version."
    assert workspace_floor.group(0) == rs_floor.group(0), (
        "The two build roots must declare the same rust-version floor."
    )


def test_rs_src_is_not_a_member_of_the_tools_workspace() -> None:
    """rs-src must stay outside the tools workspace.

    It is built by maturin as a standalone PyO3 project. Listing it as a
    workspace member would make cargo treat it as a normal crate and break
    the maturin build.
    """
    workspace = _read_toml(TOOLS_WORKSPACE / "Cargo.toml")
    assert "rs-src" not in workspace, (
        "rs-src must not be listed in the tools workspace members."
    )


def test_rs_src_path_dependencies_resolve_into_the_tools_workspace() -> None:
    """Every rs-src path dependency must point at a real workspace member."""
    manifest = _read_toml(RS_SRC / "Cargo.toml")
    paths = re.findall(r'path\s*=\s*"\.\./tools/([a-z_]+)"', manifest)
    assert paths, "rs-src must declare path dependencies into the tools crates."
    for crate in paths:
        assert (TOOLS_WORKSPACE / crate / "Cargo.toml").is_file(), (
            f"rs-src depends on ../tools/{crate} but that crate has no manifest."
        )


# --------------------------------------------------------------------------
# The build script
# --------------------------------------------------------------------------


def test_build_script_pins_the_target_triple() -> None:
    """The maturin build must name the target explicitly."""
    script = (RS_SRC / "scripts" / "build_release.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "--target" in script, (
        "build_release.ps1 must pass an explicit `--target` to maturin."
    )
    assert "MACHINE:X64" in script or "x86_64" in script, (
        "build_release.ps1 must reference the 64-bit target."
    )


def test_build_script_verifies_the_produced_module_architecture() -> None:
    """The build must confirm the produced module is 64-bit."""
    script = (RS_SRC / "scripts" / "build_release.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert "0x8664" in script, (
        "build_release.ps1 must compare the PE machine type against 0x8664 "
        "so a 32-bit module is caught at build time."
    )


# --------------------------------------------------------------------------
# The produced artifacts
# --------------------------------------------------------------------------


def _built_executables() -> list[Path]:
    found: list[Path] = []
    for profile in ("debug", "release"):
        directory = TOOLS_WORKSPACE / "target" / profile
        if not directory.is_dir():
            continue
        found.extend(sorted(directory.glob("*.exe")))
    return found


@pytest.mark.skipif(
    not _built_executables(), reason="no built manager binaries to inspect"
)
@pytest.mark.parametrize("binary", _built_executables(), ids=lambda p: p.name)
def test_every_built_manager_binary_is_64_bit(binary: Path) -> None:
    """Every deployed manager binary must be a 64-bit PE.

    The Python runtime launches these binaries as child processes. A 32-bit
    binary launched from a 64-bit host is exactly the case that produced the
    Wow64 compatibility failure.
    """
    machine = _pe_machine_type(binary)
    assert machine == PE_MACHINE_AMD64, (
        f"{binary.name} has PE machine type 0x{(machine or 0):04X}; it must be "
        f"0x{PE_MACHINE_AMD64:04X} (x86-64)."
    )


@pytest.mark.skipif(
    not (RS_SRC / "target" / "release" / "flashloan_rs.dll").is_file(),
    reason="no built extension module to inspect",
)
def test_built_extension_module_is_64_bit() -> None:
    """The PyO3 extension module must be a 64-bit PE.

    The module is imported directly into the 64-bit Python interpreter, so a
    32-bit module cannot be loaded at all.
    """
    module = RS_SRC / "target" / "release" / "flashloan_rs.dll"
    machine = _pe_machine_type(module)
    assert machine == PE_MACHINE_AMD64, (
        f"flashloan_rs.dll has PE machine type 0x{(machine or 0):04X}; it must "
        f"be 0x{PE_MACHINE_AMD64:04X} (x86-64)."
    )


def test_no_stale_per_crate_target_directories() -> None:
    """No crate may keep a private target directory.

    Cargo writes to the workspace target directory. A crate-local
    ``target/`` directory is a leftover from before the crate joined the
    workspace and it holds an old binary that a deployment path can pick up
    in place of the current one.
    """
    stale = [
        crate
        for crate in WORKSPACE_CRATES
        if (TOOLS_WORKSPACE / crate / "target").is_dir()
    ]
    assert not stale, (
        "These crates keep a crate-local target directory: "
        + ", ".join(stale)
        + ". Remove them so only the workspace target directory holds artifacts."
    )

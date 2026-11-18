"""Fail-fast verification of explicitly pinned external runtime Git sources."""

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess


_SHA = re.compile(r"[0-9a-f]{40}")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
        timeout=30,
    )
    if result.returncode:
        raise ValueError(f"Runtime Git command failed in {root}: {result.stderr.strip()}")
    return result.stdout.strip()


def _revision(value: str) -> str:
    if not _SHA.fullmatch(value):
        raise ValueError("Runtime source pins must be full lowercase 40-character Git commit SHAs")
    return value


def parse_required_submodules(values: list[str]) -> dict[str, str]:
    pins = {}
    for value in values:
        path, separator, revision = value.partition("=")
        if not separator or path in pins:
            raise ValueError("Required submodule pins must be unique PATH=FULL_COMMIT entries")
        relative = PurePosixPath(path)
        if not relative.parts or relative.is_absolute() or str(relative) != path or any(
            part in (".", "..") for part in relative.parts
        ):
            raise ValueError(f"Invalid required submodule path: {path!r}")
        pins[path] = _revision(revision)
    return pins


def _checkout(root: Path, revision: str) -> dict:
    if not root.is_dir():
        raise ValueError(f"Missing runtime source directory: {root}")
    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise ValueError(f"Runtime source is not an initialized Git checkout: {root}")
    actual = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if actual != revision:
        raise ValueError(f"Runtime source commit mismatch in {root}: expected {revision}, got {actual}")
    # Required submodules are checked separately; installed runtimes may leave
    # other gitlinks uninitialized intentionally.
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=all")
    if status:
        raise ValueError(f"Dirty runtime source checkout: {root}\n{status}")
    return {"path": str(root), "commit": actual, "clean": True}


def verify_runtime_source(root: Path, revision: str, required_submodules: dict[str, str]) -> dict:
    """Validate root and required gitlinks without executing runtime code."""
    root = root.resolve()
    revision = _revision(revision)
    pins = parse_required_submodules([f"{path}={sha}" for path, sha in required_submodules.items()])
    report = {"schema_version": 1, "audience": "developer_only", "status": "verified", **_checkout(root, revision)}
    verified = {}
    for path, sha in sorted(pins.items()):
        child = root.joinpath(path)
        if child.resolve() != child or not child.is_relative_to(root):
            raise ValueError(f"Required submodule path traverses a symlink: {path}")
        entry = _git(root, "ls-tree", "HEAD", "--", path)
        expected_entry = f"160000 commit {sha}\t{path}"
        if entry != expected_entry:
            raise ValueError(f"Required submodule gitlink mismatch: {path}; expected {sha}, got {entry!r}")
        verified[path] = _checkout(child, sha)
    report["required_submodules"] = verified
    report["scope"] = "Root tracked and untracked files, plus explicitly required submodule checkouts; installed packages and other submodules are not verified."
    return report


def write_runtime_provenance(output: Path, report: dict) -> None:
    """Publish a complete developer-only record before simulator initialization."""
    destination = output / "runtime-source-provenance.json"
    temporary = output / ".runtime-source-provenance.json.tmp"
    with temporary.open("x") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(destination)
    directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

#!/usr/bin/env python3
"""Project committed canonical Markdown into an isolated GBrain repository.

The policy contains only explicit source/target pairs.  Sources are read from
the source repository's current Git HEAD, never from a dirty worktree.  The
operation is prevalidated as a complete batch and then applied atomically per
file; it never deletes files and never creates a remote, timer, or daemon.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "1.0"
MAX_FILE_BYTES = 2_000_000
MAX_BATCH_BYTES = 8_000_000
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+)?PRIVATE KEY-----"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{35}"),
    re.compile(rb"xox[baprs]-[0-9A-Za-z-]{20,}"),
    re.compile(rb"github_pat_[0-9A-Za-z_]{20,}"),
    re.compile(rb"gh[opsu]_[0-9A-Za-z]{20,}"),
    re.compile(rb"sk-[0-9A-Za-z_-]{20,}"),
)


class ProjectionError(RuntimeError):
    """Policy or source validation failed before projection."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_git(repo: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectionError(f"git command failed for {repo}: {exc}") from exc
    return result.stdout


def _load_policy(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProjectionError(f"cannot stat policy {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise ProjectionError("policy must be a regular non-symlink file")
    if info.st_mode & 0o022:
        raise ProjectionError("policy must not be writable by group or others")
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProjectionError(f"cannot parse policy {path}: {exc}") from exc
    if not isinstance(policy, dict):
        raise ProjectionError("policy root must be an object")
    if policy.get("schema_version") != SCHEMA_VERSION:
        raise ProjectionError(f"policy schema_version must be {SCHEMA_VERSION}")
    return policy


def _root(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ProjectionError(f"{label} must be an absolute path")
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise ProjectionError(f"{label} must be a directory")
    return path


def _relative_markdown(value: Any, label: str, *, canonical: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ProjectionError(f"{label} must be a non-empty string")
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts or "." in posix.parts:
        raise ProjectionError(f"{label} must be a normalized relative path")
    if posix.suffix.casefold() != ".md":
        raise ProjectionError(f"{label} must end in .md")
    if canonical and (not posix.parts or posix.parts[0] != "canonical"):
        raise ProjectionError(f"{label} must stay below canonical/")
    return posix.as_posix()


def _assert_safe_target(brain_repo: Path, relative: str) -> Path:
    target = brain_repo.joinpath(*PurePosixPath(relative).parts)
    current = brain_repo
    for component in PurePosixPath(relative).parts[:-1]:
        current = current / component
        if current.exists() and current.is_symlink():
            raise ProjectionError(f"target parent is a symlink: {relative}")
    if target.is_symlink():
        raise ProjectionError(f"target is a symlink: {relative}")
    resolved_parent = target.parent.resolve(strict=False)
    try:
        resolved_parent.relative_to(brain_repo)
    except ValueError as exc:
        raise ProjectionError(f"target escapes brain_repo: {relative}") from exc
    return target


def _validate_content(source: str, data: bytes) -> None:
    if len(data) > MAX_FILE_BYTES:
        raise ProjectionError(f"source exceeds {MAX_FILE_BYTES} bytes: {source}")
    if b"\x00" in data:
        raise ProjectionError(f"source is not text: {source}")
    try:
        data.decode("utf-8")
    except UnicodeError as exc:
        raise ProjectionError(f"source is not valid UTF-8: {source}") from exc
    for pattern in SECRET_PATTERNS:
        if pattern.search(data):
            raise ProjectionError(f"possible secret detected in source: {source}")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o640)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def project(policy_path: Path, *, check: bool = False) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    source_repo = _root(policy.get("source_repo"), "source_repo")
    brain_repo = _root(policy.get("brain_repo"), "brain_repo")
    if source_repo == brain_repo:
        raise ProjectionError("source_repo and brain_repo must be different")

    observed_source_root = Path(
        _run_git(source_repo, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve(strict=True)
    if observed_source_root != source_repo:
        raise ProjectionError("source_repo is not the Git toplevel")
    observed_brain_root = Path(
        _run_git(brain_repo, "rev-parse", "--show-toplevel").decode().strip()
    ).resolve(strict=True)
    if observed_brain_root != brain_repo:
        raise ProjectionError("brain_repo is not the Git toplevel")

    head = _run_git(source_repo, "rev-parse", "HEAD").decode().strip()
    raw_entries = policy.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ProjectionError("entries must be a non-empty list")

    prepared: list[tuple[str, str, Path, bytes]] = []
    seen_targets: set[str] = set()
    total_bytes = 0
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise ProjectionError(f"entries[{index}] must be an object")
        source = _relative_markdown(entry.get("source"), f"entries[{index}].source")
        target_rel = _relative_markdown(
            entry.get("target"), f"entries[{index}].target", canonical=True
        )
        if target_rel in seen_targets:
            raise ProjectionError(f"duplicate target: {target_rel}")
        seen_targets.add(target_rel)
        target = _assert_safe_target(brain_repo, target_rel)
        data = _run_git(source_repo, "cat-file", "blob", f"HEAD:{source}")
        _validate_content(source, data)
        total_bytes += len(data)
        if total_bytes > MAX_BATCH_BYTES:
            raise ProjectionError(f"batch exceeds {MAX_BATCH_BYTES} bytes")
        prepared.append((source, target_rel, target, data))

    results: list[dict[str, Any]] = []
    for source, target_rel, target, data in prepared:
        existing = target.read_bytes() if target.exists() else None
        changed = existing != data
        if changed and not check:
            _atomic_write(target, data)
        action = "would_update" if check and changed else "updated" if changed else "unchanged"
        results.append(
            {
                "source": source,
                "target": target_rel,
                "bytes": len(data),
                "sha256": _sha256(data),
                "action": action,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "mode": "check" if check else "apply",
        "source_head": head,
        "source_repo": str(source_repo),
        "brain_repo": str(brain_repo),
        "entries": results,
        "changed": sum(item["action"] in {"updated", "would_update"} for item in results),
        "total_bytes": total_bytes,
    }


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    data = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _atomic_write(path, data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        receipt = project(args.policy, check=args.check)
        if args.receipt:
            _write_receipt(args.receipt, receipt)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except ProjectionError as exc:
        print(json.dumps({"status": "HOLD", "error": str(exc)}, ensure_ascii=False))
        return 20


if __name__ == "__main__":
    raise SystemExit(main())

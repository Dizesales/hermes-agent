#!/usr/bin/env python3
"""Project committed canonical Markdown into an isolated GBrain repository.

The policy contains only explicit source/target pairs.  Sources are read from
one resolved source-repository commit, never from a dirty worktree.  The
operation is prevalidated as a complete batch and then applied atomically per
file.  It may delete only stale section shards carrying its own source marker;
it never deletes source or unmanaged files and never creates a remote, timer,
or daemon.
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
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "1.0"
MAX_FILE_BYTES = 2_000_000
MAX_BATCH_BYTES = 8_000_000
SECTION_SCHEMA = "section-v1"
SECTION_INDEX_SCHEMA = "section-index-v1"
SECTION_HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*(?:\r?\n)?$")
FENCE_LINE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
DOCUMENT_TITLE = re.compile(r"(?m)^#[ \t]+(.+?)[ \t]*$")
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
    if policy.get("schema_version") not in {SCHEMA_VERSION, "1.1"}:
        raise ProjectionError("policy schema_version must be 1.0 or 1.1")
    if ("managed_roots" in policy) != (policy.get("schema_version") == "1.1"):
        raise ProjectionError("managed_roots requires policy schema_version 1.1")
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


def _relative_directory(value: Any, label: str, *, canonical: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ProjectionError(f"{label} must be a non-empty string")
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts or "." in posix.parts:
        raise ProjectionError(f"{label} must be a normalized relative path")
    if posix.suffix:
        raise ProjectionError(f"{label} must be a directory without a suffix")
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


def _assert_safe_directory(brain_repo: Path, relative: str) -> Path:
    directory = brain_repo.joinpath(*PurePosixPath(relative).parts)
    current = brain_repo
    for component in PurePosixPath(relative).parts:
        current = current / component
        if current.exists() and current.is_symlink():
            raise ProjectionError(f"target directory is a symlink: {relative}")
    resolved = directory.resolve(strict=False)
    try:
        resolved.relative_to(brain_repo)
    except ValueError as exc:
        raise ProjectionError(f"target directory escapes brain_repo: {relative}") from exc
    return directory


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


def _heading_slug(heading: str, occurrence: int) -> str:
    ascii_heading = (
        unicodedata.normalize("NFKD", heading).encode("ascii", "ignore").decode()
    )
    stem = re.sub(r"[^a-z0-9]+", "-", ascii_heading.casefold()).strip("-")
    stem = (stem or "section")[:72].rstrip("-")
    digest = _sha256(heading.encode("utf-8"))[:10]
    duplicate = f"-{occurrence}" if occurrence > 1 else ""
    return f"{stem}-{digest}{duplicate}"


def _frontmatter(
    schema: str, source: str, parent_slug: str, projection_hash: str
) -> str:
    return (
        "---\n"
        f"gbrain_library_projection: {schema}\n"
        f"gbrain_source_path: {json.dumps(source, ensure_ascii=False)}\n"
        f"gbrain_parent_slug: {json.dumps(parent_slug, ensure_ascii=False)}\n"
        f"gbrain_projection_sha256: {json.dumps(projection_hash)}\n"
        "---\n"
    )


def _section_boundaries(text: str) -> list[tuple[int, str]]:
    boundaries: list[tuple[int, str]] = []
    fence_character = ""
    fence_length = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        fence = FENCE_LINE.match(line)
        if fence:
            token = fence.group(1)
            if not fence_character:
                fence_character = token[0]
                fence_length = len(token)
            elif token[0] == fence_character and len(token) >= fence_length:
                fence_character = ""
                fence_length = 0
            offset += len(line)
            continue
        if not fence_character:
            heading = SECTION_HEADING.match(line)
            if heading:
                boundaries.append((offset, heading.group(1).strip()))
        offset += len(line)
    return boundaries


def _render_sectioned(
    source: str,
    target_rel: str,
    section_dir: str,
    data: bytes,
) -> list[tuple[str, bytes]]:
    text = data.decode("utf-8")
    boundaries = _section_boundaries(text)
    if not boundaries:
        raise ProjectionError(f"sectioned source has no level-2 headings: {source}")

    parent_slug = target_rel[:-3]
    source_hash = _sha256(data)
    preamble = text[: boundaries[0][0]].rstrip()
    title_match = DOCUMENT_TITLE.search(preamble)
    document_title = title_match.group(1).strip() if title_match else Path(source).stem
    occurrence_by_heading: dict[str, int] = {}
    sections: list[tuple[str, str, bytes]] = []

    for index, (start, heading) in enumerate(boundaries):
        key = heading.casefold()
        occurrence_by_heading[key] = occurrence_by_heading.get(key, 0) + 1
        slug = _heading_slug(heading, occurrence_by_heading[key])
        relative = f"{section_dir}/{slug}.md"
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        original_section = text[start:end].strip()
        rendered = (
            _frontmatter(
                SECTION_SCHEMA,
                source,
                parent_slug,
                _sha256(original_section.encode("utf-8")),
            )
            + f"# {document_title} — {heading}\n\n"
            + f"Fonte canônica: [[{parent_slug}]]\n\n"
            + original_section
            + "\n"
        ).encode("utf-8")
        _validate_content(source, rendered)
        sections.append((relative, heading, rendered))

    index_lines = [
        _frontmatter(SECTION_INDEX_SCHEMA, source, parent_slug, source_hash).rstrip(),
        preamble,
        "## Seções projetadas",
        "",
    ]
    for relative, heading, _ in sections:
        index_lines.append(f"- [[{relative[:-3]}|{heading}]]")
    index_data = ("\n".join(index_lines).rstrip() + "\n").encode("utf-8")
    _validate_content(source, index_data)
    return [(target_rel, index_data)] + [
        (relative, rendered) for relative, _, rendered in sections
    ]


def _managed_stale_sections(
    brain_repo: Path,
    section_dir: str,
    source: str,
    expected: set[str],
) -> list[tuple[str, Path, bytes]]:
    directory = _assert_safe_directory(brain_repo, section_dir)
    if not directory.exists():
        return []
    marker = f"gbrain_library_projection: {SECTION_SCHEMA}".encode()
    source_line = (
        f"gbrain_source_path: {json.dumps(source, ensure_ascii=False)}".encode("utf-8")
    )
    stale: list[tuple[str, Path, bytes]] = []
    for path in sorted(directory.glob("*.md")):
        relative = path.relative_to(brain_repo).as_posix()
        if path.is_symlink() or not path.is_file():
            raise ProjectionError(
                f"managed section target is not a regular file: {relative}"
            )
        with path.open("rb") as handle:
            prefix = handle.read(4096)
        if marker in prefix and source_line in prefix:
            if relative not in expected:
                existing = path.read_bytes()
                stale.append((relative, path, existing))
    return stale


def _check_managed_roots(raw: Any, brain_repo: Path, expected: set[str]) -> None:
    """An explicitly exclusive namespace cannot hide abandoned projections.

    Undeclared files require reviewed owner retirement, never automatic pruning.
    Neighbouring namespaces remain outside this optional policy boundary.
    """
    if not isinstance(raw, list) or not raw:
        raise ProjectionError("managed_roots must be a non-empty list")
    roots = [_relative_directory(value, "managed root", canonical=True) for value in raw]
    for index, root in enumerate(roots):
        if any(root == other or root.startswith(other + "/") or other.startswith(root + "/")
               for other in roots[:index]):
            raise ProjectionError("managed roots overlap")
    if any(not any(target.startswith(root + "/") for root in roots) for target in expected):
        raise ProjectionError("projection target outside managed roots")
    def walk_error(error: OSError) -> None:
        raise ProjectionError("cannot enumerate managed root") from error

    for root in roots:
        directory = _assert_safe_directory(brain_repo, root)
        if directory.exists() and not directory.is_dir():
            raise ProjectionError("managed root is not a directory")
        if not directory.exists():
            continue
        for parent, dirs, files in os.walk(directory, followlinks=False, onerror=walk_error):
            for name in dirs + files:
                path = Path(parent) / name
                relative = path.relative_to(brain_repo).as_posix()
                if path.is_symlink():
                    raise ProjectionError(f"managed target is a symlink: {relative}")
                if name.endswith(".md") and name in files and relative not in expected:
                    raise ProjectionError(f"undeclared managed target: {relative}")


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


def _retired_alias_content(target: str) -> bytes:
    slug = target[:-3].casefold()
    return (
        "---\ntype: note\ntitle: Superseded identity projection\nstatus: superseded\n---\n\n"
        "# Projecao antiga aposentada\n\n"
        "Esta copia deixou de conter instrucoes operacionais. "
        f"Consultar a projecao gerenciada [[{slug}]] e reabrir a fonte owner atual.\n"
        "Historico preservado no Git local; este ponteiro nao concede autoridade.\n"
    ).encode("utf-8")


def _check_retired_aliases(raw: Any, brain_repo: Path, targets: set[str]) -> list[dict[str, str]]:
    """Read-only guard: retirement itself requires a separate owner migration."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 32:
        raise ProjectionError("retired_aliases must be a list of at most 32 entries")
    results = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"path", "target"}:
            raise ProjectionError("retired alias requires only path and target")
        relative = _relative_markdown(item["path"], "retired alias path")
        parts = PurePosixPath(relative).parts
        if parts[0] != "identity" or any(part.startswith(".") for part in parts):
            raise ProjectionError("retired alias must stay below identity/")
        if relative in seen:
            raise ProjectionError("duplicate retired alias")
        seen.add(relative)
        target = _relative_markdown(item["target"], "retired alias target", canonical=True)
        if target not in targets:
            raise ProjectionError("retired alias target is not an active projection")
        path = _assert_safe_target(brain_repo, relative)
        expected = _retired_alias_content(target)
        if not path.is_file() or path.stat().st_size != len(expected) or path.read_bytes() != expected:
            raise ProjectionError(f"retired alias changed or missing: {relative}")
        results.append({"path": relative, "target": target, "sha256": _sha256(expected)})
    return results


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
    stale: list[tuple[str, str, Path, bytes]] = []
    section_specs: list[tuple[str, str, set[str]]] = []
    seen_targets: set[str] = set()
    seen_section_dirs: set[str] = set()
    total_bytes = 0
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            raise ProjectionError(f"entries[{index}] must be an object")
        source = _relative_markdown(entry.get("source"), f"entries[{index}].source")
        target_rel = _relative_markdown(
            entry.get("target"), f"entries[{index}].target", canonical=True
        )
        data = _run_git(source_repo, "cat-file", "blob", f"{head}:{source}")
        _validate_content(source, data)
        section_config = entry.get("sections")
        if section_config is None:
            outputs = [(target_rel, data)]
        else:
            if not isinstance(section_config, dict):
                raise ProjectionError(f"entries[{index}].sections must be an object")
            section_dir = _relative_directory(
                section_config.get("target_dir"),
                f"entries[{index}].sections.target_dir",
                canonical=True,
            )
            if section_dir in seen_section_dirs:
                raise ProjectionError(f"duplicate section target_dir: {section_dir}")
            seen_section_dirs.add(section_dir)
            outputs = _render_sectioned(source, target_rel, section_dir, data)

        output_targets = {relative for relative, _ in outputs}
        for relative, output_data in outputs:
            if relative in seen_targets:
                raise ProjectionError(f"duplicate target: {relative}")
            seen_targets.add(relative)
            target = _assert_safe_target(brain_repo, relative)
            total_bytes += len(output_data)
            if total_bytes > MAX_BATCH_BYTES:
                raise ProjectionError(f"batch exceeds {MAX_BATCH_BYTES} bytes")
            prepared.append((source, relative, target, output_data))

        if section_config is not None:
            section_specs.append((section_dir, source, output_targets))

    for section_dir, source, output_targets in section_specs:
        for relative, path, existing in _managed_stale_sections(
            brain_repo, section_dir, source, output_targets
        ):
            if relative in seen_targets:
                raise ProjectionError(
                    f"stale managed target conflicts with active target: {relative}"
                )
            stale.append((source, relative, path, existing))

    if "managed_roots" in policy:
        _check_managed_roots(
            policy["managed_roots"], brain_repo, seen_targets | {item[1] for item in stale}
        )

    retired_aliases = _check_retired_aliases(policy.get("retired_aliases"), brain_repo, seen_targets)

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

    for source, target_rel, target, existing in stale:
        if not check:
            if target.is_symlink() or not target.is_file():
                raise ProjectionError(f"stale managed target changed type: {target_rel}")
            target.unlink()
        results.append(
            {
                "source": source,
                "target": target_rel,
                "bytes": len(existing),
                "sha256": _sha256(existing),
                "action": "would_delete" if check else "deleted",
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
        "retired_aliases": retired_aliases,
        "changed": sum(
            item["action"] in {"updated", "would_update", "deleted", "would_delete"}
            for item in results
        ),
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

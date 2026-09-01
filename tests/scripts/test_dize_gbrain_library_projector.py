from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "dize_gbrain_library_projector.py"


def _load_projector():
    spec = importlib.util.spec_from_file_location("dize_gbrain_library_projector_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repos(tmp_path: Path):
    source = tmp_path / "source"
    brain = tmp_path / "brain"
    source.mkdir()
    brain.mkdir()
    for repo in (source, brain):
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Test")
        _git(repo, "config", "user.email", "test@example.invalid")
    (source / "context").mkdir()
    (source / "context" / "decisions.md").write_text("# Committed\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "source")
    (brain / ".gitignore").write_text("\n", encoding="utf-8")
    _git(brain, "add", ".")
    _git(brain, "commit", "-qm", "brain")
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source_repo": str(source),
                "brain_repo": str(brain),
                "entries": [
                    {
                        "source": "context/decisions.md",
                        "target": "canonical/context/decisions.md",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    os.chmod(policy, 0o600)
    return source, brain, policy


def test_projects_git_head_not_dirty_worktree_and_is_idempotent(tmp_path):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    (source / "context" / "decisions.md").write_text("# Dirty\n", encoding="utf-8")

    first = module.project(policy)
    second = module.project(policy)

    target = brain / "canonical" / "context" / "decisions.md"
    assert target.read_text(encoding="utf-8") == "# Committed\n"
    assert first["changed"] == 1
    assert first["entries"][0]["action"] == "updated"
    assert second["changed"] == 0
    assert second["entries"][0]["action"] == "unchanged"


@pytest.mark.parametrize(
    "target",
    ["../escape.md", "outside/library.md", "/absolute.md", "canonical/not-markdown.txt"],
)
def test_rejects_targets_outside_canonical_markdown(tmp_path, target):
    module = _load_projector()
    _, _, policy = _repos(tmp_path)
    data = json.loads(policy.read_text(encoding="utf-8"))
    data["entries"][0]["target"] = target
    policy.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(module.ProjectionError):
        module.project(policy)


def test_secret_detection_is_batch_atomic(tmp_path):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    secret_path = source / "context" / "lessons.md"
    secret_path.write_text("token: github_pat_abcdefghijklmnopqrstuvwxyz123456\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "secret fixture")
    data = json.loads(policy.read_text(encoding="utf-8"))
    data["entries"].append(
        {"source": "context/lessons.md", "target": "canonical/context/lessons.md"}
    )
    policy.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(module.ProjectionError, match="possible secret"):
        module.project(policy)
    assert not (brain / "canonical").exists()


def test_rejects_symlinked_target_parent(tmp_path):
    module = _load_projector()
    _, brain, policy = _repos(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (brain / "canonical").symlink_to(outside, target_is_directory=True)

    with pytest.raises(module.ProjectionError, match="symlink"):
        module.project(policy)

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


def _commit_source(source: Path, text: str, message: str = "sectioned source") -> None:
    (source / "context" / "decisions.md").write_text(text, encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", message)


def _enable_sections(policy: Path) -> None:
    data = json.loads(policy.read_text(encoding="utf-8"))
    data["entries"][0]["sections"] = {
        "target_dir": "canonical/context/decisions-sections"
    }
    policy.write_text(json.dumps(data), encoding="utf-8")


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


def test_projects_one_resolved_commit_when_head_advances_mid_batch(tmp_path, monkeypatch):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    lessons = source / "context" / "lessons.md"
    lessons.write_text("# Lessons committed\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "add lessons")
    expected_head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    data = json.loads(policy.read_text(encoding="utf-8"))
    data["entries"].append(
        {"source": "context/lessons.md", "target": "canonical/context/lessons.md"}
    )
    policy.write_text(json.dumps(data), encoding="utf-8")

    original_run_git = module._run_git
    advanced = False

    def advancing_run_git(repo, *args):
        nonlocal advanced
        result = original_run_git(repo, *args)
        if args[:2] == ("cat-file", "blob") and not advanced:
            advanced = True
            (source / "context" / "decisions.md").write_text(
                "# Decisions advanced\n", encoding="utf-8"
            )
            lessons.write_text("# Lessons advanced\n", encoding="utf-8")
            _git(source, "add", ".")
            _git(source, "commit", "-qm", "advance during projection")
        return result

    monkeypatch.setattr(module, "_run_git", advancing_run_git)
    receipt = module.project(policy)

    assert receipt["source_head"] == expected_head
    assert (brain / "canonical/context/decisions.md").read_text() == "# Committed\n"
    assert (brain / "canonical/context/lessons.md").read_text() == "# Lessons committed\n"


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


def test_sectioned_projection_creates_index_and_stable_managed_pages(tmp_path):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    _commit_source(
        source,
        "# Decisions\n\nCanonical intro.\n\n"
        "```markdown\n## Not a projected section\n```\n\n"
        "## 2026-01-01 — First\n\nAlpha body.\n\n"
        "## 2026-01-02 — Second\n\nBeta body.\n",
    )
    _enable_sections(policy)

    first = module.project(policy)
    index = brain / "canonical/context/decisions.md"
    section_dir = brain / "canonical/context/decisions-sections"
    section_paths = sorted(section_dir.glob("*.md"))

    assert first["changed"] == 3
    assert len(section_paths) == 2
    assert "section-index-v1" in index.read_text(encoding="utf-8")
    assert "Alpha body" not in index.read_text(encoding="utf-8")
    assert all(
        "gbrain_library_projection: section-v1"
        in path.read_text(encoding="utf-8")
        for path in section_paths
    )
    assert any("Alpha body" in path.read_text(encoding="utf-8") for path in section_paths)

    original_names = [path.name for path in section_paths]
    _commit_source(
        source,
        "# Decisions\n\nCanonical intro.\n\n"
        "```markdown\n## Not a projected section\n```\n\n"
        "## 2026-01-01 — First\n\nAlpha body updated.\n\n"
        "## 2026-01-02 — Second\n\nBeta body.\n",
        "revise one section",
    )
    second = module.project(policy)

    assert [path.name for path in sorted(section_dir.glob("*.md"))] == original_names
    assert second["changed"] == 2
    assert module.project(policy)["changed"] == 0


def test_sectioned_projection_deletes_only_its_own_stale_pages(tmp_path):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    _commit_source(
        source,
        "# Decisions\n\n## First\n\nAlpha.\n\n## Second\n\nBeta.\n",
    )
    _enable_sections(policy)
    module.project(policy)
    section_dir = brain / "canonical/context/decisions-sections"
    previous = set(section_dir.glob("*.md"))
    unmanaged = section_dir / "operator-note.md"
    unmanaged.write_text("# Preserve me\n", encoding="utf-8")

    _commit_source(source, "# Decisions\n\n## First\n\nAlpha.\n", "remove section")
    receipt = module.project(policy)

    remaining_managed = {
        path
        for path in section_dir.glob("*.md")
        if "section-v1" in path.read_text(encoding="utf-8")
    }
    assert len(previous - remaining_managed) == 1
    assert unmanaged.exists()
    assert [item["action"] for item in receipt["entries"]].count("deleted") == 1


def test_sectioned_check_reports_stale_without_mutating(tmp_path):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    _commit_source(
        source,
        "# Decisions\n\n## First\n\nAlpha.\n\n## Second\n\nBeta.\n",
    )
    _enable_sections(policy)
    module.project(policy)
    section_dir = brain / "canonical/context/decisions-sections"
    before = {path.name: path.read_bytes() for path in section_dir.glob("*.md")}
    _commit_source(source, "# Decisions\n\n## First\n\nAlpha.\n", "remove section")

    receipt = module.project(policy, check=True)

    assert any(item["action"] == "would_delete" for item in receipt["entries"])
    assert {path.name: path.read_bytes() for path in section_dir.glob("*.md")} == before


def test_stale_section_collision_with_active_target_is_batch_atomic(tmp_path):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    _commit_source(source, "# Decisions\n\n## First\n\nAlpha.\n")
    _enable_sections(policy)
    module.project(policy)
    index = brain / "canonical/context/decisions.md"
    section_dir = brain / "canonical/context/decisions-sections"
    old_index = index.read_bytes()
    stale_path = next(section_dir.glob("*.md"))
    stale_relative = stale_path.relative_to(brain).as_posix()
    old_stale = stale_path.read_bytes()

    (source / "context/decisions.md").write_text(
        "# Decisions\n\n## Second\n\nBeta.\n", encoding="utf-8"
    )
    (source / "context/lessons.md").write_text("# Lessons\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "replace section and add target")
    data = json.loads(policy.read_text(encoding="utf-8"))
    data["entries"].append(
        {"source": "context/lessons.md", "target": stale_relative}
    )
    policy.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(module.ProjectionError, match="conflicts with active target"):
        module.project(policy)

    assert index.read_bytes() == old_index
    assert stale_path.read_bytes() == old_stale
    assert len(list(section_dir.glob("*.md"))) == 1


def test_sectioned_projection_rejects_source_without_h2(tmp_path):
    module = _load_projector()
    _, _, policy = _repos(tmp_path)
    _enable_sections(policy)

    with pytest.raises(module.ProjectionError, match="no level-2 headings"):
        module.project(policy)


def _retire_alias(module, brain, policy):
    data = json.loads(policy.read_text())
    row = {"path": "identity/owner-workspace/AGENTS.md", "target": data["entries"][0]["target"]}
    data["retired_aliases"] = [row]
    policy.write_text(json.dumps(data))
    alias = brain / row["path"]
    alias.parent.mkdir(parents=True)
    alias.write_text(
        "---\ntype: note\ntitle: Superseded identity projection\nstatus: superseded\n---\n\n"
        "# Projecao antiga aposentada\n\n"
        "Esta copia deixou de conter instrucoes operacionais. "
        "Consultar a projecao gerenciada [[canonical/context/decisions]] e reabrir a fonte owner atual.\n"
        "Historico preservado no Git local; este ponteiro nao concede autoridade.\n"
    )
    return alias


@pytest.mark.parametrize("check", [True, False])
def test_retired_instruction_copy_blocks_before_any_projection_write(tmp_path, check):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    alias = _retire_alias(module, brain, policy)
    alias.write_text("# Old instructions\nAlways load private memory.\n")
    before = alias.read_bytes()
    with pytest.raises(module.ProjectionError, match="retired alias changed"):
        module.project(policy, check=check)
    assert not (brain / "canonical").exists()
    assert alias.read_bytes() == before


def test_retired_alias_is_read_only_and_follows_a_declared_current_projection(tmp_path):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    alias = _retire_alias(module, brain, policy)
    before = alias.read_bytes()
    first = module.project(policy)
    _commit_source(source, "# A new current rule\n")
    second = module.project(policy)
    assert first["retired_aliases"] == second["retired_aliases"]
    assert alias.read_bytes() == before
    assert (brain / "canonical/context/decisions.md").read_text() == "# A new current rule\n"
    assert "[[canonical/context/decisions]]" in alias.read_text()


@pytest.mark.parametrize("alteration", ["missing", "symlink", "traversal", "undeclared_target", "duplicate", "outside_identity"])
def test_retired_alias_rejects_unsafe_or_inconsistent_policy_without_writes(tmp_path, alteration):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    alias = _retire_alias(module, brain, policy)
    data = json.loads(policy.read_text())
    if alteration == "missing":
        alias.unlink()
    elif alteration == "symlink":
        alias.unlink()
        alias.symlink_to(source / "context/decisions.md")
    elif alteration == "traversal":
        data["retired_aliases"][0]["path"] = "identity/../outside.md"
    elif alteration == "undeclared_target":
        data["retired_aliases"][0]["target"] = "canonical/undeclared.md"
    elif alteration == "duplicate":
        data["retired_aliases"] *= 2
    else:
        data["retired_aliases"][0]["path"] = "canonical/old.md"
    policy.write_text(json.dumps(data))
    with pytest.raises(module.ProjectionError):
        module.project(policy)
    assert not (brain / "canonical").exists()
    assert (source / "context/decisions.md").read_text() == "# Committed\n"


@pytest.mark.parametrize("sectioned", [False, True])
@pytest.mark.parametrize("change", ["remove_entry", "rename_target", "rename_source"])
def test_managed_root_rejects_orphans_before_writes(tmp_path, sectioned, change):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    _commit_source(source, "# Decisions\n\n## First\n\nWithdraw me.\n")
    if sectioned:
        _enable_sections(policy)
    data = json.loads(policy.read_text())
    data["schema_version"] = "1.1"; data["managed_roots"] = ["canonical/context"]
    policy.write_text(json.dumps(data))
    module.project(policy)
    old = {p.relative_to(brain): p.read_bytes() for p in brain.rglob("*.md")}
    (source / "context/lessons.md").write_text("# Retained\n")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "add retained source")
    retained = {"source": "context/lessons.md", "target": "canonical/context/lessons.md"}
    if change == "remove_entry":
        data["entries"] = [retained]
    elif change == "rename_target":
        data["entries"][0]["target"] = "canonical/context/renamed.md"
    else:
        data["entries"][0] = retained
    policy.write_text(json.dumps(data))
    for check in (True, False):
        with pytest.raises(module.ProjectionError, match="undeclared managed target"):
            module.project(policy, check=check)
        assert {p.relative_to(brain): p.read_bytes() for p in brain.rglob("*.md")} == old
    # Explicit owner retirement of known old projections, then apply succeeds.
    for rel in old:
        (brain / rel).unlink()
    module.project(policy)
    assert module.project(policy, check=True)["changed"] == 0


@pytest.mark.parametrize("roots", [None, "canonical/context", [], ["../escape"], ["canonical/context", "canonical/context/sub"]])
def test_managed_roots_invalid_contract_blocks_before_write(tmp_path, roots):
    module = _load_projector()
    _, brain, policy = _repos(tmp_path)
    data = json.loads(policy.read_text()); data["schema_version"] = "1.1"; data["managed_roots"] = roots
    policy.write_text(json.dumps(data))
    with pytest.raises(module.ProjectionError):
        module.project(policy)
    assert not (brain / "canonical").exists()


def test_managed_root_preserves_neighbors_and_refuses_symlink(tmp_path):
    module = _load_projector()
    _, brain, policy = _repos(tmp_path)
    data = json.loads(policy.read_text()); data["schema_version"] = "1.1"; data["managed_roots"] = ["canonical/context"]
    policy.write_text(json.dumps(data))
    neighbor = brain / "canonical/other/note.md"
    neighbor.parent.mkdir(parents=True); neighbor.write_text("# Not managed\n")
    module.project(policy)
    assert neighbor.read_text() == "# Not managed\n"
    (brain / "canonical/context/link").symlink_to(neighbor.parent, target_is_directory=True)
    with pytest.raises(module.ProjectionError, match="symlink"):
        module.project(policy)


@pytest.mark.parametrize("version,roots", [("1.0", ["canonical/context"]), ("1.1", None)])
def test_managed_policy_cannot_silently_downgrade(tmp_path, version, roots):
    module = _load_projector()
    _, brain, policy = _repos(tmp_path)
    data = json.loads(policy.read_text()); data["schema_version"] = version
    if roots is not None:
        data["managed_roots"] = roots
    policy.write_text(json.dumps(data))
    with pytest.raises(module.ProjectionError, match="managed_roots requires"):
        module.project(policy)
    assert not (brain / "canonical").exists()


def _indexed_fixture(tmp_path):
    module = _load_projector()
    source, brain, policy = _repos(tmp_path)
    data = json.loads(policy.read_text()); data.update(schema_version='1.1', managed_roots=['canonical/context'])
    policy.write_text(json.dumps(data))
    report = module.project(policy)
    _git(brain, 'add', '.'); _git(brain, 'commit', '-qm', 'indexed projection')
    receipts = tmp_path/'completed'; receipts.mkdir()
    (receipts/'00-library-projection.json').write_text(json.dumps(report))
    (receipts/'03-sync.json').write_text(json.dumps({'phases':[{'phase':'sync','status':'ok','details':{'failedFiles':0,'dryRun':False}}]}))
    (receipts/'12-doctor.json').write_text(json.dumps({'status':'healthy'}))
    (receipts/'head.after').write_bytes(subprocess.check_output(['git','-C',str(brain),'rev-parse','HEAD']))
    return module, source, brain, policy, receipts


def test_index_fingerprint_ignores_unrelated_commits(tmp_path):
    m, source, brain, policy, receipts = _indexed_fixture(tmp_path)
    before = m.check_index(policy, receipts)
    (source/'unrelated.md').write_text('# Outside protected source\n')
    _git(source,'add','.'); _git(source,'commit','-qm','unrelated')
    (brain/'other.md').write_text('# Outside protected projection\n')
    _git(brain,'add','.'); _git(brain,'commit','-qm','unrelated')
    assert m.check_index(policy, receipts) == before
    (source/'context/decisions.md').write_text('# Dirty uncommitted\n')
    assert m.check_index(policy, receipts) == before


@pytest.mark.parametrize('change', ['source', 'policy', 'dirty_projection', 'untracked_projection', 'partial_sync', 'old_producer', 'missing_receipt'])
def test_index_guard_rejects_stale_or_incomplete_evidence(tmp_path, change):
    m, source, brain, policy, receipts = _indexed_fixture(tmp_path)
    if change == 'source':
        _commit_source(source, '# Changed\n')
    elif change == 'policy':
        data=json.loads(policy.read_text()); data['entries'][0]['target']='canonical/context/renamed.md';policy.write_text(json.dumps(data))
    elif change == 'dirty_projection':
        (brain/'canonical/context/decisions.md').write_text('# Changed\n')
    elif change == 'untracked_projection':
        (brain/'canonical/context/extra.md').write_text('# Extra\n')
    elif change == 'partial_sync':
        (receipts/'03-sync.json').write_text(json.dumps({'phases':[{'phase':'sync','status':'failed'}]}))
    elif change == 'old_producer':
        report=json.loads((receipts/'00-library-projection.json').read_text());report['projector_sha256']='0'*64;(receipts/'00-library-projection.json').write_text(json.dumps(report))
    else:
        (receipts/'head.after').unlink()
    with pytest.raises((m.ProjectionError, OSError)):
        m.check_index(policy, receipts)


@pytest.mark.linux_only
@pytest.mark.parametrize("publication_status", ["SYNCED", "NO_CHANGE"])
def test_publication_only_queues_on_protected_change(tmp_path, monkeypatch, publication_status):
    m, source, _, policy, receipts = _indexed_fixture(tmp_path)
    state=tmp_path/'publisher.json'; calls=[]; original=m.subprocess.run
    def run(args, **kwargs):
        if args[0]=='/usr/bin/systemctl':
            calls.append(args);return subprocess.CompletedProcess(args,0)
        return original(args,**kwargs)
    monkeypatch.setattr(m.subprocess,'run',run)
    unit='dizevolv-gbrain-atena-maintenance.service'
    for status in ['DEFERRED','HOLD','DRY_RUN']:
        state.write_text(json.dumps({'status':status}))
        assert m.after_publication(policy,receipts,state,unit)['status']=='SKIPPED'
    head=lambda: subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    state.write_text(json.dumps({'status':publication_status,'commit':head()}))
    assert m.after_publication(policy,receipts,state,unit)['status']=='CURRENT'
    assert not calls
    state.write_text(json.dumps({'status':publication_status}))
    with pytest.raises(m.ProjectionError, match='owner HEAD'):
        m.after_publication(policy,receipts,state,unit)
    state.write_text(json.dumps({'status':publication_status,'commit':head()}))
    _commit_source(source,'# Changed\n')
    with pytest.raises(m.ProjectionError, match='owner HEAD'):
        m.after_publication(policy,receipts,state,unit)
    state.write_text(json.dumps({'status':publication_status,'commit':head()}))
    assert m.after_publication(policy,receipts,state,unit)['status']=='REFRESH_QUEUED'
    assert calls==[['/usr/bin/systemctl','start','--no-block',unit]]
    assert m._publication_pending(receipts).exists()
    # Worker exit retains one follow-up even if start merged into an active job.
    assert m.drain_publication(policy,receipts,unit)['status']=='REFRESH_QUEUED'
    assert len(calls)==2 and not m._publication_pending(receipts).exists()
    assert m.drain_publication(policy,receipts,unit)['status']=='SKIPPED'
    assert len(calls)==2



def test_index_guard_accepts_native_sync_progress_before_json(tmp_path):
    m, _, _, policy, receipts = _indexed_fixture(tmp_path)
    path=receipts/'03-sync.json'
    path.write_text('Syncing changed canonical pages...\n'+path.read_text())
    assert m.check_index(policy,receipts)['status']=='CURRENT'

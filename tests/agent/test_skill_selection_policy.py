"""Profile policy changes guidance, never catalogue visibility or tool access."""
import os
from pathlib import Path

import pytest

from agent.prompt_builder import build_skills_system_prompt, clear_skills_system_prompt_cache


def make_home(path: Path, policy=None):
    skill = path / "skills" / "synthetic-workflow"
    skill.mkdir(parents=True, exist_ok=True)
    if not (skill / "SKILL.md").exists():
        (skill / "SKILL.md").write_text(
            "---\nname: synthetic-workflow\ndescription: Validate synthetic fixtures.\n---\n"
            "Use the required checks for the fixture.\n"
        )
    config = path / "config.yaml"
    text = "skills:\n  project_discovery: false\n"
    if policy is not None:
        text += f"  selection_policy: {policy}\n"
    config.write_text(text)
    # Native config caches use stat metadata. No sleep or live config mutation.
    stat = config.stat()
    os.utime(config, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    return path


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "ambient"))
    clear_skills_system_prompt_cache()
    return tmp_path


def render(home):
    return build_skills_system_prompt(
        skills_dir_override=home / "skills",
        available_tools={"skill_view", "skill_manage"},
        available_toolsets={"skills"},
    )


def catalogue(prompt):
    return prompt.split("<available_skills>", 1)[1].split("</available_skills>", 1)[0]


@pytest.mark.parametrize("platform", ["cli", "telegram"])
def test_policy_preserves_catalogue_and_required_workflows(isolated, monkeypatch, platform):
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", platform)
    broad = render(make_home(isolated / "broad", "broad"))
    focused = render(make_home(isolated / "focused", "task_relevant"))
    assert catalogue(broad) == catalogue(focused)
    assert "synthetic-workflow" in focused
    assert "even partially relevant" in broad
    assert "even partially relevant" not in focused
    assert "user explicitly requests" in focused
    assert "applicable instruction requires" in focused
    assert "Required safety, quality and project procedures still apply" in focused
    assert "inspect that skill" in focused
    assert len(focused) < len(broad)


def test_absent_setting_preserves_broad_behavior(isolated):
    assert render(make_home(isolated / "default")) == render(make_home(isolated / "broad", "broad"))


@pytest.mark.parametrize("invalid", ["unknown", "null", "[]", "true"])
def test_invalid_setting_falls_back_without_hiding_skills(isolated, invalid):
    assert render(make_home(isolated / "invalid", invalid)) == render(make_home(isolated / "broad", "broad"))


def test_policy_participates_in_render_cache_and_keeps_snapshot_reusable(isolated):
    home = make_home(isolated / "profile", "broad")
    broad = render(home)
    snapshot = home / ".skills_prompt_snapshot.json"
    before = snapshot.read_bytes()
    make_home(home, "task_relevant")
    focused = render(home)
    assert focused != broad
    assert render(home) == focused
    assert snapshot.read_bytes() == before
    make_home(home, "broad")
    assert render(home) == broad


def test_explicit_profile_overrides_ambient_policy(isolated, monkeypatch):
    focused = make_home(isolated / "focused", "task_relevant")
    broad = make_home(isolated / "broad", "broad")
    monkeypatch.setenv("HERMES_HOME", str(focused))
    assert "even partially relevant" in render(broad)
    monkeypatch.setenv("HERMES_HOME", str(broad))
    assert "even partially relevant" not in render(focused)

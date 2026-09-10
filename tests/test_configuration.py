import io
import json
from importlib.metadata import distribution
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from unitybuild.cli import app
from unitybuild.history import render_history
from unitybuild.projects import ProjectCatalog
from unitybuild.settings_editor import SettingsDraft


@pytest.fixture
def project_root(tmp_path):
    root = tmp_path / "한글 프로젝트"
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.17f1\n")
    return root


def test_configuration_preserves_custom_builder(project_root):
    path = project_root / "unitybuild.toml"
    path.write_text(
        '# Custom builder\nschema_version = 1\n[build]\nplatform = "StandaloneWindows64"\n'
        'method = "Game.Editor.Build"\noutput_environment = "GAME_OUTPUT"\n', encoding="utf-8",
    )
    original = path.read_bytes()
    catalog = ProjectCatalog()
    selected = catalog.load_project(path)
    assert selected.build_method == "Game.Editor.Build" and selected.output_environment == "GAME_OUTPUT"
    result = CliRunner().invoke(app, ["--project", str(path), "build", "--dry-run", "--details"])
    assert result.exit_code == 0, result.output
    assert "Game.Editor.Build" in result.output
    assert path.read_bytes() == original and not SettingsDraft(project_root, catalog).save()


def test_obsolete_config_is_not_read_or_used_for_conflict_detection(project_root):
    (project_root / "unity-build.toml").write_text("invalid obsolete configuration")
    catalog = ProjectCatalog()
    assert catalog.load_project(project_root).platform == ""
    result = CliRunner().invoke(app, ["project", "init", str(project_root), "--platform", "StandaloneWindows64"])
    assert result.exit_code == 0, result.output
    assert catalog.load_project(project_root).platform == "StandaloneWindows64"
    assert (project_root / "Assets/Editor/UnityBuild/Builder.cs").is_file()


def test_obsolete_environment_and_registry_are_ignored(tmp_path, project_root, monkeypatch):
    monkeypatch.delenv("UNITYBUILD_HOME", raising=False)
    monkeypatch.delenv("UNITYBUILD_PROJECT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setenv("UNITY_BUILDKIT_PROJECT", str(project_root))
    monkeypatch.setenv("UNITY_BUILDKIT_HOME", str(tmp_path / "obsolete-home"))
    old = tmp_path / ".unity-buildkit/projects.json"
    old.parent.mkdir()
    old.write_text(json.dumps({"projects": {"old": str(project_root)}, "default": "old"}))
    catalog = ProjectCatalog()
    assert catalog.read_registry() == ({}, "")
    assert catalog.resolve_project(required=False) is None
    assert catalog.registry_path() == tmp_path / ".unitybuild/projects.json"


def test_current_environment_and_registry_work(project_root, tmp_path, monkeypatch):
    monkeypatch.setenv("UNITYBUILD_PROJECT", str(project_root))
    monkeypatch.setenv("UNITYBUILD_HOME", str(tmp_path / "registry"))
    catalog = ProjectCatalog()
    selected = catalog.resolve_project()
    assert selected is not None and selected.root == project_root
    catalog.write_registry({"game": str(project_root)}, "game")
    assert catalog.read_registry() == ({"game": str(project_root)}, "game")


def test_history_does_not_read_obsolete_directory(tmp_path):
    for directory, profile in (("unity-buildkit", "Obsolete Record"), ("unitybuild", "Current Record")):
        path = tmp_path / "Logs" / directory / "build.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"status": "succeeded", "profile": profile}))
    output = io.StringIO()
    render_history(Console(file=output), tmp_path, "Logs/unitybuild")
    assert "Current Record" in output.getvalue() and "Obsolete Record" not in output.getvalue()


def test_distribution_exposes_only_current_command():
    commands = {item.name for item in distribution("unitybuild").entry_points if item.group == "console_scripts"}
    assert commands == {"unitybuild"}

from dataclasses import replace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from unitybuild.builds import build_project
from unitybuild.cli import app
from unitybuild.projects import ProjectCatalog, ProjectError
from unitybuild.scenes import SETTINGS, SceneDraft, enabled_scenes


@pytest.fixture
def project(tmp_path):
    (tmp_path / "Assets").mkdir()
    for index, name in enumerate(("시작", "Second")):
        path = tmp_path / f"Assets/{name}.unity"
        path.touch()
        path.with_suffix(".unity.meta").write_text(f"guid: {index + 1:032x}\n")
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.17f1\n")
    (tmp_path / SETTINGS).write_text(
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!1045 &1\nEditorBuildSettings:\n"
        "  m_ObjectHideFlags: 0\n  serializedVersion: 2\n  m_Scenes: []\n  m_configObjects: {keep: 123}\n"
    )
    return replace(ProjectCatalog().load_project(tmp_path), platform="StandaloneWindows64")


def test_empty_scenes_are_reported_without_starting_unity(project):
    with patch("unitybuild.builds.resolve_editor") as editor, patch("unitybuild.builds.ensure_builder") as helper:
        plans = []
        build_project(project, dry_run=True, on_plan=plans.append)
        assert plans[0].scene_error and plans[0].scenes == ()
        with pytest.raises(ProjectError, match="No scenes are enabled"):
            build_project(project)
    editor.assert_not_called()
    helper.assert_not_called()
    assert not (project.root / "Logs").exists()


def test_scene_menu_orders_and_saves_without_replacing_other_unity_settings(project):
    with patch("unitybuild.scenes.select_build_session", return_value=False):
        result = CliRunner().invoke(app, ["--project", str(project.root), "project", "scenes", "--plain"],
                                    input="1\n1\n1\n1\n3\n2\n5\n")
    assert result.exit_code == 0, result.output
    assert enabled_scenes(project.root) == ("Assets/시작.unity", "Assets/Second.unity")
    text = (project.root / SETTINGS).read_text(encoding="utf-8")
    assert "m_configObjects: {keep: 123}" in text
    assert "%TAG !u! tag:unity3d.com,2011:" in text
    assert not (project.root / "unitybuild.toml").exists()


def test_scene_cancel_and_external_edit_preserve_files(project):
    original = (project.root / SETTINGS).read_bytes()
    result = CliRunner().invoke(app, ["--project", str(project.root), "project", "scenes", "--plain"], input="1\n1\n0\n")
    assert result.exit_code == 0 and (project.root / SETTINGS).read_bytes() == original
    draft = SceneDraft(project.root)
    draft.selected = ["Assets/Second.unity"]
    external = original + b"# another editor\n"
    (project.root / SETTINGS).write_bytes(external)
    with patch("unitybuild.scenes.select_build_session", return_value=False), pytest.raises(ProjectError):
        draft.save()
    assert (project.root / SETTINGS).read_bytes() == external

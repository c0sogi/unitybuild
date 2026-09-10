import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from unitybuild.build_session import BuildSessionError
from unitybuild.builds import BUILDER_PATH, build_project, output_path
from unitybuild.cli import app
from unitybuild.profiles import BuildProfile
from unitybuild.projects import ProjectCatalog, ProjectError


def make_project(tmp_path):
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.14f1\n")
    (tmp_path / "Assets/Main.unity").touch()
    (tmp_path / "ProjectSettings/EditorBuildSettings.asset").write_text(
        "EditorBuildSettings:\n  m_Scenes:\n  - enabled: 1\n    path: Assets/Main.unity\n"
    )
    return replace(ProjectCatalog().load_project(tmp_path), platform="Android")


def test_build_prepares_helper_without_init_and_preserves_it_on_next_build(tmp_path):
    project = make_project(tmp_path)
    helper = tmp_path / BUILDER_PATH
    build_project(project, dry_run=True)
    assert not helper.exists() and not (tmp_path / "Logs").exists()
    output = output_path(project, BuildProfile.RELEASE)

    def execute(command, **kwargs):
        assert helper.is_file()
        assert not (tmp_path / "unitybuild.toml").exists()
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(output.read_bytes() + b"next" if output.exists() else b"first")
        return subprocess.CompletedProcess(command, 0)

    with (
        patch("unitybuild.builds.resolve_editor", return_value=Path("Unity.exe")),
        patch("unitybuild.builds.select_build_session", return_value=False),
    ):
        build_project(project, runner=execute)
        helper.write_text(helper.read_text() + "\n// Project-owned customization\n")
        expected = helper.read_bytes()
        build_project(project, runner=execute)
    assert helper.read_bytes() == expected
    result = CliRunner().invoke(app, ["project", "init", str(tmp_path), "--platform", "Android"])
    assert result.exit_code == 0, result.output
    assert helper.read_bytes() == expected


def test_locked_editor_blocks_bootstrap_without_changing_assets(tmp_path):
    project = make_project(tmp_path)
    with (
        patch("unitybuild.builds.resolve_editor", return_value=Path("Unity.exe")),
        patch("unitybuild.builds.select_build_session", side_effect=BuildSessionError("busy")),
        pytest.raises(BuildSessionError),
    ):
        build_project(project)
    assert not (tmp_path / BUILDER_PATH).exists()


def test_custom_builder_receives_configuration_without_shared_helper(tmp_path):
    project = replace(
        make_project(tmp_path), build_method="Example.Editor.Build", configuration_environment="GAME_CONFIGURATION"
    )
    output = output_path(project, BuildProfile.DEBUG)

    def execute(command, *, env, **kwargs):
        assert env["GAME_CONFIGURATION"] == "development"
        assert env["UNITYBUILD_PROFILE"] == "debug"
        assert not (tmp_path / BUILDER_PATH).exists()
        output.parent.mkdir(parents=True)
        output.write_bytes(b"debug")
        return subprocess.CompletedProcess(command, 0)

    with (
        patch("unitybuild.builds.resolve_editor", return_value=Path("Unity.exe")),
        patch("unitybuild.builds.select_build_session", return_value=False),
    ):
        build_project(project, BuildProfile.DEBUG, runner=execute)


@pytest.mark.parametrize("name", ["PATH", "HOME", "UNITYBUILD_OUTPUT", "UNITYBUILD_PROFILE", "BAD-NAME"])
def test_configuration_variable_cannot_overwrite_reserved_environment(tmp_path, name):
    make_project(tmp_path)
    (tmp_path / "unitybuild.toml").write_text(f'schema_version = 1\n[build]\nconfiguration_environment = "{name}"\n')
    with pytest.raises(ProjectError):
        ProjectCatalog().load_project(tmp_path)

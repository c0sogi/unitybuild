import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from unitybuild.builds import build_project
from unitybuild.cli import app
from unitybuild.profiles import discover_build_profiles, resolve_build_profile
from unitybuild.projects import Project, ProjectError


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "Independent Game"
    assets = root / "Assets/Settings/Build Profiles"
    assets.mkdir(parents=True)
    for fixture in Path(__file__).with_name("fixtures").glob("*.asset"):
        shutil.copyfile(fixture, assets / fixture.name)
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.14f1\n")
    return Project(root, "Independent Game")


def test_reads_real_unity_serialization_and_preserves_names(project):
    profiles = discover_build_profiles(project)
    assert [(p.name, p.platform, p.development) for p in profiles] == [
        ("Android Release", "Android", False),
        ("Android Development", "Android", True),
    ]
    assert resolve_build_profile(project, "debug").name == "Android Development"
    assert resolve_build_profile(project, "Android Release").configuration == "release"


def test_native_command_uses_asset_without_generating_script(project):
    plans = []
    result = build_project(project, resolve_build_profile(project, "debug"), dry_run=True, on_plan=plans.append)
    command = plans[0].command
    assert command[command.index("-activeBuildProfile") + 1].endswith("android-development.asset")
    assert command[command.index("-build") + 1] == str(result.output)
    assert "-executeMethod" not in command and "-buildTarget" not in command
    assert not plans[0].setup_required and not list((project.root / "Assets").rglob("*.cs"))
    assert "Debug" in result.output.parts
    assert not (project.root / "Logs").exists()


def test_duplicate_names_require_exact_asset_path(project):
    source = project.root / "Assets/Settings/Build Profiles/android-release.asset"
    shutil.copyfile(source, source.with_name("another.asset"))
    for selection in ("release", "Android Release"):
        with pytest.raises(ProjectError, match="ambiguous"):
            resolve_build_profile(project, selection)
    assert resolve_build_profile(project, source.relative_to(project.root).as_posix()).name == "Android Release"


def test_saved_profile_platform_and_bundle_determine_output(project):
    path = project.root / "Assets/Settings/Build Profiles/android-release.asset"
    path.write_text(path.read_text().replace("m_BuildAppBundle: 0", "m_BuildAppBundle: 1"))
    plans = []
    result = build_project(
        replace(project, platform="StandaloneWindows64", output="Builds/Game.exe"), dry_run=True, on_plan=plans.append
    )
    assert result.output.suffix == ".aab" and plans[0].project.platform == "Android"


def test_cli_rejects_platform_override_conflicting_with_saved_profile(project):
    result = CliRunner().invoke(
        app, ["--project", str(project.root), "build", "--platform", "StandaloneWindows64", "--dry-run"]
    )
    assert result.exit_code == 1 and "conflicts" in result.output
    assert not (project.root / "Logs").exists()


@pytest.mark.parametrize("outcome", ["success", "failure", "stale", "zero_exit_failure"])
def test_native_results_record_selected_asset_and_never_fallback(project, outcome):
    def run(command, **kwargs):
        if outcome != "stale":
            output = Path(command[command.index("-build") + 1])
            output.parent.mkdir(parents=True)
            output.write_bytes(b"output even if Unity fails")
        return subprocess.CompletedProcess(
            command,
            1 if outcome == "failure" else 0,
            "FAILURE: Build failed with an exception." if outcome == "zero_exit_failure" else "",
        )

    with (
        patch("unitybuild.builds.resolve_editor", return_value=Path("Unity.exe")),
        patch("unitybuild.builds.select_build_session"),
        patch("unitybuild.builds.ensure_builder") as helper,
    ):
        if outcome == "success":
            build_project(project, runner=run)
        else:
            with pytest.raises(ProjectError):
                build_project(project, runner=run)
        helper.assert_not_called()
    record = json.loads(next((project.root / "Logs/unitybuild").glob("*.json")).read_text())
    assert record["profile_asset"].endswith("android-release.asset")
    assert record["status"] == ("succeeded" if outcome == "success" else "failed")


def test_cli_accepts_saved_names_and_lists_assets(project):
    runner = CliRunner()
    listing = runner.invoke(app, ["--project", str(project.root), "profiles"])
    assert listing.exit_code == 0 and "Android Development" in listing.output
    preview = runner.invoke(
        app, ["--project", str(project.root), "build", "--profile", "Android Development", "--dry-run", "--details"]
    )
    assert preview.exit_code == 0, preview.output
    assert "Unity native build" in preview.output and "Android Development" in preview.output


def test_missing_asset_never_silently_uses_legacy_builder(project):
    with pytest.raises(ProjectError, match="missing"):
        resolve_build_profile(project, "Assets/Deleted.asset")


def test_native_build_does_not_inject_script_variables(project, monkeypatch):
    project = replace(
        project, build_method="Game.Editor.Build", output_environment="GAME_OUTPUT",
        configuration_environment="GAME_CONFIGURATION",
    )
    script_keys = (
        "GAME_OUTPUT", "GAME_CONFIGURATION", "UNITYBUILD_OUTPUT",
        "UNITYBUILD_PROFILE", "UNITYBUILD_PLATFORM",
    )
    for key in script_keys:
        monkeypatch.delenv(key, raising=False)
    supplied = {"MY_BUILD_SETTING": "keep"}

    def run(command, *, env, **kwargs):
        assert not set(script_keys).intersection(env)
        assert env["MY_BUILD_SETTING"] == "keep"
        assert "-executeMethod" not in command
        output = Path(command[command.index("-build") + 1])
        output.parent.mkdir(parents=True)
        output.write_bytes(b"native build")
        return subprocess.CompletedProcess(command, 0)

    with (
        patch("unitybuild.builds.resolve_editor", return_value=Path("Unity.exe")),
        patch("unitybuild.builds.select_build_session"),
    ):
        build_project(project, runner=run, environment=supplied)
    assert supplied == {"MY_BUILD_SETTING": "keep"}

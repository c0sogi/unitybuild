from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from unitybuild.artifacts import ArtifactStore, source_fingerprint
from unitybuild.builds import build_project
from unitybuild.cli import app
from unitybuild.platforms import platform_label
from unitybuild.projects import ProjectCatalog, ProjectError, discover_projects
from unitybuild.windows import resolve_manifest


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UNITYBUILD_HOME", str(tmp_path / "registry"))
    monkeypatch.delenv("UNITYBUILD_PROJECT", raising=False)
    monkeypatch.delenv("UNITYBUILD_PROJECT", raising=False)
    root = tmp_path / "독립 Unity Project"
    (root / "Assets").mkdir(parents=True)
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.14f1\n")
    return ProjectCatalog().load_project(root)


def test_discovery_requires_unity_not_a_repository(project, tmp_path):
    assert discover_projects(tmp_path) == [project.root]
    with pytest.raises(ProjectError, match="Not a Unity project"):
        ProjectCatalog().load_project(tmp_path)


def test_catalogs_do_not_share_selection_or_preset_policy(project, tmp_path, monkeypatch):
    generic = ProjectCatalog()
    custom = ProjectCatalog(home_env="CUSTOM_BUILD_HOME", preset_resolver=lambda _: "my-game")
    monkeypatch.setenv("CUSTOM_BUILD_HOME", str(tmp_path / "custom-registry"))
    custom.write_registry({"game": str(project.root)}, "game")
    assert generic.read_registry() == ({}, "")
    selected = custom.resolve_project("game")
    assert selected is not None and selected.preset == "my-game"
    assert generic.load_project(project.root).preset == "unity"


@pytest.mark.parametrize(
    "platform", ["Android", "StandaloneWindows64", "StandaloneLinux64", "StandaloneOSX", "WSAPlayer"]
)
def test_init_and_plan_run_without_unity_and_without_overwriting(project, platform):
    runner = CliRunner()
    result = runner.invoke(app, ["project", "init", str(project.root), "--platform", platform])
    assert result.exit_code == 0, result.output
    builder = project.root / "Assets/Editor/UnityBuild/Builder.cs"
    content = builder.read_bytes()
    again = runner.invoke(app, ["project", "init", str(project.root)])
    assert again.exit_code == 1
    assert builder.read_bytes() == content
    result = runner.invoke(app, ["--project", str(project.root), "build", "--profile", "debug", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert platform_label(platform) in result.output and "Debug" in result.output
    assert not (project.root / "Logs").exists()


@pytest.mark.parametrize("outcome", ["success", "reused", "failure", "stale", "cancel"])
def test_library_is_quiet_and_records_actual_build_outcome(project, outcome, capsys):
    project = replace(project, platform="Android", output="Builds/App.apk", build_method="MyGame.Build.Perform")
    output = project.root / project.output
    output.parent.mkdir()
    output.write_bytes(b"old")

    def execute(command, *, cwd, env, **kwargs):
        assert cwd == project.root
        assert env["UNITYBUILD_OUTPUT"] == str(output)
        if outcome == "cancel":
            raise KeyboardInterrupt
        if outcome == "success":
            output.write_bytes(b"new build output")
        return subprocess.CompletedProcess(
            command, 1 if outcome == "failure" else 0,
            "Build Finished, Result: Success.\n" if outcome == "reused" else "",
        )

    with (
        patch("unitybuild.builds.resolve_editor", return_value=Path("Unity.exe")),
        patch("unitybuild.builds.select_build_session", return_value=False),
    ):
        if outcome in ("success", "reused"):
            result = build_project(project, runner=execute)
            assert result.output == output and result.record is not None and result.record.is_file()
        else:
            with pytest.raises(KeyboardInterrupt if outcome == "cancel" else ProjectError):
                build_project(project, runner=execute)
    assert capsys.readouterr().out == ""
    record = json.loads(next((project.root / "Logs/unitybuild").glob("*.json")).read_text(encoding="utf-8"))
    assert record["status"] == {"success": "succeeded", "reused": "succeeded", "cancel": "cancelled"}.get(outcome, "failed")
    if outcome == "reused":
        assert record["output_reused"] is True


def test_release_cache_and_incremental_baseline_invalidate_independently(project):
    source = project.root / "Assets/source.cs"
    source.write_text("first")
    cache = project.root / "Library/Bee"
    cache.mkdir(parents=True)
    apk = project.root / "app.apk"
    apk.write_bytes(b"apk")
    configuration = ["config-1"]
    store = ArtifactStore(
        project.root / "state",
        project.root,
        "android",
        "org.example.game",
        lambda: configuration[0],
        lambda: source_fingerprint(project.root),
    )
    baseline = store.write_development_baseline(apk)
    fingerprint = source_fingerprint(project.root)
    store.write_release_artifact(apk, fingerprint)
    assert store.development_baseline_status(apk)[0] == baseline
    source.write_text("second")
    assert store.release_artifact_status(apk, source_fingerprint(project.root))[0] is None
    assert store.development_baseline_status(apk)[0] == baseline
    configuration[0] = "config-2"
    assert store.development_baseline_status(apk)[0] is None


def test_uwp_layout_uses_selected_app_name_and_preserves_existing_layout(tmp_path):
    package = tmp_path / "MyGame"
    package.mkdir()
    (package / "Package.appxmanifest").write_text("template")
    output = tmp_path / "build/bin/ARM64/Release"
    output.mkdir(parents=True)
    (output / "AppxManifest.xml").write_text('<App Name="$targetnametoken$" Language="x-generate" />')
    (output / "MyGame.exe").write_bytes(b"exe")
    existing = tmp_path / "LooseDeployLayout/keep.txt"
    existing.parent.mkdir()
    existing.write_text("preserve")
    manifest = resolve_manifest(tmp_path, "Release", "ARM64")
    assert 'Name="MyGame"' in manifest.read_text()
    assert (manifest.parent / "MyGame.exe").read_bytes() == b"exe"
    assert existing.read_text() == "preserve"
    other = tmp_path / "Other"
    other.mkdir()
    (other / "Package.appxmanifest").touch()
    with pytest.raises(ValueError, match="Expected one"):
        resolve_manifest(tmp_path, "Release", "ARM64")

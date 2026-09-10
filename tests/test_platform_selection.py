from dataclasses import replace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from unitybuild.builds import build_project
from unitybuild.cli import app
from unitybuild.menu import UnityProjectMenu
from unitybuild.platforms import default_output, platform_label, supported_platforms
from unitybuild.projects import ProjectCatalog, ProjectError
from unitybuild.targets import Target, TargetCatalog


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("unitybuild.targets.discover_targets", lambda _: TargetCatalog(
        "2022.3.17f1", None, tuple(Target(key, key, None, True, "test") for key in supported_platforms())
    ))
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.17f1\n")
    return ProjectCatalog().load_project(tmp_path)


@pytest.mark.parametrize("config", ["", "schema_version = 1\n[project]\nname = 'Game'\n"])
def test_unknown_target_never_defaults_to_android(project, config):
    if config:
        (project.root / "unitybuild.toml").write_text(config)
    selected = ProjectCatalog().load_project(project.root)
    assert selected.platform == "" and selected.output == ""
    with pytest.raises(ProjectError, match="No build platform selected"):
        build_project(selected, dry_run=True)
    assert not (project.root / "Logs").exists()
    assert list((project.root / "Assets").iterdir()) == []


@pytest.mark.parametrize("platform", supported_platforms())
def test_explicit_target_drives_command_and_output_without_writing_config(project, platform):
    result = CliRunner().invoke(
        app, ["--project", str(project.root), "build", "--platform", platform, "--dry-run", "--details"]
    )
    assert result.exit_code == 0, result.output
    assert platform_label(platform) in result.output
    assert default_output(platform) in result.output
    unwrapped = "".join(result.output.replace("│", "").split())
    assert f"-buildTarget{platform}" in unwrapped
    assert not (project.root / "unitybuild.toml").exists()
    assert list((project.root / "Assets").iterdir()) == []


def test_init_requires_target_before_writing_any_files(project):
    result = CliRunner().invoke(app, ["project", "init", str(project.root)])
    assert result.exit_code == 1 and "Choose a platform explicitly" in result.output
    assert not (project.root / "unitybuild.toml").exists()
    assert list((project.root / "Assets").iterdir()) == []


def test_configured_platform_without_output_gets_matching_extension(project):
    (project.root / "unitybuild.toml").write_text('schema_version = 1\n[build]\nplatform = "StandaloneWindows64"\n')
    selected = ProjectCatalog().load_project(project.root)
    assert build_project(selected, dry_run=True).output.suffix == ".exe"


def test_menu_passes_explicit_target_to_child_and_cancel_starts_nothing(project):
    menu = UnityProjectMenu(project, plain=True)
    linux = list(supported_platforms()).index("StandaloneLinux64")
    with (
        patch.object(menu, "choose", side_effect=[1, 0, linux, None]),
        patch.object(menu, "pause"),
        patch("unitybuild.menu.run_terminal_module", return_value=0) as child,
    ):
        menu.run()
    assert child.call_args.args[1][-4:] == ["--platform", "StandaloneLinux64", "--dry-run", "--details"]
    with (
        patch.object(menu, "choose", side_effect=[0, 0, None, None]),
        patch("unitybuild.menu.run_terminal_module") as child,
    ):
        menu.run()
    child.assert_not_called()


def test_explicit_library_platform_uses_matching_default_output(project):
    assert build_project(replace(project, platform="StandaloneOSX"), dry_run=True).output.name == "App.app"


def test_one_platform_definition_drives_config_cli_profile_and_discovery(project, monkeypatch):
    import json
    from pathlib import Path

    from unitybuild.platforms import PLATFORMS, Platform
    from unitybuild.targets import inspect_installation, installation_paths

    monkeypatch.setitem(PLATFORMS, "ExamplePlayer", Platform(
        "ExamplePlayer", "Example player", ("example-module",), "ExampleSupport",
        build_supported=True, default_output="Builds/Example/game.bin", profile_target=12345,
    ))
    config = project.root / "unitybuild.toml"
    config.write_text('schema_version = 1\n[build]\nplatform = "ExamplePlayer"\n')
    loaded = ProjectCatalog().load_project(project.root)
    assert loaded.output == "Builds/Example/game.bin"
    runner = CliRunner()
    result = runner.invoke(app, ["--project", str(project.root), "build", "--platform", "ExamplePlayer", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Example player" in result.output and "game.bin" in result.output

    editor = project.root / "editor/Editor/Unity.exe"
    metadata, engines = installation_paths(editor)
    (engines / "ExampleSupport").mkdir(parents=True)
    metadata.write_text(json.dumps([{"id": "example-module", "category": "Platforms"}]))
    target = inspect_installation(editor).targets[0]
    assert target.identifier == "ExamplePlayer" and target.installed and target.supported

    (project.root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.14f1\n")
    asset = Path(__file__).with_name("fixtures") / "android-release.asset"
    (project.root / "Assets/Example.asset").write_text(asset.read_text().replace("m_BuildTarget: 13", "m_BuildTarget: 12345"))
    plans = []
    built = build_project(loaded, dry_run=True, on_plan=plans.append)
    assert built.output == project.root / "Builds/Example/game.bin"
    assert "-activeBuildProfile" in plans[0].command and "-executeMethod" not in plans[0].command


def test_output_default_does_not_grant_build_support(project, monkeypatch):
    from unitybuild.platforms import PLATFORMS

    monkeypatch.setitem(PLATFORMS, "WebGL", replace(PLATFORMS["WebGL"], default_output="Builds/Web"))
    (project.root / "unitybuild.toml").write_text(
        'schema_version = 1\n[build]\nplatform = "WebGL"\noutput = "Builds/CustomWeb"\n'
    )
    with pytest.raises(ProjectError, match="Unsupported build.platform"):
        ProjectCatalog().load_project(project.root)
    (project.root / "unitybuild.toml").unlink()
    result = CliRunner().invoke(app, ["project", "init", str(project.root), "--platform", "WebGL"])
    assert result.exit_code == 1 and "Unsupported build platform" in result.output
    assert not (project.root / "unitybuild.toml").exists()
    assert not list((project.root / "Assets").rglob("*.cs"))

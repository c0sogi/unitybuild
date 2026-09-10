import io
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from unitybuild.builds import build_project
from unitybuild.cli import app
from unitybuild.menu import UnityProjectMenu
from unitybuild.platforms import supported_platforms
from unitybuild.projects import ProjectCatalog, ProjectError
from unitybuild.settings_editor import BuildSettingsEditor, SettingsDraft
from unitybuild.targets import Target, TargetCatalog


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr("unitybuild.targets.discover_targets", lambda _: TargetCatalog(
        "2022.3.17f1", None, tuple(Target(key, key, None, True, "test") for key in supported_platforms())
    ))
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.17f1\n")
    return tmp_path


def test_plain_menu_creates_settings_then_build_uses_saved_values(root):
    result = CliRunner().invoke(
        app, ["--project", str(root), "project", "settings", "--plain"],
        input="1\n2\n2\nBuilds/My Game.exe\n5\n\n6\n",
    )
    assert result.exit_code == 0, result.output
    assert 'platform = "StandaloneWindows64"' in result.output  # Raw TOML preview.
    project = ProjectCatalog().load_project(root)
    assert project.platform == "StandaloneWindows64"
    assert project.output == "Builds/My Game.exe"
    assert build_project(project, dry_run=True).output == root / "Builds/My Game.exe"
    assert list((root / "Assets").iterdir()) == []  # Saving does not install the helper.
    assert not (root / "Logs").exists()


def test_cancel_discards_changes_without_creating_config(root):
    result = CliRunner().invoke(
        app, ["--project", str(root), "project", "settings", "--plain"], input="1\n2\n0\n",
    )
    assert result.exit_code == 0, result.output
    assert not (root / "unitybuild.toml").exists()


def test_edit_preserves_comments_bom_custom_fields_and_fallback_file(root):
    path = root / "unitybuild.toml"
    text = (
        '# Keep this comment\r\nschema_version = 1\r\n[project]\r\npreset = "my-game"\r\n'
        '[build]\r\nplatform = "Android" # Phone\r\nmethod = "Game.Build.Run"\r\n'
        'configuration_environment = "MY_GAME_CONFIG"\r\noutput = "Builds/custom.apk" # Keep output comment\r\n'
    )
    path.write_bytes(b"\xef\xbb\xbf" + text.encode())
    catalog = ProjectCatalog(config_name="adapter.toml", config_fallbacks=("unitybuild.toml",))
    draft = SettingsDraft(root, catalog)
    draft.set_value("build", "output", "Builds/renamed.apk")
    assert draft.save()
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert b'# Keep this comment\r\n' in data
    assert b'"Android" # Phone' in data
    assert b'"Builds/renamed.apk" # Keep output comment' in data
    selected = catalog.load_project(root)
    assert selected.preset == "my-game" and selected.configuration_environment == "MY_GAME_CONFIG"
    assert not (root / "adapter.toml").exists()
    unchanged = SettingsDraft(root, catalog)
    before = path.stat().st_mtime_ns
    assert not unchanged.save() and path.stat().st_mtime_ns == before


@pytest.mark.parametrize("key,value", [
    ("method", "Not a qualified method"),
    ("output_environment", "PATH"),
    ("configuration_environment", "UNITYBUILD_PROFILE"),
    ("output", "Assets/Game.exe"),
])
def test_invalid_draft_never_replaces_existing_settings(root, key, value):
    path = root / "unitybuild.toml"
    original = b'schema_version = 1\n[build]\nplatform = "StandaloneWindows64"\n'
    path.write_bytes(original)
    draft = SettingsDraft(root, ProjectCatalog())
    draft.set_value("build", key, value)
    with pytest.raises(ProjectError):
        draft.save()
    assert path.read_bytes() == original


def test_external_edit_is_not_overwritten(root):
    draft = SettingsDraft(root, ProjectCatalog())
    draft.set_value("build", "platform", "StandaloneLinux64")
    external = b'schema_version = 1\n# Written by another editor\n'
    draft.path.write_bytes(external)
    with pytest.raises(ProjectError, match="outside this menu"):
        draft.save()
    assert draft.path.read_bytes() == external


def test_settings_menu_returns_to_same_project_with_saved_values(root):
    menu = UnityProjectMenu(ProjectCatalog().load_project(root), plain=True)
    menu.console = Console(file=io.StringIO())
    with patch("unitybuild.menu.Menu.choose", side_effect=[3, 0, 2, 5, None]):
        menu.run()
    assert menu.project.platform == "StandaloneLinux64"


@pytest.mark.parametrize("output,expected", [("Builds/App.apk", ""), ("Builds/custom.apk", "Builds/custom.apk")])
def test_platform_change_resets_only_standard_output(root, output, expected):
    editor = BuildSettingsEditor(root, catalog=ProjectCatalog(), plain=True, console=Console(file=io.StringIO()))
    editor.draft.set_value("build", "platform", "Android")
    editor.draft.set_value("build", "output", output)
    with patch.object(editor, "choose", side_effect=[0, 1, 5]):
        assert editor.run()
    assert editor.draft.value("build", "output") == expected


@pytest.mark.parametrize("save", [True, False])
def test_script_settings_are_separate_and_require_parent_save(root, save):
    stream = io.StringIO()
    editor = BuildSettingsEditor(root, catalog=ProjectCatalog(), plain=True, console=Console(file=stream))
    editor.show()
    assert "environment variable" not in stream.getvalue()
    assert "UnityBuild.Builder.Build" not in stream.getvalue()
    with (
        patch.object(editor, "choose", side_effect=[3, 0, 1, 2, None, 5 if save else None]),
        patch.object(editor, "text_input", side_effect=["Game.Editor.Build", "GAME_OUTPUT", "GAME_CONFIG"]),
    ):
        assert editor.run() is save
    if save:
        project = ProjectCatalog().load_project(root)
        assert project.build_method == "Game.Editor.Build"
        assert project.output_environment == "GAME_OUTPUT"
        assert project.configuration_environment == "GAME_CONFIG"
    else:
        assert not (root / "unitybuild.toml").exists()

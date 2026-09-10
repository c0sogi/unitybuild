import io
import json
from unittest.mock import patch

import pytest
from rich.console import Console

from unitybuild.menu import Menu
from unitybuild.projects import ProjectError
from unitybuild.targets import (
    Target,
    TargetCatalog,
    check_target_module,
    choose_target,
    discover_targets,
    inspect_installation,
    installation_paths,
)


def write_modules(path, *ids):
    path.write_text(
        json.dumps([{"id": key, "name": f"Unity {key} support", "category": "Platforms", "parent": ""} for key in ids]),
        encoding="utf-8",
    )


@pytest.mark.parametrize("layout", ["Editor/Unity.exe", "Editor/Unity", "Unity.app/Contents/MacOS/Unity"])
def test_catalog_is_discovered_from_installation_and_reflects_changes(tmp_path, layout):
    editor = tmp_path / layout
    editor.parent.mkdir(parents=True)
    editor.touch()
    metadata, engines = installation_paths(editor)
    engines.mkdir(parents=True)
    write_modules(metadata, "android", "webgl", "future-platform")
    (engines / "AndroidPlayer").mkdir()
    before = {item.identifier: item for item in inspect_installation(editor).targets}
    assert set(before) == {"Android", "WebGL", "future-platform"}
    assert before["Android"].installed is True
    assert before["WebGL"].installed is False and not before["WebGL"].supported
    assert before["future-platform"].installed is None and not before["future-platform"].supported
    assert "StandaloneWindows64" not in before  # No invented fixed menu items.
    (engines / "WebGLSupport").mkdir()
    write_modules(metadata, "android", "webgl", "future-platform", "visionos")
    after = {item.identifier: item for item in inspect_installation(editor).targets}
    assert after["WebGL"].installed is True and "VisionOS" in after


def test_missing_or_broken_hub_metadata_uses_actual_folders_only(tmp_path):
    editor = tmp_path / "Editor/Unity"
    metadata, engines = installation_paths(editor)
    (engines / "MysterySupport").mkdir(parents=True)
    (engines / "windowsstandalonesupport/Variations/win64_player_mono").mkdir(parents=True)
    metadata.write_text("broken")
    result = inspect_installation(editor)
    assert result.note and {item.identifier for item in result.targets} == {"StandaloneWindows64", "mysterysupport"}
    assert next(item for item in result.targets if item.identifier == "StandaloneWindows64").installed is True


def test_shared_desktop_folder_does_not_imply_server_or_il2cpp_installed(tmp_path):
    editor = tmp_path / "Editor/Unity.exe"
    metadata, engines = installation_paths(editor)
    (engines / "windowsstandalonesupport/Variations/win64_player_mono").mkdir(parents=True)
    write_modules(metadata, "windows-il2cpp", "windows-server")
    targets = {item.identifier: item for item in inspect_installation(editor).targets}
    assert targets["StandaloneWindows64"].installed is True  # Bundled Mono.
    assert targets["StandaloneWindows64:Server"].installed is False


def test_no_compatible_editor_does_not_invent_a_catalog(tmp_path):
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.17f1\n")
    with patch("unitybuild.targets.resolve_unity_editor", side_effect=FileNotFoundError("Missing editor")):
        result = discover_targets(tmp_path)
    assert not result.targets and result.editor is None and "Missing editor" in result.note


def test_menu_uses_dynamic_names_blocks_unsupported_and_refreshes(tmp_path):
    menu = Menu(tmp_path, console=Console(file=io.StringIO()))
    catalog = TargetCatalog(
        "2022.3.17f1",
        None,
        (
            Target("WebGL", "Web from Unity catalog", True, False, "test"),
            Target("Android", "Android from Unity catalog", False, True, "test"),
            Target("StandaloneWindows64", "Windows from Unity catalog", True, True, "test"),
        ),
    )
    with (
        patch("unitybuild.targets.discover_targets", return_value=catalog) as discover,
        patch.object(menu, "choose", side_effect=[0, 1, 3, 2]) as choose,
    ):
        assert choose_target(menu) == "StandaloneWindows64"
    assert discover.call_count == 4
    labels = choose.call_args.args[1]
    assert "Web from Unity catalog" in labels[0] and "unsupported" in labels[0]
    assert "Module not installed" in labels[1]


def test_config_can_save_missing_module_but_build_check_rejects_it(tmp_path):
    editor = tmp_path / "Editor/Unity.exe"
    metadata, engines = installation_paths(editor)
    engines.mkdir(parents=True)
    write_modules(metadata, "android")
    menu = Menu(tmp_path, console=Console(file=io.StringIO()))
    with (
        patch("unitybuild.targets.discover_targets", return_value=inspect_installation(editor)),
        patch.object(menu, "choose", return_value=0),
    ):
        assert choose_target(menu, configuration_only=True) == "Android"
    with pytest.raises(ProjectError, match="not installed"):
        check_target_module(editor, "Android")

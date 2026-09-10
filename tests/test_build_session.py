from __future__ import annotations

from unittest.mock import Mock, patch

import psutil
import pytest

from unitybuild.build_session import (
    BuildSession,
    BuildSessionError,
    EditorProcess,
    discover_editors,
    select_build_session,
)


def test_discovery_matches_projects_and_ignores_unreadable_non_editors(tmp_path):
    editor = Mock(pid=42)
    editor.name.return_value = "Unity.exe"
    editor.cmdline.return_value = ["Unity.exe", "-projectPath", str(tmp_path)]
    editor.exe.return_value = "Unity.exe"
    inaccessible = Mock(pid=99)
    inaccessible.name.side_effect = psutil.AccessDenied(99)
    exited = Mock(pid=100)
    exited.name.side_effect = psutil.NoSuchProcess(100)
    with patch("unitybuild.build_session.psutil.process_iter", return_value=[editor, inaccessible, exited]):
        assert discover_editors() == [EditorProcess(42, "Unity.exe", tmp_path.resolve())]


def test_import_workers_do_not_hide_the_open_editor_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("UNITYBUILD_MENU", "1")
    processes = []
    for pid, extra in (
        (52344, []),
        (46812, ["-batchMode", "-name", "AssetImportWorker2", "-name", "AssetImport"]),
        (55084, ["-batchMode", "-NAME", "AssetImportWorker4"]),
    ):
        process = Mock(pid=pid)
        process.name.return_value = "Unity.exe"
        process.exe.return_value = "Unity.exe"
        process.cmdline.return_value = ["Unity.exe", "-projectPath", str(tmp_path), *extra]
        processes.append(process)
    with (
        patch("unitybuild.build_session.psutil.process_iter", side_effect=lambda: iter(processes)),
        patch("unitybuild.build_session._choose", return_value="editor") as choose,
    ):
        assert select_build_session(tmp_path, BuildSession.AUTO)
    assert choose.call_args.args[1][0] == ("Use open Editor (PID 52344)", "editor")


@pytest.mark.parametrize("flag", ["-batchmode", "-batchMode", "-BATCHMODE"])
def test_real_background_build_stays_visible_and_cannot_be_reused(tmp_path, flag):
    process = Mock(pid=42)
    process.name.return_value = "Unity.exe"
    process.exe.return_value = "Unity.exe"
    process.cmdline.return_value = ["Unity.exe", flag, "-projectPath", str(tmp_path)]
    with patch("unitybuild.build_session.psutil.process_iter", side_effect=lambda: iter([process])):
        assert discover_editors() == [EditorProcess(42, "Unity.exe", tmp_path.resolve(), batch=True)]
        with pytest.raises(BuildSessionError, match="No single running editor"):
            select_build_session(tmp_path, BuildSession.EDITOR)
        with pytest.raises(BuildSessionError, match="project is in use"):
            select_build_session(tmp_path, BuildSession.BACKGROUND)


@pytest.mark.parametrize("mode", [BuildSession.EDITOR, BuildSession.BACKGROUND])
def test_explicit_modes_do_not_prompt(tmp_path, mode):
    editors = [EditorProcess(42, "Unity.exe", tmp_path)] if mode == BuildSession.EDITOR else []
    with (
        patch("unitybuild.build_session.discover_editors", return_value=editors),
        patch("unitybuild.build_session._choose") as choose,
    ):
        assert select_build_session(tmp_path, mode) == (mode == BuildSession.EDITOR)
    choose.assert_not_called()


def test_other_project_cannot_be_reused(tmp_path):
    with patch(
        "unitybuild.build_session.discover_editors",
        return_value=[EditorProcess(42, "Unity.exe", tmp_path / "other")],
    ):
        with pytest.raises(BuildSessionError, match="No single running editor"):
            select_build_session(tmp_path, BuildSession.EDITOR)
        assert not select_build_session(tmp_path, BuildSession.BACKGROUND)


def test_background_refuses_unresolved_lock(tmp_path):
    (tmp_path / "Temp").mkdir()
    (tmp_path / "Temp/UnityLockfile").touch()
    with (
        patch("unitybuild.build_session.discover_editors", return_value=[]),
        patch("unitybuild.build_session.project_lock_is_held", return_value=True),
        pytest.raises(BuildSessionError, match="project lock"),
    ):
        select_build_session(tmp_path, BuildSession.BACKGROUND)


def test_interactive_background_rechecks_after_manual_editor_close(tmp_path, monkeypatch):
    monkeypatch.setenv("UNITYBUILD_MENU", "1")
    editor = EditorProcess(42, "Unity.exe", tmp_path)
    with (
        patch("unitybuild.build_session.discover_editors", side_effect=[[editor], [editor], [], []]),
        patch("unitybuild.build_session._choose", side_effect=["background", "refresh", "background"]) as choose,
    ):
        assert not select_build_session(tmp_path, BuildSession.AUTO)
    assert "PID 42" in choose.call_args_list[0].args[1][0][0]
    assert choose.call_args_list[1].args[1] == [("Check again after closing the editor", "refresh")]


def test_editor_exits_during_selection(tmp_path, monkeypatch):
    monkeypatch.setenv("UNITYBUILD_MENU", "1")
    with (
        patch(
            "unitybuild.build_session.discover_editors", side_effect=[[EditorProcess(42, "Unity", tmp_path)], []]
        ),
        patch("unitybuild.build_session._choose", return_value="editor"),
        pytest.raises(BuildSessionError),
    ):
        select_build_session(tmp_path, BuildSession.AUTO)

"""Select the Unity session without closing editors or modifying their projects."""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import psutil
import questionary
import typer
from rich.console import Console
from rich.table import Table


class BuildSession(StrEnum):
    AUTO = "auto"
    EDITOR = "editor"
    BACKGROUND = "background"


class BuildSessionError(ValueError):
    pass


@dataclass(frozen=True)
class EditorProcess:
    pid: int
    executable: str
    project: Path | None
    batch: bool = False


def discover_editors() -> list[EditorProcess]:
    editors = []
    for process in psutil.process_iter():
        is_editor = False
        try:
            if process.name().lower() not in {"unity", "unity.exe"}:
                continue
            is_editor = True
            arguments = process.cmdline()
            options = [argument.lower() for argument in arguments]
            # Import workers also run Unity.exe with the same project path.
            # They are not independent editor sessions or build candidates.
            if any(
                option == "-name"
                and (options[index + 1] == "assetimport" or options[index + 1].startswith("assetimportworker"))
                for index, option in enumerate(options[:-1])
            ):
                continue
            project = None
            for index, argument in enumerate(arguments[:-1]):
                if argument.lower() == "-projectpath":
                    project = Path(arguments[index + 1])
                    if not project.is_absolute():
                        project = Path(process.cwd()) / project
                    project = project.resolve()
                    break
            editors.append(EditorProcess(process.pid, process.exe(), project, "-batchmode" in options))
        except psutil.AccessDenied:
            if is_editor:
                editors.append(EditorProcess(process.pid, "Unavailable (access denied)", None))
        except (psutil.NoSuchProcess, OSError):
            continue
    return sorted(editors, key=lambda editor: editor.pid)


def project_editors(project: Path, editors: list[EditorProcess]) -> list[EditorProcess]:
    return [editor for editor in editors if editor.project == project.resolve()]


def project_lock_is_held(project: Path) -> bool:
    """Distinguish a live OS file lock from a file left by a killed build.

    Never delete, create or truncate the file. Unknown access/locking errors
    remain blocking. Live editor detection is a separate check on every OS.
    """
    path = project / "Temp" / "UnityLockfile"
    if os.name != "nt":
        import fcntl

        try:
            with path.open("r+b") as stream:
                # Linux keeps BSD flock and POSIX record locks separate; check
                # both. On macOS these APIs are also available. Closing this
                # descriptor releases the probe locks, including on failure.
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.lockf(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except FileNotFoundError:
            return False
        except OSError:
            return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    # GENERIC_READ | GENERIC_WRITE, no sharing, OPEN_EXISTING.
    handle = create_file(str(path), 0xC0000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        return ctypes.get_last_error() not in (2, 3)  # Missing file or directory.
    kernel32.CloseHandle(handle)
    return False


def show_editors(console: Console, project: Path, editors: list[EditorProcess]) -> None:
    console.print(f"Build project: {project}", markup=False)
    table = Table(title="Unity Editor processes", border_style="cyan")
    for heading in ("PID", "Project", "Mode", "Unity executable"):
        table.add_column(heading, overflow="fold")
    for editor in editors:
        label = str(editor.project) if editor.project else "Unknown (cannot verify project)"
        if editor.project == project.resolve():
            label += " (this project)"
        table.add_row(str(editor.pid), label, "Background" if editor.batch else "Editor", editor.executable)
    if editors:
        console.print(table)
    else:
        console.print("No running Unity Editor processes found.", style="dim")
    if not project_editors(project, editors):
        console.print("No running editor was identified for this project.", style="yellow")


def _choose(console: Console, choices: list[tuple[str, str]]) -> str | None:
    if os.environ.get("UNITYBUILD_PLAIN") == "1" or not console.is_terminal:
        console.print("Choose how to build", style="bold")
        for index, (label, _) in enumerate(choices, 1):
            console.print(f"  {index}. {label}", markup=False)
        console.print("  0. Cancel")
        while True:
            try:
                value = input("Number > ").strip()
            except (EOFError, KeyboardInterrupt):
                return None
            if value in {"0", "q"}:
                return None
            if value.isdigit() and 1 <= int(value) <= len(choices):
                return choices[int(value) - 1][1]
    return questionary.select(
        "Choose how to build",
        choices=[questionary.Choice(label, value=value) for label, value in choices]
        + [questionary.Choice("Cancel", value="cancel")],
    ).ask()


def select_build_session(
    project: Path, mode: BuildSession, *, dry_run: bool = False, interactive: bool | None = None
) -> bool:
    """Return whether to reuse the editor; preview and explicit options never prompt."""
    if dry_run:
        return mode == BuildSession.EDITOR
    interactive = mode == BuildSession.AUTO and (
        interactive
        if interactive is not None
        else (os.environ.get("UNITYBUILD_MENU") == "1" or (sys.stdin.isatty() and sys.stdout.isatty()))
    )
    console = Console(highlight=False)
    background_requested = mode == BuildSession.BACKGROUND
    while True:
        editors = discover_editors()
        matching = project_editors(project, editors)
        reusable = len(matching) == 1 and not matching[0].batch
        locked = project_lock_is_held(project)
        busy = bool(matching) or locked
        if interactive:
            show_editors(console, project, editors)
            if locked and not matching:
                console.print("A Unity project lock exists, but its editor could not be identified.", style="yellow")
            choices = []
            if reusable and not background_requested:
                choices.append((f"Use open Editor (PID {matching[0].pid})", "editor"))
            if background_requested and busy:
                console.print("Save your work and close this project's editor, then check again.", style="yellow")
                choices.append(("Check again after closing the editor", "refresh"))
            else:
                label = "Build in background"
                if busy:
                    label += " (close this project's editor first)"
                choices.append((label, "background"))
                choices.append(("Refresh editor processes", "refresh"))
            selected = _choose(console, choices)
            if selected in (None, "cancel"):
                console.print("Build cancelled. No build was started.", style="dim")
                raise typer.Exit(0)
            if selected == "refresh":
                continue
            if selected == "background" and busy:
                background_requested = True
                continue
            mode = BuildSession(selected)
            # Recheck after user input; an editor may have opened or exited meanwhile.
            interactive = False
            continue
        if mode == BuildSession.EDITOR:
            if not reusable:
                raise BuildSessionError(
                    "No single running editor is available for this project. Refresh or use background mode."
                )
            return True
        if mode == BuildSession.AUTO and reusable:
            return True
        if busy:
            raise BuildSessionError(
                "The Unity project is in use or has an unresolved project lock. "
                "Save and close its editor before starting a background build."
            )
        return False

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import cast


class UnityVersionError(ValueError):
    pass


_VERSION_TOKEN = r"\d+\.\d+\.\d+[abfp]\d+(?:c\d+)?"
_VERSION_PATTERN = re.compile(rf"(?<![0-9A-Za-z.])({_VERSION_TOKEN})(?![0-9A-Za-z.])")
_PROJECT_VERSION_PATTERN = re.compile(rf"(?m)^m_EditorVersion:\s*({_VERSION_TOKEN})\s*$")


def read_project_version(version_file: Path) -> str:
    try:
        text = version_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise UnityVersionError(f"Unity version file could not be read: {version_file}") from exc

    match = _PROJECT_VERSION_PATTERN.search(text)
    if not match:
        raise UnityVersionError(f"A valid m_EditorVersion is missing from {version_file}")
    return match.group(1)


def probe_unity_editor_version(editor: Path) -> str:
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [str(editor), "-version"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=30,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        raise UnityVersionError("`Unity -version` timed out after 30 seconds") from exc
    except OSError as exc:
        raise UnityVersionError(f"`Unity -version` could not be started: {exc}") from exc

    if result.returncode != 0:
        raise UnityVersionError(f"`Unity -version` exited with code {result.returncode}")

    output = "\n".join(value for value in (result.stdout, result.stderr) if value)
    match = _VERSION_PATTERN.search(output)
    if not match:
        raise UnityVersionError("`Unity -version` did not report a recognizable editor version")
    return match.group(1)


def require_unity_editor_version(
    editor: Path,
    required_version: str,
    *,
    version_file: Path,
    source: str,
) -> str:
    try:
        actual_version = probe_unity_editor_version(editor)
    except UnityVersionError as exc:
        raise UnityVersionError(
            "\n".join(
                [
                    f"Unity Editor version could not be verified ({source}).",
                    f"  editor: {editor}",
                    f"  project requires: {required_version} (from {version_file})",
                    f"  reason: {exc}",
                    "No Unity project was opened. Choose an editor that reports the required version.",
                ]
            )
        ) from exc

    if actual_version != required_version:
        raise UnityVersionError(
            "\n".join(
                [
                    f"Unity Editor version mismatch ({source}).",
                    f"  editor: {editor}",
                    f"  editor reports: {actual_version}",
                    f"  project requires: {required_version} (from {version_file})",
                    "No Unity project was opened. Choose the required editor version and try again.",
                ]
            )
        )
    return actual_version


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _windows_drive_roots() -> list[Path]:
    list_drives = cast(Callable[[], list[str]] | None, getattr(os, "listdrives", None))
    if list_drives is not None:
        return [Path(drive) for drive in list_drives()]
    return _unique_paths(Path(value) for value in (os.environ.get("SystemDrive", ""), Path.cwd().anchor) if value)


def _windows_unity_editor_roots(drive_roots: Iterable[Path] | None = None) -> list[Path]:
    roots: list[Path] = []
    app_data = os.environ.get("APPDATA", "")
    if app_data:
        config = Path(app_data) / "UnityHub" / "secondaryInstallPath.json"
        try:
            secondary = json.loads(config.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            secondary = ""
        if isinstance(secondary, str) and secondary.strip():
            roots.append(Path(secondary).expanduser())

    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        program_files = os.environ.get(env_name, "")
        if program_files:
            roots.append(Path(program_files) / "Unity" / "Hub" / "Editor")

    drives = list(drive_roots) if drive_roots is not None else _windows_drive_roots()
    for drive in drives:
        roots.extend(
            [
                drive / "Program Files" / "Unity" / "Hub" / "Editor",
                drive / "Program Files (x86)" / "Unity" / "Hub" / "Editor",
                drive / "Unity" / "Hub" / "Editor",
            ]
        )
    return _unique_paths(roots)


def hub_unity_candidates(required_version: str) -> list[Path]:
    if os.name == "nt":
        return [root / required_version / "Editor" / "Unity.exe" for root in _windows_unity_editor_roots()]
    if sys.platform == "darwin":
        return [
            Path("/Applications/Unity/Hub/Editor") / required_version / "Unity.app" / "Contents" / "MacOS" / "Unity"
        ]
    return [
        root / required_version / "Editor" / "Unity"
        for root in (
            Path.home() / "Unity" / "Hub" / "Editor",
            Path("/opt/Unity/Hub/Editor"),
            Path("/opt/unity/Hub/Editor"),
        )
    ] + [
        path
        for path in (
            Path("/opt/unity/editor/Unity"),
            Path("/opt/Unity/Editor/Unity"),
            Path("/usr/bin/unity-editor"),
        )
    ]

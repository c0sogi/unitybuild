"""Exact-version Editor resolution and low-level batch execution."""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

import psutil
from rich.console import Console

from .progress import run_logged_process
from .unity import UnityVersionError, hub_unity_candidates, read_project_version, require_unity_editor_version


def resolve_unity_editor(
    project: Path,
    requested: str = "",
    *,
    candidates: Callable[[str], Iterable[tuple[str, Path]]] | None = None,
) -> Path:
    version_file = project / "ProjectSettings/ProjectVersion.txt"
    version = read_project_version(version_file)
    explicit = requested.strip()
    configured = os.environ.get("UNITY_EDITOR_PATH", "").strip()
    if explicit or (configured and Path(configured).expanduser().is_file()):
        path = Path(explicit or configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError(
                f"The --unity-path value does not point to a Unity Editor file: {path}. No Unity project was opened."
            )
        require_unity_editor_version(
            path, version, version_file=version_file, source="--unity-path" if explicit else "UNITY_EDITOR_PATH"
        )
        return path.resolve()

    def default_candidates(version):
        found = shutil.which("Unity") or shutil.which("Unity.exe")
        return ([("PATH", Path(found))] if found else []) + [("Unity Hub", p) for p in hub_unity_candidates(version)]

    candidates = candidates or default_candidates
    rejected = []
    seen = set()
    for source, path in candidates(version):
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            require_unity_editor_version(path, version, version_file=version_file, source=source)
        except UnityVersionError as exc:
            rejected.append(str(exc))
            continue
        return path
    raise FileNotFoundError(
        f"No compatible Unity {version} Editor found for {project}. Pass --unity-path.\n" + "\n".join(rejected)
    )


def run_unity_batch(
    project: Path,
    editor: str | Path,
    *,
    extra_args: list[str],
    log_path: Path,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    label: str = "Unity build",
    console: Console | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an already resolved Editor; callers interpret operation-specific success markers."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.unlink(missing_ok=True)
    command = [
        str(editor),
        "-batchmode",
        "-nographics",
        "-projectPath",
        str(project),
        *extra_args,
        "-logFile",
        str(log_path),
        "-quit",
    ]
    environment = (os.environ if env is None else env).copy()
    environment = prepare_unity_environment(environment)
    return run_logged_process(
        command, cwd=cwd or project, env=environment, label=label, tail_paths=(log_path,), console=console
    )


def windows_package_family() -> str | None:
    """Find an MSIX host, including unpackaged CLI children of a packaged app."""
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_family = kernel32.GetPackageFamilyName
    get_family.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p]
    get_family.restype = ctypes.c_long
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        processes = [psutil.Process(), *psutil.Process().parents()]
    except psutil.Error:
        return None
    for process in processes:
        handle = kernel32.OpenProcess(0x1000, False, process.pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            size = ctypes.c_uint32()
            if get_family(handle, ctypes.byref(size), None) != 122:  # ERROR_INSUFFICIENT_BUFFER
                continue
            buffer = ctypes.create_unicode_buffer(size.value)
            if get_family(handle, ctypes.byref(size), buffer) == 0:
                return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    return None


def prepare_unity_environment(environment: dict[str, str]) -> dict[str, str]:
    result = environment.copy()
    if os.name == "nt" and not result.get("ALLUSERSPROFILE"):
        result["ALLUSERSPROFILE"] = result.get("ProgramData") or r"C:\ProgramData"
    family = windows_package_family()
    local = result.get("LOCALAPPDATA")
    if not family or not local:
        return result

    # Packaged desktop apps virtualize ordinary LocalAppData paths. Java's
    # AF_UNIX bind/connect can disagree about that path (WSAEINVAL). TempState
    # is the package's real temporary folder and is excluded from redirection.
    # Preserve custom temporary paths outside the virtualized LocalAppData tree.
    local_path = Path(local).resolve()
    packages = local_path / "Packages"
    package_temp = packages / family / "TempState"
    for name in ("TEMP", "TMP"):
        value = result.get(name)
        if not value:
            continue
        path = Path(value).resolve()
        if path.is_relative_to(local_path) and not path.is_relative_to(packages):
            package_temp.mkdir(parents=True, exist_ok=True)
            result[name] = str(package_temp)
    return result

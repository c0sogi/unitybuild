"""Build a configured Unity project with an explicit configuration."""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from unitybuild.build_session import BuildSession, select_build_session
from unitybuild.profiles import BuildProfile
from unitybuild.progress import run_quiet_process
from unitybuild.projects import Project, ProjectError
from unitybuild.unity import read_project_version

from .execution import prepare_unity_environment, resolve_unity_editor
from .history import failure_reason
from .platforms import PLATFORMS, default_output, supports_build
from .profiles import SavedBuildProfile, resolve_build_profile
from .scenes import enabled_scenes
from .targets import check_target_module

BUILDER_METHOD = "UnityBuild.Builder.Build"
BUILDER_PATH = "Assets/Editor/UnityBuild/Builder.cs"


def ensure_builder(project: Path) -> bool:
    """Create a missing helper; preserve an existing project-owned implementation."""
    path = project / BUILDER_PATH
    if not path.resolve().is_relative_to(project.resolve()):
        raise ProjectError(f"Unity builder path points outside the selected project: {path}")
    if path.is_file():
        return False
    source = files("unitybuild").joinpath("templates/Builder.cs").read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(source)
    except FileExistsError:
        if not path.is_file():
            raise ProjectError(f"Unity builder path is not a file: {path}")
        return False
    return True


def resolve_editor(project: Project, requested: str = "") -> Path:
    try:
        return resolve_unity_editor(project.root, requested)
    except OSError as exc:
        raise ProjectError(str(exc)) from exc


def output_path(project: Project, profile: BuildProfile | SavedBuildProfile) -> Path:
    if not project.platform:
        raise ProjectError(
            "No build platform selected. Choose a build target in the menu, use build --platform PLATFORM, "
            "or save build.platform in unitybuild.toml. Release/Debug does not select a platform."
        )
    if not supports_build(project.platform):
        raise ProjectError(f"Unsupported build platform: {project.platform}")
    return resolve_output_path(
        project.unity_project, project.output or default_output(project.platform),
        development=profile.configuration == "development",
    )


def resolve_output_path(root: Path, configured: str, *, development: bool = False) -> Path:
    """Validate an explicit output independently of target selection."""
    output = Path(configured)
    if not output.is_absolute():
        output = root / output
    if development:
        output = output.parent / "Debug" / output.name
    output = output.resolve()
    # Build output must never be a project root or a source directory.
    root = root.resolve()
    if (
        output == root
        or output in root.parents
        or any(
            output == root / name or root / name in output.parents
            for name in ("Assets", "Packages", "ProjectSettings", "Library")
        )
    ):
        raise ProjectError(f"Build output overlaps the Unity project or its source/cache directories: {output}")
    return output


@dataclass(frozen=True)
class BuildResult:
    output: Path
    log: Path | None
    record: Path | None
    dry_run: bool


@dataclass(frozen=True)
class BuildPlan:
    project: Project
    profile: BuildProfile | SavedBuildProfile
    version: str
    output: Path
    command: tuple[str, ...]
    dry_run: bool
    setup_required: bool
    scenes: tuple[str, ...] | None = None
    scene_error: str = ""


def build_project(
    project: Project,
    profile: BuildProfile | SavedBuildProfile = BuildProfile.RELEASE,
    *,
    unity_path: str = "",
    dry_run: bool = False,
    emit: Callable[[str], None] = lambda message: None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    environment: dict[str, str] | None = None,
    logs_directory: str = "Logs/unitybuild",
    on_plan: Callable[[BuildPlan], None] | None = None,
) -> BuildResult:
    """Build without prompts. Callers own presentation; failures raise ProjectError."""
    root = project.unity_project
    version = read_project_version(root / "ProjectSettings/ProjectVersion.txt")
    profile = resolve_build_profile(
        project, profile.asset_path if isinstance(profile, SavedBuildProfile) else profile.value
    )
    native = isinstance(profile, SavedBuildProfile)
    if isinstance(profile, SavedBuildProfile):
        if tuple(int(part) for part in version.split(".")[:2]) < (6000, 3):
            raise ProjectError("Native Build Profile execution currently requires Unity 6.3 or newer.")
        target_output = (
            project.output
            if project.output and project.platform in ("", profile.platform)
            else default_output(profile.platform)
        )
        target_output = PLATFORMS[profile.platform].native_output(
            target_output, app_bundle=profile.app_bundle, export_project=profile.export_project
        )
        project = replace(project, platform=profile.platform, output=target_output)
    output = output_path(project, profile)
    # A plan can be inspected on a machine without Unity; real builds verify the exact editor version.
    scenes = None
    scene_error = ""
    if not native and project.build_method == BUILDER_METHOD:
        try:
            scenes = enabled_scenes(root)
        except ProjectError as exc:
            scenes, scene_error = (), str(exc)
    editor = (unity_path or f"<Unity {version}>") if dry_run or scene_error else str(resolve_editor(project, unity_path))
    command = [
        editor,
        "-batchmode",
        "-nographics",
        "-projectPath",
        str(root),
    ]
    if isinstance(profile, SavedBuildProfile):
        command += ["-activeBuildProfile", profile.asset_path, "-build", str(output)]
    else:
        command += ["-buildTarget", project.platform, "-executeMethod", project.build_method]
    command += ["-quit"]
    setup_required = not native and project.build_method == BUILDER_METHOD and not (root / BUILDER_PATH).is_file()
    if on_plan is not None:
        on_plan(BuildPlan(project, profile, version, output, tuple(command), dry_run, setup_required, scenes, scene_error))
    emit(f"Project: {project.name}\nPath: {root}\nUnity: {version}\nProfile: {profile.value}\nOutput: {output}")
    emit(f"Command: {subprocess.list2cmdline(command)}")
    if dry_run:
        emit("[DRY-RUN] Plan only; Unity availability and compilation were not checked. No files were written.")
        if setup_required:
            emit("The bundled Unity builder will be prepared automatically when building.")
        if scene_error:
            emit(scene_error)
        return BuildResult(output, None, None, True)
    if scene_error:
        raise ProjectError(scene_error)
    select_build_session(root, BuildSession.BACKGROUND, interactive=False)
    check_target_module(Path(editor), project.platform)
    if not native and project.build_method == BUILDER_METHOD:
        ensure_builder(root)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    logs = root / logs_directory
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{run_id}.log"
    record = logs / f"{run_id}.json"
    command += ["-logFile", "-"]
    environment = os.environ.copy() | (environment or {})
    # Native profiles receive the asset and output through Unity's CLI. Only
    # script builders need the Python-to-C# environment contract.
    if not native:
        environment[project.output_environment] = str(output)
        environment["UNITYBUILD_PROFILE"] = profile.value
        environment["UNITYBUILD_PLATFORM"] = project.platform
        if project.configuration_environment:
            environment[project.configuration_environment] = profile.configuration
    environment = prepare_unity_environment(environment)
    before = artifact_signature(output)
    state = {
        "project": str(root),
        "profile": profile.value,
        "platform": project.platform,
        "unity": version,
        "profile_asset": profile.asset_path if isinstance(profile, SavedBuildProfile) else None,
        "output": str(output),
        "log": str(log),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "failed",
    }
    try:
        result = (runner or run_quiet_process)(
            command, cwd=root, env=environment, label=f"Build {project.name}", log_path=log
        )
        if result.returncode:
            reason = failure_reason(result.stdout or "")
            if reason:
                state["failure_reason"] = reason
            raise ProjectError(f"{reason or f'Unity build exited with code {result.returncode}.'} See {log}")
        # Unity 6000.3's native CLI can return zero after BuildPlayer reports
        # failure. Reject known build failures even if a partial output changed.
        if re.search(
            r"^(?:BuildFailedException:|CommandInvokationFailure:|FAILURE: Build failed|"
            r"Error building Player|Build completed with a result of '(?:Failed|Cancelled)')",
            result.stdout or "",
            re.MULTILINE,
        ):
            raise ProjectError(f"Unity reported a build failure despite exit code 0. See {log}")
        after = artifact_signature(output)
        reported_success = re.search(
            r"^(?:Build Finished, Result: Success\.|Build completed with a result of 'Succeeded')",
            result.stdout or "", re.MULTILINE,
        )
        if not after or (after == before and not reported_success):
            raise ProjectError(f"Unity exited successfully but no new or updated build output was found: {output}")
        state["output_reused"] = after == before
        state["status"] = "succeeded"
    except BaseException as exc:
        state["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
        state["error"] = str(exc)
        raise
    finally:
        state["finished_at"] = datetime.now(UTC).isoformat()
        record.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    emit(f"[OK] Build completed: {output}\nBuild record: {record}")
    return BuildResult(output, log, record, False)


def artifact_signature(path: Path) -> list[tuple[str, int, int]]:
    files = [path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []
    if path.is_file():
        # Desktop players can reuse the launcher executable unchanged while
        # rebuilding scenes and scripts in the adjacent <player>_Data folder.
        data = path.with_name(path.stem + "_Data")
        if data.is_dir():
            files += sorted(data.rglob("*"))
    return [
        (str(item), item.stat().st_size, item.stat().st_mtime_ns)
        for item in files
        if item.is_file() and item.stat().st_size > 0
    ]

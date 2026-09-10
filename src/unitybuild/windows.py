"""Windows UWP compilation and loose deployment with explicit inputs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .progress import run_quiet_process as run_logged_process


@dataclass
class WindowsBuildOptions:
    solution: str = ""
    build_path: str = ""
    msbuild_path: str = ""
    log: str = ""
    configuration: str = "Release"
    platform: str = "ARM64"
    target: str = "Build"
    generate_appx_package: bool = False
    sdk_version: str = ""
    winappdeploy_path: str = ""
    remote_dir: str = ""
    delete_extra_files: bool = False
    device_ip: str = ""
    pin: str = ""
    connect_timeout: int = 0
    register: bool = False
    dry_run: bool = False
    timeout_seconds: int = 10


@dataclass(frozen=True)
class MsBuildResult:
    status: str
    solution: str
    msbuild: str
    configuration: str
    platform: str
    log: str
    detail: str
    output_exe: str = ""


@dataclass(frozen=True)
class WinDeployResult:
    status: str
    command: str
    device_ip: str
    manifest: str
    remote_dir: str
    winappdeploycmd: str
    log: str
    detail: str


@dataclass(frozen=True)
class WinDevicesResult:
    status: str
    timeout_seconds: int
    winappdeploycmd: str
    log: str
    detail: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace") if path.exists() else ""


def default_log_path(project: Path, stem: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return project / "Logs" / f"{stem}-{stamp}.log"


def visual_studio_roots() -> list[Path]:
    roots: list[Path] = []
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    for major in ("2022", "18"):
        base = Path(program_files) / "Microsoft Visual Studio" / major
        for edition in ("Community", "Professional", "Enterprise", "Preview", "BuildTools"):
            roots.append(base / edition)
    return roots


def resolve_msbuild(msbuild_path: str) -> Path:
    candidates: list[Path] = []
    if msbuild_path:
        candidates.append(Path(msbuild_path))
    env_path = os.environ.get("MSBUILD_PATH", "")
    if env_path:
        candidates.append(Path(env_path))
    for root in visual_studio_roots():
        candidates.append(root / "MSBuild" / "Current" / "Bin" / "MSBuild.exe")

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("MSBuild.exe was not found. Pass --msbuild-path or set MSBUILD_PATH.")


def windows_kits_root() -> Path:
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return Path(program_files_x86) / "Windows Kits" / "10"


def sdk_version_key(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split("."):
        if not piece.isdigit():
            return (0,)
        parts.append(int(piece))
    return tuple(parts)


def list_installed_sdk_versions(path: Path) -> list[str]:
    if not path.exists():
        return []
    return sorted((child.name for child in path.iterdir() if child.is_dir()), key=sdk_version_key, reverse=True)


def installed_uap_sdks() -> list[str]:
    return list_installed_sdk_versions(windows_kits_root() / "Platforms" / "UAP")


def installed_windows_mobile_sdks() -> list[str]:
    return list_installed_sdk_versions(windows_kits_root() / "Extension SDKs" / "WindowsMobile")


def choose_windows_mobile_sdk(preferred: str = "") -> str:
    uap_versions = set(installed_uap_sdks())
    mobile_versions = set(installed_windows_mobile_sdks())
    if preferred and preferred in uap_versions and preferred in mobile_versions:
        return preferred

    common_versions = sorted(uap_versions & mobile_versions, key=sdk_version_key, reverse=True)
    return common_versions[0] if common_versions else ""


def normalize_generated_windows_mobile_sdk(build_path: Path) -> str:
    preferred = read_target_platform_version(build_path)
    sdk_version = choose_windows_mobile_sdk(preferred)

    changed: list[str] = []
    for project_file in sorted(build_path.glob("*/*.vcxproj")):
        text = project_file.read_text(encoding="utf-8", errors="replace")
        if "WindowsMobile, Version=" not in text:
            continue

        if sdk_version:
            updated = re.sub(
                r"<WindowsTargetPlatformVersion>[^<]+</WindowsTargetPlatformVersion>",
                f"<WindowsTargetPlatformVersion>{sdk_version}</WindowsTargetPlatformVersion>",
                text,
            )
            updated = re.sub(r'(WindowsMobile, Version=)[^"]+', rf"\g<1>{sdk_version}", updated)
        else:
            updated = re.sub(
                r"^\s*<SDKReference Include=\"WindowsMobile, Version=[^\"]+\"\s*/>\r?\n?", "", text, flags=re.MULTILINE
            )
        if updated != text:
            project_file.write_text(updated, encoding="utf-8")
            changed.append(str(project_file.relative_to(build_path)))

    if changed:
        if sdk_version:
            return f"normalized WindowsMobile SDK to {sdk_version} in {', '.join(changed)}"
        return f"removed unavailable WindowsMobile SDK reference in {', '.join(changed)}"
    return ""


def resolve_winappdeploy(winappdeploy_path: str, sdk_version: str = "") -> Path:
    candidates: list[Path] = []
    if winappdeploy_path:
        candidates.append(Path(winappdeploy_path))
    env_path = os.environ.get("WINAPPDEPLOYCMD_PATH", "")
    if env_path:
        candidates.append(Path(env_path))

    bin_root = windows_kits_root() / "bin"
    architectures = ("x64", "x86") if os.name == "nt" else ("x64",)
    if sdk_version:
        candidates.extend(
            bin_root / sdk_version / architecture / "WinAppDeployCmd.exe" for architecture in architectures
        )
    if bin_root.exists():
        for sdk_dir in sorted(bin_root.iterdir(), reverse=True):
            candidates.extend(sdk_dir / architecture / "WinAppDeployCmd.exe" for architecture in architectures)

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("WinAppDeployCmd.exe was not found. Pass --winappdeploy-path or set WINAPPDEPLOYCMD_PATH.")


def resolve_solution(args: WindowsBuildOptions) -> Path:
    if args.solution:
        solution = Path(args.solution).resolve()
        if solution.exists():
            return solution
        raise FileNotFoundError(f"Solution file was not found: {solution}")

    if not args.build_path:
        raise ValueError("--solution or --build-path is required for MSBuild.")

    build_path = Path(args.build_path).resolve()
    solutions = sorted(build_path.glob("*.sln"))
    if len(solutions) == 1:
        return solutions[0]
    if not solutions:
        raise FileNotFoundError(f"No .sln found under {build_path}")
    raise ValueError(f"Multiple .sln files found under {build_path}; pass --solution explicitly.")


def resolve_build_path(args: WindowsBuildOptions) -> Path:
    if args.build_path:
        build_path = Path(args.build_path).resolve()
        if build_path.exists():
            return build_path
        raise FileNotFoundError(f"Build path was not found: {build_path}")
    if args.solution:
        return Path(args.solution).resolve().parent
    raise ValueError("--build-path or --solution is required.")


def run_msbuild(args: WindowsBuildOptions, *, runner: Callable = run_logged_process) -> MsBuildResult:
    solution = resolve_solution(args)
    sdk_normalization = normalize_generated_windows_mobile_sdk(solution.parent)
    msbuild = resolve_msbuild(args.msbuild_path)
    log = Path(args.log).resolve() if args.log else default_log_path(solution.parent, "msbuild-uwp")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.unlink(missing_ok=True)

    command = [
        str(msbuild),
        str(solution),
        "/nologo",
        "/maxcpucount",
        f"/p:Configuration={args.configuration}",
        f"/p:Platform={args.platform}",
        f"/p:SolutionDir={solution.parent!s}\\",
        f"/t:{args.target}",
        "/clp:Verbosity=minimal",
    ]
    if not args.generate_appx_package:
        command.extend(["/p:GenerateAppxPackageOnBuild=false", "/p:AppxPackage=false"])
    process = runner(command, cwd=solution.parent, label="MSBuild UWP", log_path=log)
    output = read_text(log)

    if process.returncode == 0:
        packaging = (
            "with appx package generation" if args.generate_appx_package else "with appx package generation disabled"
        )
        output_exe = find_output_exe(solution, args.configuration, args.platform)
        detail = f"MSBuild {args.target} succeeded for {solution.name} ({packaging})"
        if sdk_normalization:
            detail = f"{detail}; {sdk_normalization}"
        return MsBuildResult(
            "OK",
            str(solution),
            str(msbuild),
            args.configuration,
            args.platform,
            str(log),
            detail,
            str(output_exe) if output_exe else "",
        )

    failure_lines = [
        line.strip()
        for line in output.splitlines()
        if "error " in line.lower() or "Build FAILED" in line or "빌드하지 못했습니다" in line
    ]
    detail = failure_lines[0] if failure_lines else f"MSBuild exited with code {process.returncode}"
    if sdk_normalization:
        detail = f"{detail}; {sdk_normalization}"
    output_exe = find_output_exe(solution, args.configuration, args.platform)
    return MsBuildResult(
        "FAIL",
        str(solution),
        str(msbuild),
        args.configuration,
        args.platform,
        str(log),
        detail,
        str(output_exe) if output_exe else "",
    )


def find_output_exe(solution: Path, configuration: str, platform: str) -> Path | None:
    output_dir = solution.parent / "build" / "bin" / platform / configuration
    if not output_dir.exists():
        return None
    candidates = sorted(output_dir.glob("*.exe"))
    return candidates[0] if candidates else None


def read_target_platform_version(build_path: Path) -> str:
    project_files = sorted(build_path.glob("*/*.vcxproj"))
    for project_file in project_files:
        text = project_file.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"<WindowsTargetPlatformVersion>([^<]+)</WindowsTargetPlatformVersion>", text)
        if match:
            return match.group(1).strip()
    return ""


def resolve_manifest(build_path: Path, configuration: str, platform: str) -> Path:
    manifests = sorted(build_path.glob("*/Package.appxmanifest"))
    if len(manifests) != 1:
        raise ValueError(f"Expected one UWP package manifest under {build_path}, found {len(manifests)}")
    project_manifest = manifests[0]
    output_dir = build_path / "build" / "bin" / platform / configuration
    output_manifest = output_dir / "AppxManifest.xml"
    if not project_manifest.exists():
        raise FileNotFoundError(f"Package manifest was not found: {project_manifest}")
    if not output_dir.exists():
        raise FileNotFoundError(f"MSBuild output directory was not found: {output_dir}")
    if not output_manifest.exists():
        raise FileNotFoundError(f"MSBuild app manifest was not found: {output_manifest}")

    layout = Path(tempfile.mkdtemp(prefix="LooseDeployLayout-", dir=build_path))
    shutil.copytree(project_manifest.parent, layout, dirs_exist_ok=True)
    shutil.copytree(output_dir, layout, dirs_exist_ok=True)

    manifest_text = output_manifest.read_text(encoding="utf-8", errors="replace")
    manifest_text = manifest_text.replace("$targetnametoken$", project_manifest.parent.name)
    manifest_text = manifest_text.replace('Language="x-generate"', 'Language="en-US"')
    manifest = layout / "AppxManifest.xml"
    manifest.write_text(manifest_text, encoding="utf-8")
    return manifest.resolve()


def default_remote_dir(build_path: Path) -> str:
    return build_path.name + "_LooseDeployLayout"


def build_winappdeploy_command(
    args: WindowsBuildOptions,
    build_path: Path,
    command_name: str,
) -> tuple[list[str], Path, Path, str]:
    manifest = resolve_manifest(build_path, args.configuration, args.platform)
    sdk_version = args.sdk_version or read_target_platform_version(build_path)
    winappdeploy = resolve_winappdeploy(args.winappdeploy_path, sdk_version)
    remote_dir = args.remote_dir.strip() or default_remote_dir(build_path)

    command = [str(winappdeploy), command_name]
    if command_name == "deployfiles":
        command.extend(["-file", str(manifest), "-remotedeploydir", remote_dir])
        if args.delete_extra_files:
            command.append("-deleteextrafiles")
    else:
        command.extend(["-remotedeploydir", remote_dir])
    command.extend(["-ip", args.device_ip])
    if args.pin:
        command.extend(["-pin", args.pin])
    if args.connect_timeout:
        command.extend(["-connecttimeout", str(args.connect_timeout)])
    return command, manifest, winappdeploy, remote_dir


def run_winappdeploy(args: WindowsBuildOptions) -> WinDeployResult:
    if not args.device_ip.strip():
        raise ValueError("--device-ip is required.")
    build_path = resolve_build_path(args)
    log = Path(args.log).resolve() if args.log else default_log_path(build_path, "winappdeploy-loose")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.unlink(missing_ok=True)

    first_command, manifest, winappdeploy, remote_dir = build_winappdeploy_command(args, build_path, "deployfiles")
    commands = [first_command]
    if args.register:
        second_command, _, _, _ = build_winappdeploy_command(args, build_path, "registerfiles")
        commands.append(second_command)

    if args.dry_run:
        lines = [" ".join(command) for command in commands]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return WinDeployResult(
            "OK",
            "dry-run",
            args.device_ip,
            str(manifest),
            remote_dir,
            str(winappdeploy),
            str(log),
            "WinAppDeployCmd loose-file command generated but not executed",
        )

    combined_output: list[str] = []
    for command in commands:
        process = subprocess.run(command, cwd=build_path, capture_output=True, text=True, check=False)
        combined_output.append(f"> {' '.join(command)}")
        if process.stdout:
            combined_output.append(process.stdout)
        if process.stderr:
            combined_output.append(process.stderr)
        if process.returncode != 0:
            log.write_text("\n".join(combined_output), encoding="utf-8")
            combined_text = "\n".join(combined_output)
            if command[1] == "registerfiles" and (
                "0x80073cfb" in combined_text.lower() or "already installed" in combined_text.lower()
            ):
                return WinDeployResult(
                    "OK",
                    "deployfiles",
                    args.device_ip,
                    str(manifest),
                    remote_dir,
                    str(winappdeploy),
                    str(log),
                    "WinAppDeployCmd deployfiles succeeded; registerfiles skipped because package is already registered",
                )
            return WinDeployResult(
                "FAIL",
                command[1],
                args.device_ip,
                str(manifest),
                remote_dir,
                str(winappdeploy),
                str(log),
                f"WinAppDeployCmd {command[1]} exited with code {process.returncode}",
            )

    log.write_text("\n".join(combined_output), encoding="utf-8")
    detail = "WinAppDeployCmd deployfiles succeeded"
    if args.register:
        detail = f"{detail}; registerfiles succeeded"
    return WinDeployResult(
        "OK",
        "deployfiles",
        args.device_ip,
        str(manifest),
        remote_dir,
        str(winappdeploy),
        str(log),
        detail,
    )


def run_winappdeploy_devices(args: WindowsBuildOptions) -> WinDevicesResult:
    winappdeploy = resolve_winappdeploy(args.winappdeploy_path, args.sdk_version)
    log = Path(args.log).resolve() if args.log else default_log_path(Path.cwd(), "winappdeploy-devices")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.unlink(missing_ok=True)
    command = [str(winappdeploy), "devices", str(args.timeout_seconds)]
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    output = process.stdout
    if process.stderr:
        output = f"{output}\n{process.stderr}"
    log.write_text(output, encoding="utf-8")
    if process.returncode != 0:
        return WinDevicesResult(
            "FAIL",
            args.timeout_seconds,
            str(winappdeploy),
            str(log),
            f"WinAppDeployCmd devices exited with code {process.returncode}",
        )

    non_empty_lines = [line.strip() for line in output.splitlines() if line.strip()]
    device_lines = [
        line
        for line in non_empty_lines
        if not line.startswith("Windows App Deployment Tool")
        and not line.startswith("Version ")
        and not line.startswith("Copyright")
        and not line.startswith("Discovering devices")
        and not line.startswith("IP Address")
        and line != "Done."
    ]
    detail = "no discoverable devices" if not device_lines else " | ".join(device_lines)
    return WinDevicesResult("OK", args.timeout_seconds, str(winappdeploy), str(log), detail)

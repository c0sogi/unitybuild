"""Project selection and portable, project-owned build settings."""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .platforms import default_output, supports_build

CONFIG_NAME = "unitybuild.toml"
PROJECT_ENV = "UNITYBUILD_PROJECT"



class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    root: Path
    name: str
    preset: str = "unity"
    platform: str = ""
    build_method: str = "UnityBuild.Builder.Build"
    output: str = ""
    output_environment: str = "UNITYBUILD_OUTPUT"
    configuration_environment: str = ""

    @property
    def unity_project(self) -> Path:
        return self.root


def is_unity_project(path: Path) -> bool:
    """Recognition depends on Unity's files, never a repository or app name."""
    return (path / "Assets").is_dir() and (path / "ProjectSettings/ProjectVersion.txt").is_file()


def discover_projects(path: Path, *, max_depth: int = 4) -> list[Path]:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise ProjectError(f"Search directory does not exist: {root}")
    ignored = {"Library", "Temp", "Logs", "Obj", "Build", "Builds", "Assets", "Packages", "node_modules"}
    result = []
    for directory, children, _ in os.walk(root, followlinks=False):
        current = Path(directory)
        if is_unity_project(current):
            result.append(current)
            children[:] = []
        elif len(current.relative_to(root).parts) >= max_depth:
            children[:] = []
        else:
            children[:] = sorted(
                name
                for name in children
                if name not in ignored
                and not name.startswith(".")
                and not (current / name).is_symlink()
                and not (current / name).is_junction()
            )
    return result


def local_package_inputs(project: Path) -> list[Path]:
    """Follow the selected project's local Unity package dependencies, including archives."""
    result: set[Path] = set()
    pending = [project / "Packages/manifest.json"]
    visited: set[Path] = set()
    while pending:
        manifest = pending.pop()
        if manifest in visited or not manifest.is_file():
            continue
        visited.add(manifest)
        try:
            dependencies = json.loads(manifest.read_text(encoding="utf-8-sig")).get("dependencies", {})
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(dependencies, dict):
            continue
        for value in dependencies.values():
            if isinstance(value, str) and value.startswith("file:"):
                dependency = (manifest.parent / value[5:]).resolve()
                if dependency.exists() and dependency not in result:
                    result.add(dependency)
                    if dependency.is_dir():
                        pending.append(dependency / "package.json")
    return sorted(result)


@dataclass(frozen=True)
class ProjectCatalog:
    """An independent registry/configuration namespace with optional app preset detection."""

    config_name: str = CONFIG_NAME
    config_fallbacks: tuple[str, ...] = ()
    project_env: str = PROJECT_ENV
    home_env: str = "UNITYBUILD_HOME"
    home_directory: str = ".unitybuild"
    preset_resolver: Callable[[Path], str] = lambda path: "unity"

    def registry_path(self) -> Path:
        configured = os.environ.get(self.home_env, "")
        return (Path(configured).expanduser() if configured else Path.home() / self.home_directory) / "projects.json"

    def read_registry(self) -> tuple[dict[str, str], str]:
        path = self.registry_path()
        if not path.exists():
            return {}, ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            projects, default = data["projects"], data.get("default", "")
            if (
                not isinstance(projects, dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in projects.items())
                or not isinstance(default, str)
            ):
                raise ValueError("invalid project registry")
            return projects, default
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ProjectError(f"Cannot read project registry {path}: {exc}") from exc

    def write_registry(self, projects: dict[str, str], default: str) -> None:
        path = self.registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"projects": projects, "default": default}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def load_project(self, path: Path) -> Project:
        root = path.expanduser().resolve()
        if root.is_file() and root.name in (self.config_name, *self.config_fallbacks):
            root = root.parent
        if not root.is_dir():
            raise ProjectError(f"Project directory does not exist: {root}")
        if not is_unity_project(root):
            raise ProjectError(
                f"Not a Unity project: {root}. Select a folder containing Assets and ProjectSettings/ProjectVersion.txt. "
                "Use `project scan PATH` to find Unity projects below a workspace."
            )
        from unitybuild.unity import read_project_version

        try:
            read_project_version(root / "ProjectSettings/ProjectVersion.txt")
        except ValueError as exc:
            raise ProjectError(str(exc)) from exc
        config = self.config_path(root)
        if not config.exists():
            return Project(root, root.name, preset=self.preset_resolver(root))
        try:
            content = config.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise ProjectError(f"Cannot read {config}: {exc}") from exc
        return self.project_from_config(root, content)

    def config_path(self, root: Path) -> Path:
        """Return the effective file, including an adapter's fallback configuration."""
        names = (self.config_name, *self.config_fallbacks)
        return next(
            (root / name for name in names if (root / name).is_file()),
            root / self.config_name,
        )

    def project_from_config(self, root: Path, content: str) -> Project:
        """Validate a draft using the same rules as configuration loaded from disk."""
        config = self.config_path(root)
        try:
            data = tomllib.loads(content)
            if data.get("schema_version") != 1:
                raise ValueError("schema_version must be 1")
            settings = data.get("project", {})
            build = data.get("build", {})
            if not isinstance(settings, dict) or not isinstance(build, dict):
                raise TypeError("project and build must be tables")
            unknown = set(data) - {"schema_version", "project", "build"}
            unknown |= set(settings) - {"name", "preset", "path"}
            unknown |= set(build) - {"platform", "method", "output", "output_environment", "configuration_environment"}
            if unknown:
                raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
            if any(not isinstance(value, str) or not value.strip() for value in [*settings.values(), *build.values()]):
                raise ValueError("project and build settings must be nonempty strings")
            if settings.get("path", ".") != ".":
                raise ValueError("Build settings must be stored in the Unity project itself; project.path must be '.'.")
            preset = settings.get("preset", self.preset_resolver(root))
            method = build.get("method", "UnityBuild.Builder.Build")
            if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", method):
                raise ValueError("build.method must be a qualified static C# method name")
            platform = build.get("platform", "")
            if platform and not supports_build(platform):
                raise ValueError(f"Unsupported build.platform: {platform}")
            output_env = build.get("output_environment", "UNITYBUILD_OUTPUT")
            if method == "UnityBuild.Builder.Build" and output_env != "UNITYBUILD_OUTPUT":
                raise ValueError("The packaged builder requires output_environment = UNITYBUILD_OUTPUT")
            configuration_env = build.get("configuration_environment", "")
            reserved = {
                "PATH",
                "HOME",
                "USERPROFILE",
                "SYSTEMROOT",
                "COMSPEC",
                "PYTHONPATH",
                "CODEX_HOME",
            }
            for name in (output_env, *([configuration_env] if configuration_env else [])):
                if not re.fullmatch(r"[A-Za-z_]\w*", name) or name.upper() in reserved:
                    raise ValueError("Build environment settings must name build-specific environment variables")
            if configuration_env and configuration_env.upper() in {
                output_env.upper(),
                "UNITYBUILD_OUTPUT",
                "UNITYBUILD_PROFILE",
                "UNITYBUILD_PLATFORM",
            }:
                raise ValueError(
                    "build.configuration_environment must not overwrite the output or standard builder variables"
                )
            project = Project(
                root,
                settings.get("name", root.name),
                preset,
                platform,
                method,
                build.get("output", default_output(platform)),
                output_env,
                configuration_env,
            )
            return project
        except (OSError, ValueError, TypeError) as exc:
            raise ProjectError(f"Invalid {config}: {exc}") from exc

    def resolve_project(self, requested: str = "", *, required: bool = True) -> Project | None:
        selection = requested or os.environ.get(self.project_env, "")
        if selection:
            candidate = Path(selection).expanduser()
            if candidate.exists() or candidate.is_absolute():
                return self.load_project(candidate)
            projects, _ = self.read_registry()
            if selection not in projects:
                raise ProjectError(f"Unknown project: {selection}. Use `unitybuild project list` or pass a path.")
            return self.load_project(Path(projects[selection]))
        current = Path.cwd().resolve()
        for candidate in (current, *current.parents):
            if is_unity_project(candidate):
                return self.load_project(candidate)
        projects, default = self.read_registry()
        if default:
            if default not in projects:
                raise ProjectError(f"Default project {default!r} is not registered.")
            return self.load_project(Path(projects[default]))
        candidates = discover_projects(current)
        if len(candidates) == 1:
            return self.load_project(candidates[0])
        if required:
            choices = "\n".join(str(path) for path in candidates)
            raise ProjectError(
                "No project selected. Use `unitybuild --project PATH` or `project add NAME PATH`."
                + (f"\nMultiple Unity projects found; choose one:\n{choices}" if choices else "")
            )
        return None

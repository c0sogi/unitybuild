"""Unity command registration and standalone entry point."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .build_session import BuildSessionError
from .builds import BUILDER_PATH, build_project, ensure_builder
from .menu import Menu, UnityProjectMenu, choose_project, render_plan
from .platforms import default_output, supported_platforms, supports_build
from .profiles import SavedBuildProfile, discover_build_profiles, resolve_build_profile
from .progress import run_logged_process
from .projects import ProjectCatalog, ProjectError, discover_projects
from .unity import UnityVersionError, read_project_version


def register_commands(
    app: typer.Typer,
    *,
    catalog: ProjectCatalog | None = None,
    build_handler: Callable | None = None,
    command_name: str = "unitybuild",
    logs_directory: str = "Logs/unitybuild",
) -> None:
    catalog = catalog or ProjectCatalog()
    load_project, resolve_project = catalog.load_project, catalog.resolve_project
    read_registry, write_registry = catalog.read_registry, catalog.write_registry

    def default_build_handler(project, profile, *, details=False, **kwargs):
        console = Console(highlight=False)
        result = build_project(
            project,
            profile,
            runner=run_logged_process,
            on_plan=lambda plan: render_plan(console, plan, details=details, command_name=command_name),
            **kwargs,
        )
        if not result.dry_run:
            console.print("Build completed.", style="bold green")
            console.print(str(result.output), markup=False)
            console.print(f"Build record: {result.record}", markup=False, style="dim")
        return result

    build_handler = build_handler or default_build_handler

    project_app = typer.Typer(help="Find, register, select, and configure Unity projects.", no_args_is_help=True)

    @project_app.command("scan")
    def scan_projects(
        path: Annotated[Path, typer.Argument()] = Path("."),
        depth: Annotated[int, typer.Option(min=0, max=12)] = 4,
    ) -> None:
        """Find Unity project folders, including projects without build-manager settings."""
        with command_errors():
            projects = discover_projects(path, max_depth=depth)
            if not projects:
                typer.echo("No Unity projects found.")
            for project in projects:
                typer.echo(str(project))

    @contextmanager
    def command_errors():
        try:
            yield
        except (ProjectError, UnityVersionError, BuildSessionError, OSError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc

    @project_app.command("init")
    def init_project(
        path: Path,
        platform: Annotated[str | None, typer.Option(help="Unity player platform; explicitly required for init.")] = None,
        build_method: Annotated[
            str, typer.Option(help="Existing static C# build method; omit to install the bundled builder.")
        ] = "",
    ) -> None:
        """Create build settings for an existing Unity project. Never overwrite existing files."""
        with command_errors():
            root = path.expanduser().resolve()
            read_project_version(root / "ProjectSettings/ProjectVersion.txt")
            if not (root / "Assets").is_dir():
                raise ProjectError(f"Unity Assets directory is missing: {root}")
            config = root / catalog.config_name
            builder = root / BUILDER_PATH
            if any((root / name).exists() for name in (catalog.config_name, *catalog.config_fallbacks)):
                raise ProjectError("Project settings or the packaged builder already exist; edit them explicitly.")
            if platform is None:
                raise ProjectError("Choose a platform explicitly: project init PATH --platform PLATFORM.")
            if not supports_build(platform):
                raise ProjectError(f"Unsupported build platform: {platform}. Supported: {', '.join(supported_platforms())}")
            output = default_output(platform)
            method = build_method or "UnityBuild.Builder.Build"
            if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+", method):
                raise ProjectError("--build-method must be a qualified static C# method name.")
            content = (
                f"schema_version = 1\n\n[project]\nname = {json.dumps(root.name, ensure_ascii=False)}\n"
                f'preset = "unity"\npath = "."\n\n[build]\nplatform = "{platform}"\n'
                f'method = "{method}"\noutput = "{output}"\n'
                'output_environment = "UNITYBUILD_OUTPUT"\n'
            )
            if not build_method:
                ensure_builder(root)
            with config.open("x", encoding="utf-8") as stream:
                stream.write(content)
            typer.echo(f"Created {config}")
            if not build_method:
                typer.echo(f"Installed Unity builder: {builder}")
            typer.echo(f'Next: {command_name} --project "{root}" build --dry-run')

    @project_app.command("add")
    def add_project(name: str, path: Path) -> None:
        """Register a Unity project. Build-manager configuration is optional."""
        with command_errors():
            if not name.strip() or any(char in name for char in ("/", "\\", ":")) or name in {".", ".."}:
                raise ProjectError("Use a nonempty project name without path separators.")
            project = load_project(path)
            projects, default = read_registry()
            if name in projects and Path(projects[name]) != project.root:
                raise ProjectError(f"Project name {name!r} is already registered. Remove it or choose another name.")
            projects[name] = str(project.root)
            write_registry(projects, default or name)
            typer.echo(f"Registered {name}: {project.root}")

    @project_app.command("list")
    def list_projects() -> None:
        """List registered paths, including missing projects so they can be repaired."""
        with command_errors():
            projects, default = read_registry()
            if not projects:
                typer.echo("No registered projects. Use `project add NAME PATH`.")
            for name, path in projects.items():
                typer.echo(
                    f"{'*' if name == default else ' '} {name}: {path}{' [missing]' if not Path(path).is_dir() else ''}"
                )

    @project_app.command("use")
    def use_project(name: str) -> None:
        """Set the default project used outside a configured project directory."""
        with command_errors():
            projects, _ = read_registry()
            if name not in projects:
                raise ProjectError(f"Unknown registered project: {name}")
            load_project(Path(projects[name]))
            write_registry(projects, name)
            typer.echo(f"Default project: {name}")

    @project_app.command("remove")
    def remove_project(name: str) -> None:
        """Remove a registration; leave all project files untouched."""
        with command_errors():
            projects, default = read_registry()
            if name not in projects:
                raise ProjectError(f"Unknown registered project: {name}")
            del projects[name]
            write_registry(projects, "" if default == name else default)
            typer.echo(f"Removed registration: {name}")

    @project_app.command("show")
    def show_project() -> None:
        """Show the effective selected project and its settings."""
        with command_errors():
            project = resolve_project()
            assert project is not None
            typer.echo(f"Project: {project.name}\nRoot: {project.root}\nPreset: {project.preset}")
            if project.preset == "unity":
                typer.echo(
                    f"Unity project: {project.unity_project}\nPlatform: {project.platform or 'Not selected'}"
                    f"\nMethod: {project.build_method}"
                )

    app.add_typer(project_app, name="project")

    @project_app.command("settings")
    def edit_settings(plain: bool = False) -> None:
        """View and edit the selected project's TOML; changes are saved explicitly."""
        from .settings_editor import BuildSettingsEditor

        with command_errors():
            project = resolve_project()
            assert project is not None
            BuildSettingsEditor(project.root, catalog=catalog, plain=plain).run()

    @app.command("profiles")
    def profiles() -> None:
        """List the selected project's saved Unity Build Profile assets."""
        with command_errors():
            project = resolve_project()
            assert project is not None
            saved = discover_build_profiles(project)
            for item in saved:
                typer.echo(f"{item.name} | {item.platform} | {item.configuration} | {item.asset_path}")
            if not saved:
                typer.echo("No saved Unity Build Profiles. Legacy script profiles: release, debug.")

    @project_app.command("targets")
    def list_targets(unity_path: str = "") -> None:
        """Inspect this Unity installation's catalog and installed build modules."""
        from .targets import discover_targets, show_targets

        with command_errors():
            project = resolve_project()
            assert project is not None
            show_targets(Console(highlight=False), discover_targets(project.root, unity_path))

    @project_app.command("scenes")
    def edit_scenes(plain: bool = False) -> None:
        """Choose and order scenes in Unity Build Settings; save explicitly."""
        from .scenes import BuildScenesEditor

        with command_errors():
            project = resolve_project()
            assert project is not None
            BuildScenesEditor(project.root, plain=plain).run()

    @app.command("build")
    def build(
        profile: str = "release",
        platform: Annotated[str | None, typer.Option(help="Build target for a project without a saved profile.")] = None,
        unity_path: str = "",
        dry_run: bool = False,
        details: Annotated[bool, typer.Option(help="Show the build method and full Unity command.")] = False,
    ) -> None:
        """Build the selected project using its own settings."""
        with command_errors():
            project = resolve_project()
            assert project is not None
            selected = resolve_build_profile(project, profile)
            if platform is not None:
                if not supports_build(platform):
                    raise ProjectError(f"Unsupported build platform: {platform}. Supported: {', '.join(supported_platforms())}")
                if isinstance(selected, SavedBuildProfile):
                    if platform != selected.platform:
                        raise ProjectError("--platform conflicts with the selected Unity Build Profile's platform.")
                elif project.preset != "unity":
                    raise ProjectError("This application preset selects its own target; use its build settings.")
                else:
                    project = replace(
                        project,
                        platform=platform,
                        output=project.output if project.platform in ("", platform) else "",
                    )
            options = {"details": True} if details else {}
            build_handler(
                project, selected, unity_path=unity_path, dry_run=dry_run, **options
            )

    @app.command("history")
    def history() -> None:
        """Show the selected Unity project's recent build records."""
        with command_errors():
            project = resolve_project()
            assert project is not None
            if project.preset != "unity" and not discover_build_profiles(project):
                typer.echo(f"App build logs and records: {project.unity_project / 'Logs'}")
                return
            from .history import render_history

            render_history(Console(highlight=False), project.unity_project, logs_directory)


app = typer.Typer(help="Select and build any Unity project.", no_args_is_help=False)
catalog = ProjectCatalog()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context, project: Annotated[str, typer.Option("--project", "-p")] = "", plain: bool = False):
    previous = os.environ.get(catalog.project_env)
    if project:
        os.environ[catalog.project_env] = project

    def restore():
        if previous is None:
            os.environ.pop(catalog.project_env, None)
        else:
            os.environ[catalog.project_env] = previous

    ctx.call_on_close(restore)
    if ctx.invoked_subcommand is None:
        try:
            selected = catalog.resolve_project(required=False)
            while True:
                path = selected.root if selected else choose_project(Menu(Path.cwd(), plain=plain), catalog)
                if path is None:
                    return
                path = UnityProjectMenu(catalog.load_project(path), plain=plain, catalog=catalog).run()
                if path is None:
                    return
                selected = catalog.load_project(path)
        except ProjectError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc


register_commands(app, catalog=catalog)

if __name__ == "__main__":
    app()

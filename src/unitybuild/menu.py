"""Shared terminal menu controls, without application-specific actions."""

from __future__ import annotations

import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import questionary
from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .platforms import PLATFORMS, platform_label
from .profiles import BuildProfile, SavedBuildProfile, discover_build_profiles
from .projects import Project, ProjectCatalog, ProjectError, discover_projects

if TYPE_CHECKING:
    from .builds import BuildPlan

class Menu:
    def __init__(
        self, repo_root: Path, *, plain: bool = False, console: Console | None = None, platform: str = ""
    ) -> None:
        self.root = repo_root
        self.selected_platform = platform
        self.console = console or Console(highlight=False)
        self.plain = plain or self.console.is_dumb_terminal or not self.console.is_terminal
        self.fullscreen = not self.plain and not self.console.legacy_windows

    def choose(self, title: str, choices: list[str], *, back: str = "Back", default: int | None = None) -> int | None:
        if self.plain:
            self.console.print(title, style="bold")
            for index, choice in enumerate(choices, 1):
                self.console.print(f"  {index}. {choice}", markup=False)
            self.console.print(f"  0. {back}")
            while True:
                try:
                    answer = input("Number > ").strip()
                except (EOFError, KeyboardInterrupt):
                    return None
                if not answer and default is not None:
                    return default
                if answer in {"0", "q"}:
                    return None
                if answer.isdigit() and 1 <= int(answer) <= len(choices):
                    return int(answer) - 1
                self.console.print("Enter one of the listed numbers.", style="yellow")
        return questionary.select(
            title,
            choices=[questionary.Choice(label, value=index) for index, label in enumerate(choices)]
            + [questionary.Choice(back, value=-1)],
            instruction="(↑↓ move • Enter select • Ctrl+C back)",
            style=questionary.Style([("pointer", "fg:cyan bold"), ("highlighted", "fg:cyan bold")]),
        ).ask()

    def pause(self) -> None:
        if self.plain:
            try:
                input("Press Enter to return to the menu")
            except (EOFError, KeyboardInterrupt):
                pass
        else:
            questionary.text("Press Enter to return to the menu").ask()


def run_terminal_module(module: str, arguments: Sequence[str] = ()) -> int:
    """Share the environment and terminal; let the child handle console Ctrl+C.

    A callable handler protects the parent while leaving the child's default
    signal handling intact after exec. Both processes remain in the console's
    foreground group, so Ctrl+C reaches the child on Windows and POSIX.
    """
    previous = signal.signal(signal.SIGINT, lambda _signal, _frame: None)
    try:
        with subprocess.Popen([sys.executable, "-m", module, *arguments]) as child:
            return child.wait()
    finally:
        signal.signal(signal.SIGINT, previous)


def project_header(console: Console, project: Project) -> None:
    console.print()
    console.print(Text(project.name, style="bold bright_white"))
    console.print(Text(str(project.root), style="dim"))
    console.print()


def render_plan(
    console: Console, plan: BuildPlan, *, details: bool = False, command_name: str = "unitybuild"
) -> None:
    project = plan.project
    rows = Table.grid(padding=(0, 3), expand=True)
    rows.add_column(style="dim", no_wrap=True)
    rows.add_column(ratio=1, overflow="fold")
    rows.add_row("Project", Text(project.name, style="bold"))
    target = platform_label(project.platform)
    if isinstance(plan.profile, SavedBuildProfile):
        target = PLATFORMS[project.platform].build_label(
            app_bundle=plan.profile.app_bundle, export_project=plan.profile.export_project
        )
    rows.add_row("Target", Text(target))
    rows.add_row(
        "Profile",
        Text(
            plan.profile.name if isinstance(plan.profile, SavedBuildProfile) else plan.profile.value.title(),
            style="cyan",
        ),
    )
    rows.add_row("Unity", Text(plan.version))
    try:
        output = plan.output.relative_to(project.root).as_posix()
    except ValueError:
        output = str(plan.output)
    rows.add_row("Output", Text(output))
    if plan.scenes is not None:
        rows.add_row("Scenes", Text("\n".join(plan.scenes) or "None selected"))
    rows.add_row("Folder", Text(str(project.root), style="dim"))

    content: list = [rows]
    if plan.scene_error:
        content.extend([Text(), Text(plan.scene_error, style="yellow")])
    if plan.dry_run:
        content.extend([Text(), Text("Preview only. Unity was not started; no files changed.", style="dim")])
    console.print()
    console.print(
        Panel(
            Group(*content),
            title="Build plan" if plan.dry_run else "Build",
            title_align="left",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False,
            width=92,
        )
    )
    if details:
        extra = Table.grid(padding=(0, 2))
        extra.add_column(style="dim", no_wrap=True)
        extra.add_column(overflow="fold")
        extra.add_row("Platform", Text(project.platform))
        if isinstance(plan.profile, SavedBuildProfile):
            extra.add_row("Profile asset", Text(plan.profile.asset_path))
            extra.add_row("Build", Text("Unity native build"))
        else:
            extra.add_row("Method", Text(project.build_method))
        if plan.setup_required:
            extra.add_row("Build helper", Text("Prepared automatically when building"))
        # A placeholder is explanatory text, not a runnable executable.
        command = list(plan.command)
        if command[0].startswith("<Unity "):
            extra.add_row("Editor", Text(f"Unity {plan.version} (resolved when building)"))
            command[0] = "Unity"
        extra.add_row("Command", Text(subprocess.list2cmdline(command)))
        console.print(Panel(extra, title="Details", title_align="left", border_style="dim", width=92))
    else:
        console.print("  Use --details to show the build method and command.", style="dim")
    console.print()


def choose_project(menu: Menu, catalog: ProjectCatalog | None = None) -> Path | None:
    catalog = catalog or ProjectCatalog()
    while True:
        projects, _ = catalog.read_registry()
        entries = list(projects.items())
        seen = {Path(path).resolve() for _, path in entries}
        entries += [(path.name, str(path)) for path in discover_projects(Path.cwd()) if path not in seen]
        selected = menu.choose(
            "Choose a Unity project",
            [f"{name} • {path}" for name, path in entries] + ["Enter Unity project or search folder"],
        )
        if selected is None or selected < 0:
            return None
        try:
            path = (
                Path(entries[selected][1])
                if selected < len(entries)
                else Path(input("Project path > ").strip().strip('"'))
            )
            try:
                return catalog.load_project(path).root
            except ProjectError:
                matches = discover_projects(path)
                if not matches:
                    raise
                choice = menu.choose("Unity projects found", [str(item) for item in matches])
                if choice is not None and choice >= 0:
                    return catalog.load_project(matches[choice]).root
        except (EOFError, KeyboardInterrupt):
            return None
        except ProjectError as exc:
            menu.console.print(str(exc), style="red", markup=False)


class UnityProjectMenu(Menu):
    def __init__(
        self,
        project: Project,
        *,
        plain: bool = False,
        catalog: ProjectCatalog | None = None,
        module: str = "unitybuild.cli",
    ) -> None:
        super().__init__(project.root, plain=plain)
        self.project = project
        self.catalog = catalog or ProjectCatalog()
        self.module = module

    def run(self) -> Path | None:
        from .settings_editor import BuildSettingsEditor
        from .targets import choose_target

        while True:
            project_header(self.console, self.project)
            selected = self.choose(
                "Choose a task",
                ["Build", "Show build details", "Build history", "Build settings", "Build scenes", "Switch project"],
                back="Exit",
            )
            if selected is None or selected < 0:
                return None
            if selected == 3:
                try:
                    BuildSettingsEditor(
                        self.project.root, catalog=self.catalog, plain=self.plain, console=self.console
                    ).run()
                    self.project = self.catalog.load_project(self.project.root)
                except (ProjectError, OSError) as exc:
                    self.console.print(str(exc), style="red", markup=False)
                continue
            if selected == 4:
                from .scenes import BuildScenesEditor

                try:
                    BuildScenesEditor(self.project.root, plain=self.plain, console=self.console).run()
                except (ProjectError, OSError) as exc:
                    self.console.print(str(exc), style="red", markup=False)
                continue
            if selected == 5:
                path = choose_project(self, self.catalog)
                if path is not None:
                    return path
                continue
            arguments = ["--project", str(self.project.root)]
            if selected == 2:
                arguments += ["history"]
            else:
                self.project = self.catalog.load_project(self.project.root)
                saved = discover_build_profiles(self.project)
                script_profiles = list(BuildProfile)
                labels = [item.name for item in saved] if saved else [item.value.title() for item in script_profiles]
                profile = self.choose("Choose build profile", labels, default=0)
                if profile is None or profile < 0:
                    continue
                selection = saved[profile].asset_path if saved else script_profiles[profile].value
                arguments += ["build", "--profile", selection]
                if not saved and not self.project.platform and self.project.preset == "unity":
                    target = choose_target(self, configuration_only=selected == 1)
                    if target is None:
                        continue
                    arguments += ["--platform", target]
                if selected == 1:
                    arguments += ["--dry-run", "--details"]
            try:
                result = run_terminal_module(self.module, arguments)
                if result != 0:
                    self.console.print(f"Task failed (exit {result}).", style="red")
            except OSError as exc:
                self.console.print(f"Cannot start task: {exc}", style="red", markup=False)
            self.pause()

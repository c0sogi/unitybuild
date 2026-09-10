"""Review and edit project TOML in memory, writing only on Save changes."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import questionary
import tomlkit
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .builds import resolve_output_path
from .menu import Menu
from .platforms import default_output, platform_label
from .profiles import BuildProfile
from .projects import ProjectCatalog, ProjectError
from .targets import choose_target

FIELDS = [
    ("Build target", "build", "platform", "Not selected (or supplied by a saved Unity profile)"),
    ("Output path", "build", "output", "Automatic for the selected target"),
    ("Project name", "project", "name", "Folder name"),
]

SCRIPT_FIELDS = [
    ("Build method", "build", "method", "UnityBuild.Builder.Build"),
    ("Output environment variable", "build", "output_environment", "UNITYBUILD_OUTPUT"),
    ("Configuration environment variable", "build", "configuration_environment", "None"),
]


class SettingsDraft:
    def __init__(self, root: Path, catalog: ProjectCatalog) -> None:
        self.root = root
        self.catalog = catalog
        self.path = catalog.config_path(root)
        self.original = self.path.read_bytes() if self.path.exists() else None
        try:
            self.document = (
                tomlkit.parse(self.original.decode("utf-8-sig")) if self.original is not None else tomlkit.document()
            )
        except ValueError as exc:
            raise ProjectError(f"Cannot read {self.path}: {exc}") from exc
        if self.original is None:
            self.document["schema_version"] = 1
        self.initial = self.text

    @property
    def text(self) -> str:
        return tomlkit.dumps(self.document)

    def value(self, section: str, key: str) -> str:
        return str(self.document.get(section, {}).get(key, ""))

    def set_value(self, section: str, key: str, value: str) -> None:
        if section not in self.document:
            if not value:
                return
            self.document[section] = tomlkit.table()
        table = self.document[section]
        if value:
            table[key] = value
        elif key in table:
            del table[key]

    def save(self) -> bool:
        project = self.catalog.project_from_config(self.root, self.text)
        if project.output:
            # A saved Unity profile may supply the platform later. Check that
            # both Release and Debug output paths avoid project sources now.
            for profile in BuildProfile:
                resolve_output_path(
                    project.root, project.output, development=profile.configuration == "development"
                )
        if self.path.is_symlink() or self.catalog.config_path(self.root) != self.path:
            raise ProjectError("Settings file changed location. Reopen Build settings before saving.")
        current = self.path.read_bytes() if self.path.exists() else None
        if current != self.original:
            raise ProjectError("Settings were changed outside this menu. Reopen Build settings before saving.")
        if self.original is not None and self.text == self.initial:
            return False
        payload = self.text.encode("utf-8")
        if self.original and self.original.startswith(b"\xef\xbb\xbf"):
            payload = b"\xef\xbb\xbf" + payload
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=f".{self.path.name}.", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if self.path.exists():
                temporary.chmod(self.path.stat().st_mode)
            current = self.path.read_bytes() if self.path.exists() else None
            if current != self.original:
                raise ProjectError("Settings were changed outside this menu. Reopen Build settings before saving.")
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True


class BuildSettingsEditor(Menu):
    def __init__(self, root: Path, *, catalog: ProjectCatalog, plain: bool = False, console=None) -> None:
        super().__init__(root, plain=plain, console=console)
        self.draft = SettingsDraft(root, catalog)

    def text_input(self, title: str, current: str) -> str | None:
        title += " ('-' restores the default)"
        if not self.plain:
            return questionary.text(title, default=current).ask()
        try:
            return input(f"{title} [{current}] > ").strip() or current
        except (EOFError, KeyboardInterrupt):
            return None

    def show(self) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim")
        table.add_column(overflow="fold")
        for label, section, key, default in FIELDS:
            value = self.draft.value(section, key)
            if key == "platform":
                value = platform_label(value)
            table.add_row(label, Text(value or default))
        table.add_row("Settings file", Text(str(self.draft.path)))
        self.console.print(Panel(table, title="Build settings", border_style="cyan"))
        self.console.print(
            "Saved Unity Build Profiles supply their own target and scene settings. "
            "Choose a profile when starting a build.", style="dim"
        )
        state = "New file (not saved)" if self.draft.original is None else (
            "Unsaved changes" if self.draft.text != self.draft.initial else "No changes"
        )
        self.console.print(state, style="yellow")

    def edit_script_builder(self) -> None:
        while True:
            table = Table.grid(padding=(0, 2))
            table.add_column(style="dim")
            table.add_column(overflow="fold")
            for label, section, key, default in SCRIPT_FIELDS:
                table.add_row(label, Text(self.draft.value(section, key) or default))
            self.console.print(Panel(table, title="Script builder (advanced)", border_style="cyan"))
            self.console.print(
                "These settings apply only to C# build methods. Saved Unity Build Profiles do not use them. "
                "Variable names must match the C# method; values are passed only to the Unity child process. "
                "Save changes in the parent menu to apply edits.", style="dim"
            )
            selected = self.choose(
                "Script builder", [f"Edit {label.lower()}" for label, *_ in SCRIPT_FIELDS],
                back="Return to Build settings",
            )
            if selected is None or selected < 0:
                return
            label, section, key, _ = SCRIPT_FIELDS[selected]
            value = self.text_input(label, self.draft.value(section, key))
            if value is not None:
                self.draft.set_value(section, key, "" if value.strip() == "-" else value.strip())

    def run(self) -> bool:
        while True:
            self.show()
            selected = self.choose(
                "Build settings",
                [f"Edit {label.lower()}" for label, *_ in FIELDS]
                + ["Script builder (advanced)", "View TOML", "Save changes"],
                back="Back (discard unsaved changes)",
            )
            if selected is None or selected < 0:
                return False
            if selected == len(FIELDS):
                self.edit_script_builder()
            elif selected == len(FIELDS) + 1:
                self.console.print(Syntax(self.draft.text, "toml", word_wrap=True))
                self.pause()
            elif selected == len(FIELDS) + 2:
                try:
                    saved = self.draft.save()
                except (ProjectError, OSError) as exc:
                    self.console.print(str(exc), style="red", markup=False)
                    continue
                self.console.print(f"Saved {self.draft.path}" if saved else "No changes to save.", markup=False)
                return saved
            else:
                label, section, key, _ = FIELDS[selected]
                if key == "platform":
                    value = choose_target(self, allow_unset=True, configuration_only=True)
                    if value is None:
                        continue
                    previous = self.draft.value(section, key)
                    self.draft.set_value(section, key, value)
                    # Only reset an old standard output; preserve custom paths.
                    if previous != value and self.draft.value("build", "output") == default_output(previous):
                        self.draft.set_value("build", "output", "")
                else:
                    value = self.text_input(label, self.draft.value(section, key))
                    if value is not None:
                        self.draft.set_value(section, key, "" if value.strip() == "-" else value.strip())

"""Discover a selected Editor's platform catalog and installed module evidence.

Hub metadata supplies the catalog, not a hard-coded menu. The small adapter map
translates recognized module IDs to build commands that this package implements.
Unknown/new modules remain visible and are never advertised as supported builds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .execution import resolve_unity_editor
from .menu import Menu
from .platforms import PLATFORMS, supports_build
from .projects import ProjectError
from .unity import UnityVersionError, read_project_version


@dataclass(frozen=True)
class Target:
    identifier: str
    name: str
    installed: bool | None
    supported: bool
    source: str

    @property
    def status(self) -> str:
        module = (
            "Module detected"
            if self.installed
            else "Module not installed"
            if self.installed is False
            else "Not verified"
        )
        return module if self.supported else f"{module}; unitybuild unsupported"

    @property
    def label(self) -> str:
        return f"{self.name} · {self.status}"


@dataclass(frozen=True)
class TargetCatalog:
    version: str
    editor: Path | None
    targets: tuple[Target, ...]
    note: str = ""


def installation_paths(editor: Path) -> tuple[Path, Path]:
    if editor.parent.name == "MacOS" and editor.parent.parent.name == "Contents":
        contents = editor.parent.parent
        return contents.parent.parent / "modules.json", contents / "PlaybackEngines"
    return editor.parent.parent / "modules.json", editor.parent / "Data/PlaybackEngines"


def inspect_installation(editor: Path, version: str = "") -> TargetCatalog:
    adapters = {module: platform for platform in PLATFORMS.values() for module in platform.hub_modules}
    metadata, engines = installation_paths(editor)
    note = ""
    modules = []
    try:
        data = json.loads(metadata.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise TypeError("Expected a module list")
        modules = [
            item
            for item in data
            if isinstance(item, dict)
            and str(item.get("category", "")).lower() in {"platform", "platforms"}
            and not item.get("parent")
            and not item.get("hidden")
        ]
    except (OSError, ValueError, TypeError) as exc:
        note = f"Hub catalog unavailable; showing detected module folders only. ({exc})"
    try:
        folders = {p.name.casefold(): p for p in engines.iterdir() if p.is_dir()}
        inspected = True
    except OSError:
        folders, inspected = {}, False
    targets: dict[str, Target] = {}
    consumed: set[str] = set()
    for module in modules:
        module_id = str(module.get("id", ""))
        if not module_id:
            continue
        adapter = adapters.get(module_id)
        identifier, folder_name = (adapter.identifier, adapter.player_folder) if adapter else (module_id, "")
        folder = folders.get(folder_name.casefold())
        installed: bool | None = folder is not None if inspected and folder_name else None
        if folder:
            consumed.add(folder.name.casefold())
            # Shared desktop folders do not prove that an optional backend or
            # dedicated server module is present. Inspect player variations.
            if module_id.endswith(("-il2cpp", "-mono", "-server")):
                marker = module_id.rsplit("-", 1)[1]
                variations = folder / "Variations"
                if variations.is_dir():
                    installed = any(marker in p.name.casefold() for p in variations.iterdir())
                else:
                    installed = None
        candidate = Target(
            identifier,
            (adapter.catalog_label if adapter else "") or str(module.get("name") or module_id),
            installed,
            supports_build(identifier),
            str(metadata),
        )
        existing = targets.get(identifier)
        # One selectable target per platform; prefer an installed backend.
        rank = {True: 2, None: 1, False: 0}
        if existing is None or rank[candidate.installed] > rank[existing.installed]:
            targets[identifier] = candidate
    # Native desktop Mono can be bundled with the editor and absent from Hub's
    # optional module list. Actual installed folders supply that evidence.
    for module_id, adapter in adapters.items():
        identifier, folder_name = adapter.identifier, adapter.player_folder
        if module_id.endswith(("-il2cpp", "-server")):
            continue
        folder = folders.get(folder_name.casefold())
        if not folder:
            continue
        consumed.add(folder.name.casefold())
        installed = True
        if module_id.endswith("-mono"):
            variations = folder / "Variations"
            installed = variations.is_dir() and any("mono" in p.name.casefold() for p in variations.iterdir())
        if installed and (identifier not in targets or targets[identifier].installed is not True):
            targets[identifier] = Target(
                identifier, adapter.catalog_label or identifier, True, supports_build(identifier), str(folder)
            )
    for key, folder in folders.items():
        if key not in consumed:
            targets[key] = Target(key, folder.name, True, False, str(folder))
    ordered = sorted(targets.values(), key=lambda item: (not (item.installed and item.supported), item.name.casefold()))
    return TargetCatalog(version, editor, tuple(ordered), note)


def discover_targets(project: Path, requested: str = "") -> TargetCatalog:
    version = read_project_version(project / "ProjectSettings/ProjectVersion.txt")
    try:
        editor = resolve_unity_editor(project, requested)
    except (OSError, UnityVersionError) as exc:
        return TargetCatalog(version, None, (), str(exc))
    return inspect_installation(editor, version)


def show_targets(console: Console, catalog: TargetCatalog) -> None:
    console.print(Text(f"Unity {catalog.version} · {catalog.editor or 'Editor not found'}", style="dim"))
    if catalog.note:
        console.print(Text(catalog.note, style="yellow"))
    table = Table("Target", "Identifier", "Status")
    for target in catalog.targets:
        table.add_row(Text(target.name), Text(target.identifier), Text(target.status))
    console.print(table)
    console.print("Module detection does not verify SDKs, toolchains or project compilation.", style="dim")


def choose_target(menu: Menu, *, allow_unset: bool = False, configuration_only: bool = False) -> str | None:
    while True:
        catalog = discover_targets(menu.root)
        menu.console.print(Text(f"Unity {catalog.version} · {catalog.editor or 'Editor not found'}", style="dim"))
        if catalog.note:
            menu.console.print(Text(catalog.note, style="yellow"))
        menu.console.print("Module detection does not verify SDKs or project compilation.", style="dim")
        if configuration_only:
            menu.console.print("Missing modules can be selected for configuration or preview.", style="dim")
        choices = [target.label for target in catalog.targets]
        choices += ["Refresh installed modules", "Enter target identifier manually"]
        if allow_unset:
            choices += ["Not selected"]
        selected = menu.choose("Choose build target", choices)
        if selected is None or selected < 0:
            return None
        if selected == len(catalog.targets):
            continue
        if selected == len(catalog.targets) + 1:
            try:
                value = input("Unity build target identifier > ").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not supports_build(value):
                menu.console.print("This target is not implemented by unitybuild.", style="yellow")
                continue
            known = next((item for item in catalog.targets if item.identifier == value), None)
            if known and known.installed is False and not configuration_only:
                menu.console.print(
                    "Install this module for the selected Unity version in Unity Hub, then refresh.", style="yellow"
                )
                continue
            return value
        if selected == len(catalog.targets) + 2:
            return ""
        target = catalog.targets[selected]
        if not target.supported:
            menu.console.print(
                "Unity lists this module, but unitybuild has no build adapter for it yet.", style="yellow"
            )
            continue
        if target.installed is False and not configuration_only:
            menu.console.print(
                "Install this module for the selected Unity version in Unity Hub, then refresh.", style="yellow"
            )
            continue
        return target.identifier


def check_target_module(editor: Path, target: str) -> None:
    known = next((item for item in inspect_installation(editor).targets if item.identifier == target), None)
    if known and known.installed is False:
        raise ProjectError(
            f"The {target} build module is not installed for {editor}. "
            "Add the module to this Unity version in Unity Hub."
        )

"""Read and edit Unity's standard build scene list without changing scene assets."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import yaml

from .build_session import BuildSession, BuildSessionError, select_build_session
from .menu import Menu
from .projects import ProjectError

SETTINGS = "ProjectSettings/EditorBuildSettings.asset"
SCENE_BLOCK = re.compile(r"^  m_Scenes:.*?(?=^  [A-Za-z_]|\Z)", re.MULTILINE | re.DOTALL)


def read_scenes(root: Path) -> list[dict]:
    path = root / SETTINGS
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
        text = re.sub(r"^%.*\n|^--- !u!.*\n", "", text, flags=re.MULTILINE)
        # Unity GUIDs may contain only digits; keep their leading zeroes.
        entries = yaml.load(text, Loader=yaml.BaseLoader)["EditorBuildSettings"]["m_Scenes"]
        if not isinstance(entries, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("path"), str)
            or item.get("enabled") not in ("0", "1") for item in entries
        ):
            raise ValueError("Invalid scene list")
        return [dict(item, enabled=int(item["enabled"])) for item in entries]
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise ProjectError(f"Cannot read build scenes from {path}: {exc}") from exc


def enabled_scenes(root: Path) -> tuple[str, ...]:
    scenes = tuple(item["path"] for item in read_scenes(root) if item.get("enabled"))
    if not scenes:
        raise ProjectError(
            "No scenes are enabled in Unity Build Settings. Open Build scenes in the project menu "
            "(or project scenes) to select the startup scene and build order."
        )
    for scene in scenes:
        path = root / scene
        if not path.resolve().is_relative_to(root.resolve()) or path.suffix != ".unity" or not path.is_file():
            raise ProjectError(f"Build scene is missing or outside the project: {scene}")
    return scenes


class SceneDraft:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / SETTINGS
        self.original = self.path.read_bytes() if self.path.exists() else None
        self.entries = read_scenes(root)
        self.selected = [item["path"] for item in self.entries if item.get("enabled")]
        self.initial = self.selected.copy()

    def save(self) -> bool:
        if self.selected == self.initial:
            return False
        if not self.selected:
            raise ProjectError("Select at least one build scene.")
        entries = []
        for scene in self.selected:
            path = self.root / scene
            if not path.resolve().is_relative_to(self.root.resolve()) or path.suffix != ".unity" or not path.is_file():
                raise ProjectError(f"Build scene is missing or outside the project: {scene}")
            match = re.search(r"^guid: ([a-fA-F0-9]{32})$", Path(str(path) + ".meta").read_text(), re.MULTILINE)
            if not match:
                raise ProjectError(f"Scene has no valid Unity GUID: {scene}")
            entries.append({"enabled": 1, "path": scene, "guid": match.group(1)})
        entries += [dict(item, enabled=0) for item in self.entries if item["path"] not in self.selected]
        select_build_session(self.root, BuildSession.BACKGROUND, interactive=False)
        if self.path.is_symlink() or (self.path.read_bytes() if self.path.exists() else None) != self.original:
            raise ProjectError("Build scene settings changed outside this menu. Reopen Build scenes before saving.")
        text = self.original.decode("utf-8-sig") if self.original is not None else (
            "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!1045 &1\nEditorBuildSettings:\n"
            "  m_ObjectHideFlags: 0\n  serializedVersion: 2\n  m_Scenes: []\n  m_configObjects: {}\n"
        )
        newline = "\r\n" if "\r\n" in text else "\n"
        block = "  m_Scenes:" + newline + "".join(
            f"  - enabled: {item['enabled']}{newline}"
            f"    path: {json.dumps(item['path'], ensure_ascii=False)}{newline}"
            f"    guid: {item['guid']}{newline}" for item in entries
        )
        if len(SCENE_BLOCK.findall(text)) != 1:
            raise ProjectError("Cannot safely locate Unity's build scene list.")
        payload = SCENE_BLOCK.sub(lambda _: block, text).encode("utf-8")
        if self.original and self.original.startswith(b"\xef\xbb\xbf"):
            payload = b"\xef\xbb\xbf" + payload
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if self.path.exists():
                temporary.chmod(self.path.stat().st_mode)
            if (self.path.read_bytes() if self.path.exists() else None) != self.original:
                raise ProjectError("Build scene settings changed outside this menu.")
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True


class BuildScenesEditor(Menu):
    def run(self) -> None:
        draft = SceneDraft(self.root)
        self.console.print("Edits Unity Build Settings. Saved Unity profiles may override this scene list.", style="dim")
        while True:
            self.console.print("Build scenes · first scene starts the application", style="bold")
            for index, scene in enumerate(draft.selected, 1):
                self.console.print(f"  {index}. {scene}", markup=False)
            if not draft.selected:
                self.console.print("No scenes selected", style="yellow")
            self.console.print(f"Settings: {draft.path}", markup=False, style="dim")
            action = self.choose("Edit build scenes", ["Add scene", "Remove scene", "Move up", "Move down", "Save changes"],
                                 back="Back (discard unsaved changes)")
            if action is None or action < 0:
                return
            if action == 4:
                try:
                    saved = draft.save()
                except (OSError, ProjectError, BuildSessionError) as exc:
                    self.console.print(str(exc), style="red", markup=False)
                    continue
                self.console.print("Build scenes saved." if saved else "No changes to save.")
                return
            candidates = (
                [path.relative_to(self.root).as_posix() for path in sorted((self.root / "Assets").rglob("*.unity"))
                 if path.resolve().is_relative_to(self.root.resolve())
                 and path.relative_to(self.root).as_posix() not in draft.selected]
                if action == 0 else draft.selected.copy()
            )
            if not candidates:
                self.console.print("No scenes available for this action.")
                continue
            selected = self.choose("Choose scene", candidates)
            if selected is None or selected < 0:
                continue
            if action == 0:
                draft.selected.append(candidates[selected])
            elif action == 1:
                draft.selected.pop(selected)
            else:
                destination = selected + (-1 if action == 2 else 1)
                if 0 <= destination < len(draft.selected):
                    draft.selected[selected], draft.selected[destination] = draft.selected[destination], draft.selected[selected]

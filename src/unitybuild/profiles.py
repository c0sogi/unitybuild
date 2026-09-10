"""Read project-owned Unity Build Profile assets without starting the Editor.

Unity remains the authority for interpreting and applying the selected asset.
The YAML reader only supplies discovery, labels, and output format to the CLI.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

import yaml

from .platforms import PLATFORMS
from .projects import Project, ProjectError


class BuildProfile(StrEnum):
    RELEASE = "release"
    DEBUG = "debug"

    @property
    def configuration(self) -> str:
        return "development" if self == self.DEBUG else "release"


@dataclass(frozen=True)
class SavedBuildProfile:
    asset_path: str
    name: str
    platform: str
    development: bool
    app_bundle: bool = False
    export_project: bool = False

    @property
    def value(self) -> str:
        return self.name

    @property
    def configuration(self) -> str:
        return "development" if self.development else "release"


def discover_build_profiles(project: Project) -> list[SavedBuildProfile]:
    profiles = []
    root = project.root.resolve()
    for path in sorted((root / "Assets").rglob("*.asset")):
        if not path.resolve().is_relative_to(root):
            continue
        content = path.read_text(encoding="utf-8-sig", errors="replace")
        if "UnityEditor.Build.Profile.BuildProfile" not in content:
            continue
        try:
            content = re.sub(r"^%.*\n", "", content, flags=re.MULTILINE)
            content = re.sub(r"^--- !u!\d+ &[-\d]+.*$", "---", content, flags=re.MULTILINE)
            documents = list(yaml.safe_load_all(content))
            data = documents[0]["MonoBehaviour"]
            if not str(data.get("m_EditorClassIdentifier", "")).endswith("::UnityEditor.Build.Profile.BuildProfile"):
                continue
            platform = next((item.identifier for item in PLATFORMS.values()
                             if item.build_supported and item.profile_target == data["m_BuildTarget"]), None)
            if platform is None:
                raise ValueError(f"Unsupported Unity build target: {data['m_BuildTarget']}")
            rid = data["m_PlatformBuildProfile"]["rid"]
            settings = next(item["data"] for item in data["references"]["RefIds"] if item["rid"] == rid)
            profiles.append(
                SavedBuildProfile(
                    path.relative_to(root).as_posix(),
                    str(data.get("m_Name") or path.stem),
                    platform,
                    bool(settings["m_Development"]),
                    bool(settings.get("m_BuildAppBundle", False)),
                    bool(settings.get("m_ExportAsGoogleAndroidProject", False)),
                )
            )
        except (yaml.YAMLError, KeyError, TypeError, ValueError, StopIteration) as exc:
            raise ProjectError(f"Cannot read Unity Build Profile {path}: {exc}") from exc
    return sorted(profiles, key=lambda profile: (profile.development, profile.name.casefold(), profile.asset_path))


def resolve_build_profile(project: Project, selection: str) -> BuildProfile | SavedBuildProfile:
    profiles = discover_build_profiles(project)
    exact = [p for p in profiles if selection.casefold() in {p.name.casefold(), p.asset_path.casefold()}]
    if len(exact) == 1:
        return exact[0]
    if not exact and selection.casefold() in {"release", "debug", "development"}:
        if not profiles:
            return BuildProfile.DEBUG if selection.casefold() != "release" else BuildProfile.RELEASE
        exact = [p for p in profiles if p.development == (selection.casefold() != "release")]
    if len(exact) == 1:
        return exact[0]
    available = ", ".join(p.asset_path for p in profiles) or "release, debug (script builder)"
    raise ProjectError(f"Build profile {selection!r} is missing or ambiguous. Select a profile asset: {available}")

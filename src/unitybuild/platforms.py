"""Unity platform definitions shared by configuration, discovery and builds.

These are supported build implementations, not a list of installed modules.
Installation evidence is collected separately by targets.py.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Platform:
    identifier: str
    label: str
    hub_modules: tuple[str, ...]
    player_folder: str
    build_supported: bool = False
    default_output: str = ""
    profile_target: int | None = None
    android_formats: bool = False
    catalog_label: str = ""

    def native_output(self, output: str, *, app_bundle: bool, export_project: bool) -> str:
        if self.android_formats:
            return str(Path(output).with_suffix("" if export_project else ".aab" if app_bundle else ".apk"))
        return output

    def build_label(self, *, app_bundle: bool = False, export_project: bool = False) -> str:
        if self.android_formats:
            return "Android / Gradle export" if export_project else "Android / AAB" if app_bundle else self.label
        return self.label


PLATFORMS = {platform.identifier: platform for platform in (
    Platform("Android", "Android / APK", ("android",), "AndroidPlayer",
             True, "Builds/App.apk", 13, android_formats=True),
    Platform("StandaloneWindows64", "Windows / 64-bit", ("windows-mono", "windows-il2cpp"),
             "windowsstandalonesupport", True, "Builds/Windows/App.exe", 19, catalog_label="Windows / 64-bit"),
    Platform("StandaloneLinux64", "Linux / 64-bit", ("linux-mono", "linux-il2cpp"),
             "LinuxStandaloneSupport", True, "Builds/Linux/App", 24, catalog_label="Linux / 64-bit"),
    Platform("StandaloneOSX", "macOS", ("mac-mono", "mac-il2cpp"),
             "MacStandaloneSupport", True, "Builds/App.app", 2, catalog_label="macOS"),
    Platform("WSAPlayer", "Windows / UWP export", ("universal-windows-platform",),
             "MetroSupport", True, "Builds/UWP", 21),
    Platform("WebGL", "WebGL", ("webgl",), "WebGLSupport"),
    Platform("iOS", "iOS", ("ios",), "iOSSupport"),
    Platform("tvOS", "tvOS", ("appletv",), "AppleTVSupport"),
    Platform("VisionOS", "visionOS", ("visionos",), "VisionOSSupport"),
    Platform("StandaloneWindows64:Server", "Windows Server", ("windows-server",), "windowsstandalonesupport"),
    Platform("StandaloneLinux64:Server", "Linux Server", ("linux-server",), "LinuxStandaloneSupport"),
    Platform("StandaloneOSX:Server", "macOS Server", ("mac-server",), "MacStandaloneSupport"),
)}


def supported_platforms() -> tuple[str, ...]:
    return tuple(key for key, platform in PLATFORMS.items() if platform.build_supported)


def supports_build(identifier: str) -> bool:
    platform = PLATFORMS.get(identifier)
    return platform is not None and platform.build_supported


def default_output(identifier: str) -> str:
    platform = PLATFORMS.get(identifier)
    return platform.default_output if platform else ""


def platform_label(identifier: str) -> str:
    platform = PLATFORMS.get(identifier)
    return platform.label if platform else identifier


def output_label(output: str) -> str:
    """Infer a label for old history records that did not store a target."""
    suffix = Path(output).suffix.lower()
    if suffix:
        for platform in PLATFORMS.values():
            if suffix == Path(platform.default_output).suffix.lower():
                return platform.label
            if platform.android_formats and suffix == ".aab":
                return platform.build_label(app_bundle=True)
    return "Not recorded"

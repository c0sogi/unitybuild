"""Build artifact identity, incremental baselines, device state and history.

Callers supply their configuration fingerprint; no application files are assumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .projects import local_package_inputs


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_files(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted((item for item in paths if item.is_file()), key=lambda item: item.as_posix()):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = str(path.resolve())
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json_object(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def source_fingerprint(project: Path, *, hash_root: Path | None = None) -> str:
    """Fingerprint Android player inputs without hashing generated build/cache directories."""
    roots = (
        project / "Assets",
        project / "Packages",
        project / "ProjectSettings",
        *local_package_inputs(project),
    )
    inputs: list[Path] = []
    for root in roots:
        if root.is_file():
            inputs.append(root)
        elif root.is_dir():
            for directory, children, names in os.walk(root):
                children[:] = [
                    name
                    for name in children
                    if not name.startswith(".")
                    and name
                    not in {
                        "Library",
                        "Temp",
                        "Logs",
                        "Builds",
                        "Build",
                        "Obj",
                        "obj",
                        "bin",
                        "__pycache__",
                        "node_modules",
                    }
                ]
                inputs.extend(Path(directory) / name for name in names)
    return _hash_files(hash_root or project, inputs)


@dataclass(frozen=True)
class ArtifactStore:
    root: Path
    project: Path
    target_id: str
    package_name: str
    configuration_fingerprint: Callable[[], str]
    source_fingerprint: Callable[[], str]

    def development_baseline_path(self) -> Path:
        return self.root / "baseline.json"

    def release_artifact_path(self) -> Path:
        return self.root / "artifacts" / "release.json"

    def android_history_root(self) -> Path:
        return self.root / "history"

    def development_device_state_path(
        self,
        device_serial: str,
    ) -> Path:
        device_key = hashlib.sha256(device_serial.encode("utf-8")).hexdigest()[:16]
        return self.root / "devices" / f"{device_key}.json"

    def development_baseline_status(
        self,
        apk_path: Path,
    ) -> tuple[dict[str, object] | None, str]:
        state = _read_json_object(self.development_baseline_path())
        if state is None:
            return None, "no saved Development baseline"
        if state.get("schemaVersion") != 1:
            return None, "the saved Development baseline uses an unsupported schema"
        if state.get("target") != self.target_id or state.get("packageName") != self.package_name:
            return None, "the saved Development baseline belongs to another target"
        if state.get("apkPath") != str(apk_path):
            return None, "the Development APK path changed"
        if not apk_path.is_file():
            return None, "the Development APK is missing"
        saved_apk_hash = state.get("apkSha256")
        if isinstance(saved_apk_hash, str) and saved_apk_hash and saved_apk_hash != _sha256_file(apk_path):
            return None, "the Development APK content changed"
        if not (self.project / "Library" / "Bee").is_dir():
            return None, "the Unity Library/Bee incremental cache is missing"
        fingerprint = self.configuration_fingerprint()
        if state.get("configurationFingerprint") != fingerprint:
            return None, "a build configuration, package, Player setting, or Android plug-in changed"
        return state, "compatible"

    def write_development_baseline(
        self,
        apk_path: Path,
    ) -> dict[str, object]:
        fingerprint = self.configuration_fingerprint()
        baseline_id = hashlib.sha256(
            f"{self.target_id}\0{self.package_name}\0{fingerprint}\0{time.time_ns()}".encode()
        ).hexdigest()
        state: dict[str, object] = {
            "schemaVersion": 1,
            "baselineId": baseline_id,
            "target": self.target_id,
            "packageName": self.package_name,
            "configuration": "development",
            "incrementalMode": "full",
            "apkPath": str(apk_path),
            "apkSha256": _sha256_file(apk_path),
            "configurationFingerprint": fingerprint,
            "sourceFingerprint": self.source_fingerprint(),
            "createdAtUnixSeconds": int(time.time()),
        }
        _write_json_object(self.development_baseline_path(), state)
        return state

    def device_has_development_baseline(
        self,
        device_serial: str,
        baseline_id: object,
    ) -> bool:
        state = _read_json_object(self.development_device_state_path(device_serial))
        return bool(
            state
            and state.get("deviceSerial") == device_serial
            and state.get("baselineId") == baseline_id
            and state.get("packageName") == self.package_name
            and state.get("artifactKind") in (None, "development")
        )

    def saved_development_device_serials(
        self,
        baseline_id: object,
    ) -> tuple[str, ...]:
        devices_path = self.root / "devices"
        if not devices_path.is_dir():
            return ()

        serials: set[str] = set()
        for state_path in devices_path.glob("*.json"):
            state = _read_json_object(state_path)
            if not state:
                continue
            serial = state.get("deviceSerial")
            if (
                state.get("schemaVersion") == 1
                and state.get("baselineId") == baseline_id
                and state.get("packageName") == self.package_name
                and isinstance(serial, str)
                and serial.strip()
            ):
                serials.add(serial.strip())
        return tuple(sorted(serials))

    def write_development_device_state(
        self,
        device_serial: str,
        baseline_id: object,
        *,
        apk_sha256: str = "",
        source_fingerprint: str = "",
        operation: str = "install-baseline",
    ) -> None:
        _write_json_object(
            self.development_device_state_path(device_serial),
            {
                "schemaVersion": 1,
                "deviceSerial": device_serial,
                "packageName": self.package_name,
                "artifactKind": "development",
                "baselineId": baseline_id,
                "apkSha256": apk_sha256,
                "appliedSourceFingerprint": source_fingerprint,
                "lastOperation": operation,
                "installedAtUnixSeconds": int(time.time()),
            },
        )

    def write_release_artifact(
        self,
        apk_path: Path,
        source_fingerprint: str,
    ) -> dict[str, object]:
        state: dict[str, object] = {
            "schemaVersion": 1,
            "artifactKind": "release",
            "target": self.target_id,
            "packageName": self.package_name,
            "apkPath": str(apk_path),
            "apkSha256": _sha256_file(apk_path),
            "sourceFingerprint": source_fingerprint,
            "builtAtUnixSeconds": int(time.time()),
        }
        _write_json_object(self.release_artifact_path(), state)
        return state

    def release_artifact_status(
        self,
        apk_path: Path,
        source_fingerprint: str,
    ) -> tuple[dict[str, object] | None, str]:
        state = _read_json_object(self.release_artifact_path())
        if state is None:
            return None, "no saved Release artifact"
        if state.get("schemaVersion") != 1:
            return None, "the saved Release artifact uses an unsupported schema"
        if state.get("target") != self.target_id or state.get("packageName") != self.package_name:
            return None, "the saved Release artifact belongs to another target"
        if state.get("apkPath") != str(apk_path) or not apk_path.is_file():
            return None, "the saved Release APK is missing or moved"
        if state.get("sourceFingerprint") != source_fingerprint:
            return None, "Android player source changed since the Release build"
        if state.get("apkSha256") != _sha256_file(apk_path):
            return None, "the saved Release APK content changed"
        return state, "compatible"

    def write_release_device_state(
        self,
        device_serial: str,
        artifact: Mapping[str, object],
    ) -> None:
        _write_json_object(
            self.development_device_state_path(device_serial),
            {
                "schemaVersion": 1,
                "deviceSerial": device_serial,
                "packageName": self.package_name,
                "artifactKind": "release",
                "apkSha256": artifact.get("apkSha256", ""),
                "appliedSourceFingerprint": artifact.get("sourceFingerprint", ""),
                "lastOperation": "install-release",
                "installedAtUnixSeconds": int(time.time()),
            },
        )

    def record_android_history(
        self,
        *,
        command: str,
        operation: str,
        device_serial: str,
        source_fingerprint: str,
        apk_path: Path,
        baseline_id: object = "",
    ) -> Path:
        timestamp_ns = time.time_ns()
        event = {
            "schemaVersion": 1,
            "timestampUnixMilliseconds": timestamp_ns // 1_000_000,
            "command": command,
            "operation": operation,
            "result": "succeeded",
            "target": self.target_id,
            "packageName": self.package_name,
            "deviceSerial": device_serial,
            "sourceFingerprint": source_fingerprint,
            "apkPath": str(apk_path),
            "apkSha256": _sha256_file(apk_path) if apk_path.is_file() else "",
            "baselineId": baseline_id,
        }
        path = self.android_history_root() / f"{timestamp_ns}-{operation}.json"
        _write_json_object(path, event)
        _write_json_object(self.root / "latest.json", event)
        return path

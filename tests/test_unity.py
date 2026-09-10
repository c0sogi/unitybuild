import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from unitybuild import execution, unity


def version_result(version: str, *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["Unity", "-version"],
        returncode=returncode,
        stdout=f"{version}\n",
        stderr="",
    )


class UnityVersionTest(unittest.TestCase):
    def test_reads_exact_project_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            version_file = Path(tmp) / "ProjectVersion.txt"
            version_file.write_text(
                "m_EditorVersion: 2022.3.17f1\nm_EditorVersionWithRevision: 2022.3.17f1 (4fc78088f837)\n",
                encoding="utf-8",
            )

            self.assertEqual(
                unity.read_project_version(version_file),
                "2022.3.17f1",
            )

    def test_probe_uses_only_version_and_accepts_exact_output(self) -> None:
        editor = Path("C:/Unity/Editor/Unity.exe")
        with patch(
            "unitybuild.unity.subprocess.run",
            return_value=version_result("2022.3.17f1"),
        ) as run:
            actual = unity.probe_unity_editor_version(editor)

        self.assertEqual(actual, "2022.3.17f1")
        command = run.call_args.args[0]
        self.assertEqual(command, [str(editor), "-version"])
        self.assertNotIn("-projectPath", command)

    def test_probe_rejects_malformed_and_missing_version_output(self) -> None:
        editor = Path("C:/Unity/Editor/Unity.exe")
        for output in ("Unity Editor\n", ""):
            with (
                self.subTest(output=output),
                patch(
                    "unitybuild.unity.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=["Unity", "-version"],
                        returncode=0,
                        stdout=output,
                        stderr="",
                    ),
                ),
                self.assertRaisesRegex(
                    unity.UnityVersionError,
                    "did not report a recognizable editor version",
                ),
            ):
                unity.probe_unity_editor_version(editor)

    def test_version_mismatch_explains_that_project_was_not_opened(self) -> None:
        editor = Path("C:/Unity/Editor/Unity.exe")
        version_file = Path("C:/Project/ProjectSettings/ProjectVersion.txt")
        with (
            patch(
                "unitybuild.unity.subprocess.run",
                return_value=version_result("6000.3.14f1"),
            ),
            self.assertRaises(unity.UnityVersionError) as raised,
        ):
            unity.require_unity_editor_version(
                editor,
                "2022.3.17f1",
                version_file=version_file,
                source="--unity-path",
            )

        message = str(raised.exception)
        self.assertIn("editor reports: 6000.3.14f1", message)
        self.assertIn("project requires: 2022.3.17f1", message)
        self.assertIn("No Unity project was opened", message)


# Editor discovery is owned here; adapters only supply their project and policy.


@pytest.mark.parametrize("version", ["2022.3.17f1", "6000.3.14f1"])
def test_resolver_uses_exact_hub_version_after_stale_override_and_wrong_path(tmp_path, monkeypatch, version):
    project = tmp_path / "Project"
    (project / "ProjectSettings").mkdir(parents=True)
    (project / "ProjectSettings/ProjectVersion.txt").write_text(f"m_EditorVersion: {version}\n")
    wrong = tmp_path / "wrong-editor"
    exact = tmp_path / "exact-editor"
    wrong.touch()
    exact.touch()
    monkeypatch.setenv("UNITY_EDITOR_PATH", str(tmp_path / "removed"))
    monkeypatch.setattr(execution.shutil, "which", lambda _: str(wrong))
    monkeypatch.setattr(execution, "hub_unity_candidates", lambda _: [exact])
    with patch(
        "unitybuild.unity.subprocess.run",
        side_effect=lambda args, **_: version_result(version if Path(args[0]) == exact else "2021.3.1f1"),
    ):
        assert execution.resolve_unity_editor(project) == exact.resolve()


@pytest.mark.parametrize("explicit", [True, False])
def test_resolver_rejects_wrong_explicit_or_environment_editor_without_fallback(tmp_path, monkeypatch, explicit):
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.17f1\n")
    editor = tmp_path / "editor"
    editor.touch()
    monkeypatch.setenv("UNITY_EDITOR_PATH", "" if explicit else str(editor))
    with (
        patch("unitybuild.unity.subprocess.run", return_value=version_result("6000.3.14f1")),
        patch.object(execution, "hub_unity_candidates", side_effect=AssertionError("No fallback")),
    ):
        with pytest.raises(unity.UnityVersionError, match="No Unity project was opened"):
            execution.resolve_unity_editor(tmp_path, str(editor) if explicit else "")


def test_resolver_rejects_missing_explicit_editor_before_probe(tmp_path):
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.17f1\n")
    with patch.object(execution, "hub_unity_candidates", side_effect=AssertionError("No fallback")):
        with pytest.raises(FileNotFoundError, match="No Unity project was opened"):
            execution.resolve_unity_editor(tmp_path, str(tmp_path / "missing"))

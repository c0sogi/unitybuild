import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from unitybuild import unity


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

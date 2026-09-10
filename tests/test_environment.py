import subprocess
from pathlib import Path
from unittest.mock import patch

from unitybuild.execution import prepare_unity_environment


def test_packaged_host_uses_real_package_temp_without_java_flags(tmp_path):
    local = tmp_path / "Local"
    source = {
        "LOCALAPPDATA": str(local),
        "TEMP": str(local / "Temp"),
        "TMP": str(local / "Other"),
        "JAVA_TOOL_OPTIONS": "-Duser.language=en",
    }
    original = source.copy()
    with patch("unitybuild.execution.windows_package_family", return_value="Example.App_123"):
        result = prepare_unity_environment(source)
    expected = local / "Packages/Example.App_123/TempState"
    assert Path(result["TEMP"]) == expected
    assert Path(result["TMP"]) == expected
    assert expected.is_dir()
    assert source == original
    assert result["JAVA_TOOL_OPTIONS"] == source["JAVA_TOOL_OPTIONS"]
    assert "JDK_JAVA_OPTIONS" not in result
    # Exercise a real child, rather than only checking the returned dictionary.
    import sys

    child = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ['TEMP'])"],
        env=result,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(child.stdout.strip()) == expected


def test_unpacked_host_preserves_temporary_directories(tmp_path):
    source = {"LOCALAPPDATA": str(tmp_path), "TEMP": str(tmp_path / "Temp")}
    with patch("unitybuild.execution.windows_package_family", return_value=None):
        result = prepare_unity_environment(source)
    assert result["TEMP"] == source["TEMP"]
    assert not (tmp_path / "Packages").exists()


def test_packaged_host_preserves_custom_and_already_native_paths(tmp_path):
    local = tmp_path / "Local"
    source = {
        "LOCALAPPDATA": str(local),
        "TEMP": str(tmp_path / "custom-temp"),
        "TMP": str(local / "Packages/Example.App_123/TempState"),
    }
    with patch("unitybuild.execution.windows_package_family", return_value="Example.App_123"):
        result = prepare_unity_environment(source)
    assert result["TEMP"] == source["TEMP"]
    assert result["TMP"] == source["TMP"]


def test_missing_environment_paths_are_not_invented():
    with patch("unitybuild.execution.windows_package_family", return_value="Example.App_123"):
        result = prepare_unity_environment({})
    assert "TEMP" not in result
    assert "TMP" not in result

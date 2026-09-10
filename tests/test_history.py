import io
import json
import re

from rich.console import Console

from unitybuild.history import failure_reason, render_history


def test_history_decodes_legacy_unicode_and_explains_failure(tmp_path):
    logs = tmp_path / "Logs/unitybuild"
    logs.mkdir(parents=True)
    log = logs / "build.log"
    log.write_text("BuildFailedException: Enable at least one scene in Unity Build Settings.\n")
    record = {
        "profile": "릴리즈", "status": "failed", "output": str(tmp_path / "한글 게임.exe"), "log": str(log),
        "started_at": "2026-09-10T11:41:31+00:00", "finished_at": "2026-09-10T11:41:59+00:00",
        "error": "Unity build exited with code 1",
    }
    (logs / "20260910.json").write_text(json.dumps(record), encoding="utf-8")
    (logs / "20260909.json").write_text("{broken")
    output = io.StringIO()
    render_history(Console(file=output, width=140), tmp_path, "Logs/unitybuild")
    shown = output.getvalue()
    assert "릴리즈" in shown and "한글 게임.exe" in shown and not re.search(r"\\u[0-9a-fA-F]{4}", shown)
    assert "Failed" in shown and "00:00:28" in shown and "Windows / 64-bit" in shown
    assert "Enable at least one scene" in shown and "Cannot read 20260909.json" in shown


def test_compilation_error_precedes_generic_builder_exception():
    text = "Assets/게임.cs(3,4): error CS0234: Missing namespace\nBuildFailedException: Build failed: Failed\n"
    assert failure_reason(text) == "Assets/게임.cs(3,4): error CS0234: Missing namespace"


def test_cancelled_record_without_dates_or_log_is_readable(tmp_path):
    (tmp_path / "cancel.json").write_text('{"status": "cancelled", "profile": "debug"}')
    output = io.StringIO()
    render_history(Console(file=output), tmp_path, ".")
    assert "Cancelled by user" in output.getvalue()

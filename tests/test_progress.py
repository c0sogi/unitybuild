from __future__ import annotations

import io
import subprocess
import sys
from unittest.mock import patch

import pytest
from rich.console import Console

from unitybuild.progress import BuildMonitor, FileTail, run_logged_process


@pytest.mark.parametrize("encoding", ["cp949", "ascii", "utf-8"])
@pytest.mark.parametrize("rich", [False, True])
def test_console_encoding_does_not_abort_process_or_change_full_log(tmp_path, monkeypatch, encoding, rich):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("UNITYBUILD_LOG_PARENT", raising=False)
    monkeypatch.delenv("UNITYBUILD_PLAIN", raising=False)
    raw = io.BytesIO()
    output = io.TextIOWrapper(raw, encoding=encoding, errors="strict")
    log = tmp_path / "build.log"
    message = "빌드 완료 \ufffd \U0001f680"
    result = run_logged_process(
        [sys.executable, "-c", f"print({message!r})"],
        cwd=tmp_path,
        label="Build \U0001f680",
        log_path=log,
        console=Console(file=output, force_terminal=rich, legacy_windows=False),
    )
    output.flush()
    assert result.returncode == 0
    assert message in result.stdout and message in log.read_text(encoding="utf-8")
    assert message.encode(encoding, errors="backslashreplace") in raw.getvalue()
    assert output.encoding == encoding and output.errors == "strict"


def test_console_error_policy_is_restored_after_failure(tmp_path):
    output = io.TextIOWrapper(io.BytesIO(), encoding="cp949", errors="strict")
    with pytest.raises(RuntimeError, match="test failure"):
        with BuildMonitor("Build", console=Console(file=output)) as monitor:
            monitor.feed("\ufffd\n")
            raise RuntimeError("test failure")
    assert output.errors == "strict"


def test_output_and_unity_log_are_visible_before_process_finishes(tmp_path):
    ack = tmp_path / "observed"
    unity = tmp_path / "Unity.log"
    full = tmp_path / "session.log"

    class AcknowledgeOutput(io.StringIO):
        def write(self, text):
            result = super().write(text)
            if "Unity is compiling" in self.getvalue():
                ack.touch()
            return result

    output = AcknowledgeOutput()
    script = """
import sys, time
from pathlib import Path
ack, log = map(Path, sys.argv[1:])
print('stdout started', flush=True)
print('stderr started', file=sys.stderr, flush=True)
log.write_text('Unity is compiling\\n', encoding='utf-8')
deadline = time.monotonic() + 5
while not ack.exists() and time.monotonic() < deadline:
    time.sleep(0.02)
if not ack.exists():
    sys.exit(9)
with log.open('a', encoding='utf-8') as stream:
    stream.write('[red]literal log text[/red]\\n')
print('finished', flush=True)
"""
    result = run_logged_process(
        [sys.executable, "-u", "-c", script, str(ack), str(unity)],
        cwd=tmp_path,
        label="Test build",
        log_path=full,
        tail_paths=(unity,),
        console=Console(file=output),
    )
    assert result.returncode == 0
    text = full.read_text(encoding="utf-8")
    assert all(value in text for value in ("stdout started", "stderr started", "Unity is compiling", "finished"))
    assert "[red]literal log text[/red]" in text


def test_file_tail_handles_partial_unicode_and_truncation(tmp_path):
    path = tmp_path / "Unity.log"
    tail = FileTail(path)
    assert tail.read() == ""
    encoded = "빌드\n".encode()
    path.write_bytes(encoded[:2])
    assert tail.read() == ""
    with path.open("ab") as stream:
        stream.write(encoded[2:])
    assert tail.read() == "빌드\n"
    path.write_text("new", encoding="utf-8")
    assert tail.read() == "new"
    assert tail.read() == ""


def test_silence_shows_elapsed_time_without_claiming_failure():
    output = io.StringIO()
    with patch("unitybuild.progress.time.monotonic", return_value=10):
        monitor = BuildMonitor("Compile", console=Console(file=output, width=100))
    with patch("unitybuild.progress.time.monotonic", return_value=75):
        monitor.console.print(monitor.render())
    assert "00:01:05" in output.getvalue()
    assert "may still be working" in output.getvalue()
    assert "Failed" not in output.getvalue()


def test_nonzero_exit_keeps_failure_output_and_full_log(tmp_path):
    log = tmp_path / "failed.log"
    result = run_logged_process(
        [sys.executable, "-c", "import sys; print('compile error', flush=True); sys.exit(7)"],
        cwd=tmp_path,
        label="Failing build",
        log_path=log,
        console=Console(file=io.StringIO()),
    )
    assert result.returncode == 7
    assert "compile error" in log.read_text(encoding="utf-8")


def test_ctrl_c_stops_the_owned_process(tmp_path):
    launched: list[subprocess.Popen] = []
    original = subprocess.Popen

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        if args[0][0] == sys.executable:
            launched.append(process)
        return process

    with (
        patch("unitybuild.progress.subprocess.Popen", side_effect=launch),
        patch.object(BuildMonitor, "tick", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        run_logged_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            label="Cancel test",
            console=Console(file=io.StringIO()),
        )
    assert launched and launched[0].poll() is not None


def test_rich_logs_are_appended_once_and_never_replayed_by_status(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("UNITYBUILD_LOG_PARENT", raising=False)
    monkeypatch.delenv("UNITYBUILD_PLAIN", raising=False)
    output = io.StringIO()
    console = Console(file=output, force_terminal=True, legacy_windows=False, width=90, height=18)
    with BuildMonitor("Compile", console=console) as monitor:
        monitor.feed("".join(f"log-entry-{index:03d}\n" for index in range(80)))
        monitor.feed("unfinished-")
        monitor.feed("log-entry")
        for _ in range(20):
            monitor.tick(force=True)
        visible = Console(file=io.StringIO(), width=90)
        with visible.capture() as capture:
            visible.print(monitor.render())
        assert "log-entry" not in capture.get()
        assert "Elapsed" in capture.get()
    for index in range(80):
        assert output.getvalue().count(f"log-entry-{index:03d}") == 1
    assert output.getvalue().count("unfinished-log-entry") == 1
    assert "Elapsed" in output.getvalue()


def test_status_refresh_is_limited_to_once_per_second_and_finishes_immediately(monkeypatch):
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("UNITYBUILD_LOG_PARENT", raising=False)
    monkeypatch.delenv("UNITYBUILD_PLAIN", raising=False)
    console = Console(file=io.StringIO(), force_terminal=True, legacy_windows=False)
    with (
        patch("unitybuild.progress.time.monotonic", return_value=10),
        BuildMonitor("Compile", console=console) as monitor,
    ):
        assert monitor.live is not None
        with patch.object(monitor.live, "update") as update:
            for _ in range(100):
                monitor.tick("Still compiling")
            update.assert_not_called()
            with patch("unitybuild.progress.time.monotonic", return_value=11):
                monitor.tick()
                monitor.tick()
            assert update.call_count == 1
            monitor.state = "Completed"
            monitor.tick(force=True)
            assert update.call_count == 2
            assert "Completed" in update.call_args.args[0].plain

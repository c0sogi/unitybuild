"""Real OS locks and subprocess cancellation, including Unicode paths."""

import io
import os
import signal
import subprocess
import sys
import time
from unittest.mock import patch

import pytest
from rich.console import Console

from unitybuild.build_session import BuildSession, BuildSessionError, project_lock_is_held, select_build_session
from unitybuild.progress import BuildMonitor, run_logged_process

LOCK_KINDS = ["windows"] if os.name == "nt" else ["flock", "lockf"]

LOCK_HOLDER = r"""
import ctypes, os, sys, time
from pathlib import Path
project = Path(sys.argv[1])
if os.name == 'nt':
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                              ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(project / 'Temp/UnityLockfile'), 0xC0000000, 0, None, 4, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
else:
    import fcntl
    stream = (project / 'Temp/UnityLockfile').open('a+b')
    getattr(fcntl, sys.argv[2])(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
(project / 'ready').write_text('ready')
print('lock acquired', flush=True)
if len(sys.argv) == 3:
    time.sleep(60)
if os.name == 'nt':
    kernel.CloseHandle(handle)
else:
    stream.close()
"""


@pytest.mark.parametrize("name", ["Project", "(테스트) 한글 프로젝트"])
@pytest.mark.parametrize("lock_kind", LOCK_KINDS)
def test_cancel_releases_real_lock_and_allows_repeated_retry(tmp_path, name, lock_kind):
    project = tmp_path / name
    (project / "Temp").mkdir(parents=True)
    lock = project / "Temp/UnityLockfile"
    ready = project / "ready"

    def cancel_after_lock_acquired(self, *args, **kwargs):
        if self.state == "Running" and ready.exists():
            assert project_lock_is_held(project)
            with pytest.raises(BuildSessionError):
                select_build_session(project, BuildSession.BACKGROUND, interactive=False)
            raise KeyboardInterrupt

    # Two cancellations must not poison a subsequent launch. Use a real process
    # holding a real OS lock, not a mocked process.poll() or file existence.
    with patch("unitybuild.build_session.discover_editors", return_value=[]):
        for _ in range(2):
            ready.unlink(missing_ok=True)
            with patch.object(BuildMonitor, "tick", cancel_after_lock_acquired), pytest.raises(KeyboardInterrupt):
                run_logged_process(
                    [sys.executable, "-u", "-c", LOCK_HOLDER, str(project), lock_kind],
                    cwd=project,
                    label="Cancellation test",
                    console=Console(file=io.StringIO()),
                )
            assert lock.exists()
            original = (lock.read_bytes(), lock.stat().st_mtime_ns)
            assert not project_lock_is_held(project)
            assert not select_build_session(project, BuildSession.BACKGROUND, interactive=False)
            assert (lock.read_bytes(), lock.stat().st_mtime_ns) == original
        result = run_logged_process(
            [sys.executable, "-u", "-c", LOCK_HOLDER, str(project), lock_kind, "finish"],
            cwd=project,
            label="Retry test",
            console=Console(file=io.StringIO()),
        )
        assert result.returncode == 0
        assert "lock acquired" in result.stdout
        assert not project_lock_is_held(project)


def test_probe_does_not_create_a_missing_lock(tmp_path):
    assert not project_lock_is_held(tmp_path)
    assert not (tmp_path / "Temp").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups and SIGTERM")
@pytest.mark.parametrize("leader_exits_first", [False, True])
def test_cancel_stops_descendant_that_ignores_term(tmp_path, leader_exits_first):
    """The leader exiting must not leave a child holding the project lock."""
    (tmp_path / "Temp").mkdir()
    ready = tmp_path / "ready"
    group = tmp_path / "group"
    child = """
import fcntl, os, signal, sys, time
from pathlib import Path
project = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with (project / 'Temp/UnityLockfile').open('a+b') as stream:
    fcntl.lockf(stream, fcntl.LOCK_EX)
    (project / 'ready').write_text(str(os.getpid()))
    time.sleep(60)
"""
    parent = """
import os, subprocess, sys, time
from pathlib import Path
project = Path(sys.argv[1])
(project / 'group').write_text(str(os.getpid()))
subprocess.Popen([sys.executable, '-c', sys.argv[2], str(project)])
if sys.argv[3] == 'True':
    sys.exit(0)
time.sleep(60)
"""
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    ready_since = None

    def cancel_when_ready(self, *args, **kwargs):
        nonlocal ready_since
        if self.state == "Running" and ready.exists():
            ready_since = ready_since or time.monotonic()
            # Allow the leader to exit before triggering cancellation.
            if time.monotonic() - ready_since > 0.2:
                assert project_lock_is_held(tmp_path)
                raise KeyboardInterrupt

    try:
        with patch.object(BuildMonitor, "tick", cancel_when_ready), pytest.raises(KeyboardInterrupt):
            run_logged_process(
                [sys.executable, "-c", parent, str(tmp_path), child, str(leader_exits_first)],
                cwd=tmp_path,
                label="Cancel descendants",
                console=Console(file=io.StringIO()),
            )
        deadline = time.monotonic() + 3
        while project_lock_is_held(tmp_path) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not project_lock_is_held(tmp_path)
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=3)
        if os.name != "nt" and group.exists():
            try:
                os.killpg(int(group.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

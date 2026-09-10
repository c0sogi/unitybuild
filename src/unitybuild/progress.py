"""Stream process output and growing log files without hiding silent build stages."""

from __future__ import annotations

import codecs
import io
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Self

import psutil
from rich.console import Console
from rich.live import Live
from rich.text import Text

PARENT_LOG_ENV = "UNITYBUILD_LOG_PARENT"


def session_log(directory: Path, label: str) -> Path:
    stem = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "build"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return directory / f"{stamp}-{stem}-{uuid.uuid4().hex[:6]}.log"


class FileTail:
    """Read appended UTF-8 bytes, including partial characters and file truncation."""

    def __init__(self, path: Path, *, offset: int = 0) -> None:
        self.path = path
        self.offset = offset
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.identity: tuple[int, int] | None = None

    def read(self) -> str:
        try:
            with self.path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                identity = (info.st_dev, info.st_ino)
                if self.identity is not None and identity != self.identity:
                    self.offset = 0
                    self.decoder.reset()
                self.identity = identity
                stream.seek(0, 2)
                if stream.tell() < self.offset:
                    self.offset = 0
                    self.decoder.reset()
                stream.seek(self.offset)
                chunk = stream.read(256 * 1024)
                self.offset = stream.tell()
        except OSError:
            return ""
        return self.decoder.decode(chunk)


def duration(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


class BuildMonitor:
    def __init__(self, label: str, *, log_path: Path | None = None, console: Console | None = None) -> None:
        self.label = label
        self.log_path = log_path
        self.console = console or Console(highlight=False)
        self.started = time.monotonic()
        self.last_output: float | None = None
        self.last_heartbeat = self.started
        self.last_refresh = self.started
        self.lines: deque[str] = deque(maxlen=2000)
        self.partial = ""
        self.line_count = 0
        self.state = "Running"
        self.message = "Waiting for output"
        self.live: Live | None = None
        self.stream: IO[str] | None = None
        self._console_stream: io.TextIOWrapper | None = None
        self.nested = bool(os.environ.get(PARENT_LOG_ENV))
        self.rich = (
            self.console.is_terminal
            and not self.console.is_dumb_terminal
            and not self.console.legacy_windows
            and not self.nested
            and os.environ.get("UNITYBUILD_PLAIN") != "1"
        )

    def __enter__(self) -> Self:
        stream = self.console.file
        if isinstance(stream, io.TextIOWrapper) and stream.errors == "strict":
            # Keep the terminal's encoding. Escape unrepresentable characters only
            # on screen; the persisted UTF-8 log and captured output stay intact.
            stream.reconfigure(errors="backslashreplace")
            self._console_stream = stream
        try:
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                self.stream = self.log_path.open("w", encoding="utf-8")
            if self.rich:
                self.live = Live(self.render(), console=self.console, auto_refresh=False, vertical_overflow="crop")
                self.live.start(refresh=True)
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def feed(self, text: str) -> None:
        if not text:
            return
        self.last_output = time.monotonic()
        if self.stream:
            self.stream.write(text)
            self.stream.flush()
        if not self.rich:
            self.console.print(text, end="", markup=False, highlight=False, soft_wrap=True)
            self.console.file.flush()
        combined = self.partial + text
        fragments = combined.splitlines(keepends=True)
        self.partial = ""
        appended = Text()
        for fragment in fragments:
            if fragment.endswith(("\n", "\r")):
                line = fragment.rstrip("\r\n")
                self.lines.append(line)
                self.line_count += 1
                if self.rich:
                    style = "red" if re.search(r"\b(error|exception|failed)\b", line, re.IGNORECASE) else ""
                    if not style and re.search(r"\bwarning\b", line, re.IGNORECASE):
                        style = "yellow"
                    appended.append(line + "\n", style=style)
            else:
                # Bound a malformed or exceptionally long unterminated log line.
                if self.rich and len(fragment) > 8192:
                    appended.append(fragment[:-8192] + "\n")
                self.partial = fragment[-8192:]
        if self.rich and appended:
            # Live keeps only the status below this append-only log stream.
            self.console.print(appended, end="", soft_wrap=True)

    def render(self) -> Text:
        now = time.monotonic()
        silent = now - (self.last_output or self.started)
        activity = f"Last output {duration(silent)} ago" if self.last_output else "No output received yet"
        status = Text(
            f"{self.state} | Elapsed {duration(now - self.started)} | {activity}\n",
            style="bold cyan",
            no_wrap=True,
            overflow="ellipsis",
        )
        if self.state == "Running" and silent >= 30:
            status.append("No recent output; the build may still be working.", style="yellow")
        else:
            status.append(f"{self.label}: {self.message}", style="dim")
        return status

    def tick(self, message: str | None = None, *, force: bool = False) -> None:
        if message:
            self.message = message
        if self.live:
            now = time.monotonic()
            if force or now - self.last_refresh >= 1:
                self.live.update(self.render(), refresh=True)
                self.last_refresh = now
        elif not self.nested and time.monotonic() - self.last_heartbeat >= 10:
            now = time.monotonic()
            silence = now - (self.last_output or self.started)
            self.console.print(
                f"[{self.state}] {self.label}: elapsed {duration(now - self.started)}, "
                f"last output {duration(silence)} ago; {self.message}",
                markup=False,
            )
            self.last_heartbeat = now

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            self._finish(exc_type)
        finally:
            if self._console_stream is not None:
                self._console_stream.reconfigure(errors="strict")
                self._console_stream = None

    def _finish(self, exc_type) -> None:
        if exc_type:
            self.state = "Cancelled" if issubclass(exc_type, KeyboardInterrupt) else "Failed"
        elif self.state == "Running":
            self.state = "Completed"
        try:
            if self.rich and self.partial:
                self.console.print(Text(self.partial), soft_wrap=True)
            self.tick(force=True)
        finally:
            try:
                if self.live:
                    self.live.stop()
            finally:
                if self.stream:
                    self.stream.close()
        if self.log_path and not self.nested:
            self.console.print(f"Full log: {self.log_path}", style="dim", markup=False)


def _signal_process_group(group: int, sig: int) -> bool:
    """Signal an owned group; distinguish absent/terminated members from denial."""
    try:
        getattr(os, "killpg")(group, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin can return EPERM for a group containing only zombies. Do not
        # suppress a genuine denial: confirm that no live member remains first.
        for pid in psutil.pids():
            try:
                if getattr(os, "getpgid")(pid) == group and psutil.Process(pid).status() not in (
                    psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD,
                ):
                    raise
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
        return False


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Cancel only the process tree started for this command."""
    if os.name == "nt":
        if process.poll() is not None:
            return
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        # run_logged_process creates a new session, so this group belongs only
        # to the build. The leader can exit before children release their locks.
        if not _signal_process_group(process.pid, signal.SIGTERM):
            process.wait(timeout=3)
            return
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            process.poll()  # Reap the leader so it cannot keep the group alive.
            if not _signal_process_group(process.pid, 0):
                break
            time.sleep(0.05)
        else:
            _signal_process_group(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def run_logged_process(
    command: list[str],
    *,
    cwd: Path,
    label: str,
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
    tail_paths: tuple[Path, ...] = (),
    console: Console | None = None,
) -> subprocess.CompletedProcess[str]:
    """Return the exit code and bounded output tail; persist the full stream to log_path."""
    environment = (os.environ if env is None else env).copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment[PARENT_LOG_ENV] = "1"
    chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=256)
    stopped = threading.Event()
    tails = [FileTail(path) for path in tail_paths]
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    with BuildMonitor(label, log_path=log_path, console=console) as monitor:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        assert process.stdout is not None
        stdout = process.stdout

        def read_output() -> None:
            try:
                while not stopped.is_set():
                    chunk = os.read(stdout.fileno(), 65536)
                    while not stopped.is_set():
                        try:
                            chunks.put(chunk or None, timeout=0.1)
                            break
                        except queue.Full:
                            continue
                    if not chunk:
                        break
            finally:
                stdout.close()

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        eof = False
        ended_at: float | None = None
        try:
            while True:
                for _ in range(256):
                    try:
                        chunk = chunks.get_nowait()
                    except queue.Empty:
                        break
                    if chunk is None:
                        eof = True
                    else:
                        monitor.feed(decoder.decode(chunk))
                for tail in tails:
                    monitor.feed(tail.read())
                monitor.tick(f"{monitor.line_count} log lines received")
                result = process.poll()
                if result is not None:
                    ended_at = ended_at or time.monotonic()
                    if eof or time.monotonic() - ended_at > 1:
                        break
                time.sleep(0.1)
            monitor.feed(decoder.decode(b"", final=True))
            for tail in tails:
                while text := tail.read():
                    monitor.feed(text)
            monitor.state = "Completed" if result == 0 else f"Failed (exit {result})"
            output = "\n".join([*monitor.lines, *([monitor.partial] if monitor.partial else [])])
            return subprocess.CompletedProcess(command, result, output, "")
        except BaseException:
            stop_process_tree(process)
            raise
        finally:
            stopped.set()
            reader.join(timeout=1)


def run_quiet_process(
    command: list[str],
    *,
    cwd: Path,
    label: str = "Build",
    env: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    with open(os.devnull, "w", encoding="utf-8") as stream:
        return run_logged_process(
            command,
            cwd=cwd,
            label=label,
            env=env,
            log_path=log_path,
            console=Console(file=stream, force_terminal=False),
        )

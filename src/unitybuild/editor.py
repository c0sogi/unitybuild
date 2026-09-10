"""Request/response transport for a cooperating Unity Editor bridge.

The Unity project owns the operation handler. The caller supplies its payload and
stage descriptions; no application build or verification methods are assumed.
"""

from __future__ import annotations

import io
import json
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, cast

from rich.console import Console

from .progress import BuildMonitor, FileTail, session_log


class EditorBuildError(ValueError):
    pass


class _NullOutput(io.TextIOBase):
    def write(self, text: str) -> int:
        return len(text)


def read_response(path: Path, request_id: str) -> dict[str, object] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or document.get("requestId") != request_id:
        return None
    return document


def run_editor_request(
    request_path: Path,
    response_path: Path,
    output_path: Path,
    *,
    payload: Mapping[str, object],
    steps: list[tuple[str, str, str]],
    display_name: str = "Unity",
    verbose: bool = False,
    start_timeout: float = 120,
    timeout: float = 3600,
    emit: Callable[[str], None] = lambda message: None,
    console: Console | None = None,
    response_reader: Callable[[Path, str], dict[str, object] | None] = read_response,
) -> None:
    console = console or Console(file=cast(IO[str], _NullOutput()), force_terminal=False)
    if not steps or start_timeout <= 0 or timeout <= 0:
        raise ValueError("At least one stage and positive timeouts are required.")
    if request_path.exists():
        raise EditorBuildError(
            f"The open {display_name} Editor already has a pending build request: {request_path}. "
            "Wait for it to finish or restart that Editor if the request is stale."
        )

    request_id = uuid.uuid4().hex
    request = dict(payload) | {"schemaVersion": 1, "requestId": request_id, "outputPath": str(output_path)}
    request_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.unlink(missing_ok=True)
    temporary_path = request_path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(request_path)

    emit(f"  Using the {display_name} Unity Editor that is already open.")
    emit("  You can keep the Editor open; it may be unresponsive while Unity builds the output.")

    started_at = time.monotonic()
    first_response_deadline = started_at + start_timeout
    deadline = started_at + timeout
    last_stage = 0
    last_response_text = ""
    try:
        with BuildMonitor(
            "Unity Editor build", console=console, log_path=session_log(request_path.parent, "open-editor-build")
        ) as monitor:
            tail: FileTail | None = None
            last_message = ""
            monitor.message = "Waiting for Unity; Ctrl+C stops monitoring this open Editor"
            while time.monotonic() < deadline:
                monitor.tick()
                response = response_reader(response_path, request_id)
                if response is None:
                    if time.monotonic() >= first_response_deadline:
                        raise EditorBuildError(
                            f"The open {display_name} Editor did not accept the build request within "
                            f"{start_timeout} seconds. "
                            "Wait for Unity compilation to finish, or restart that Editor and rerun the command."
                        )
                    time.sleep(0.2)
                    continue

                if tail is None and isinstance(response.get("logPath"), str) and response["logPath"]:
                    offset = response.get("logOffset", 0)
                    tail = FileTail(
                        Path(str(response["logPath"])), offset=max(0, offset) if isinstance(offset, int) else 0
                    )
                if tail is not None:
                    monitor.feed(tail.read())
                message_text = str(response.get("message") or "")
                if message_text and message_text != last_message:
                    monitor.feed(message_text + "\n")
                    last_message = message_text
                monitor.tick(f"Unity stage {response.get('stage', '?')}/{len(steps)} • {message_text}")
                if verbose:
                    response_text = json.dumps(response, ensure_ascii=False, sort_keys=True)
                    if response_text != last_response_text:
                        emit(f"  editor response: {response_text}")
                        last_response_text = response_text

                stage = response.get("stage")
                state = response.get("state")
                message = response.get("message")
                if not isinstance(stage, int) or not isinstance(state, str):
                    raise EditorBuildError(f"The open {display_name} Editor returned an invalid build response.")

                if stage != last_stage:
                    if not 1 <= stage <= len(steps) or stage < last_stage:
                        raise EditorBuildError(
                            f"The open {display_name} Editor reported an invalid build stage: {stage}."
                        )
                    if 0 < last_stage <= len(steps):
                        emit(f"  [PASS] {steps[last_stage - 1][2]}")
                    for skipped_stage in range(max(1, last_stage + 1), stage):
                        skipped_label, skipped_explanation, skipped_success = steps[skipped_stage - 1]
                        emit(f"\nStep {skipped_stage}/{len(steps)} {skipped_label}")
                        emit(f"  {skipped_explanation}")
                        emit(f"  [PASS] {skipped_success}")
                    label, explanation, _success_message = steps[stage - 1]
                    emit(f"\nStep {stage}/{len(steps)} {label}")
                    emit(f"  {explanation}")
                    if isinstance(message, str) and message:
                        emit(f"  {message}")
                    last_stage = stage

                if state == "succeeded":
                    reported_output = response.get("outputPath")
                    if not isinstance(reported_output, str) or not reported_output:
                        raise EditorBuildError(f"The open {display_name} Editor did not report the output output path.")
                    if Path(reported_output).resolve() != output_path.resolve():
                        raise EditorBuildError(
                            f"The open {display_name} Editor wrote an unexpected output: {reported_output}."
                        )
                    if not output_path.is_file():
                        raise EditorBuildError(
                            f"The open {display_name} Editor did not create the output: {output_path}."
                        )
                    actual_size = output_path.stat().st_size
                    reported_size = response.get("sizeBytes")
                    if not isinstance(reported_size, int) or reported_size != actual_size:
                        raise EditorBuildError(
                            f"The open {display_name} Editor reported an invalid output size: "
                            f"reported={reported_size!r}, actual={actual_size}."
                        )
                    emit(f"  [PASS] {steps[stage - 1][2]}")
                    emit(f"  output size: {actual_size:,} bytes")
                    if tail is not None:
                        while text := tail.read():
                            monitor.feed(text)
                    return
                if state == "failed":
                    detail = message if isinstance(message, str) and message else "The open Unity Editor build failed."
                    raise EditorBuildError(f"Open Editor build failed during step {stage}/{len(steps)}: {detail}")
                time.sleep(0.2)

            raise EditorBuildError(
                f"The open {display_name} Editor build did not finish within {timeout // 60} minutes."
            )
    except KeyboardInterrupt:
        emit("Stopped monitoring. The already-open Unity Editor may continue its build; cancel it in Unity if needed.")
        raise
    finally:
        current_request = None
        if request_path.exists():
            try:
                current_request = json.loads(request_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current_request = None
        if isinstance(current_request, dict) and current_request.get("requestId") == request_id:
            request_path.unlink(missing_ok=True)
        response = response_reader(response_path, request_id)
        if response is not None:
            response_path.unlink(missing_ok=True)

"""Human-readable build records, including older JSON with escaped Unicode."""

import json
import re
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .platforms import output_label, platform_label
from .progress import duration


def failure_reason(text: str) -> str:
    """Prefer the actionable exception over Unity's generic executeMethod error."""
    for pattern in (
        r"^(.*?\berror CS\d+:\s*.+)",
        r"^BuildFailedException:\s*(.+)",
        r"^FAILURE:\s*(.+)",
        r"^CommandInvokationFailure:\s*(.+)",
    ):
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            return match.group(1).strip()[:500]
    return ""


def record_reason(record: dict, root: Path) -> str:
    if record.get("status") == "succeeded":
        return ""
    if record.get("status") == "cancelled":
        return "Cancelled by user"
    if record.get("failure_reason"):
        return str(record["failure_reason"])
    log = Path(str(record.get("log", "")))
    if not log.is_absolute():
        log = root / log
    if log.resolve().is_relative_to(root.resolve()) and log.is_file():
        try:
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 256 * 1024))
                reason = failure_reason(stream.read().decode("utf-8", errors="replace"))
            if reason:
                return reason
        except OSError:
            pass
    return str(record.get("error") or "Reason unavailable; see full log")


def render_history(console: Console, root: Path, directory: str, *, limit: int = 20) -> None:
    paths = sorted((root / directory).glob("*.json"), reverse=True)[:limit]
    if not paths:
        console.print("No build records yet.")
        return
    console.print(Text(f"Build history · {root.name}", style="bold"))
    console.print(Text(str(root), style="dim"))
    console.print("Times are shown in local time.", style="dim")
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(record, dict):
                raise TypeError("Expected a build record object")
        except (OSError, ValueError, TypeError) as exc:
            console.print(Text(f"Cannot read {path.name}: {exc}", style="yellow"))
            continue
        try:
            start = datetime.fromisoformat(record["started_at"])
            date = start.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            elapsed = duration(max(0, (datetime.fromisoformat(record["finished_at"]) - start).total_seconds()))
        except (KeyError, TypeError, ValueError, OverflowError):
            date, elapsed = str(record.get("started_at", path.stem)), "Unknown"
        status = str(record.get("status", "unknown"))
        label, color = {
            "succeeded": ("Succeeded", "green"), "failed": ("Failed", "red"),
            "cancelled": ("Cancelled", "yellow"),
        }.get(status, (status.title(), "dim"))
        output = str(record.get("output", "Unknown"))
        platform = record.get("platform") or output_label(output)
        platform = platform_label(str(platform))
        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim")
        table.add_column(overflow="fold")
        def relative_path(value: object) -> str:
            try:
                return Path(str(value)).relative_to(root).as_posix()
            except ValueError:
                return str(value)

        for key, value in (
            ("Profile", record.get("profile", "Unknown")), ("Target", platform),
            ("Duration", elapsed), ("Output", relative_path(output)),
            ("Reason", record_reason(record, root)), ("Log", relative_path(record.get("log", "Not recorded"))),
        ):
            if value:
                table.add_row(key, Text(str(value)))
        console.print(Panel(table, title=Text(f"{date} · {label}"), title_align="left", border_style=color))

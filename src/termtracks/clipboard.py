"""Read the system clipboard / primary selection on Linux, macOS, and Windows."""

from __future__ import annotations

import shutil
import subprocess
import sys


def _run_paste(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def read_primary() -> str:
    """Return the X11/Wayland primary selection (middle-click paste buffer)."""
    for cmd in (
        ["wl-paste", "--primary", "--no-newline"],
        ["xclip", "-selection", "primary", "-o"],
        ["xsel", "--primary", "--output"],
    ):
        text = _run_paste(cmd)
        if text:
            return text
    return ""


def read_clipboard() -> str:
    """Return the system clipboard contents."""
    if sys.platform == "darwin":
        return _run_paste(["pbpaste"]) or ""
    if sys.platform == "win32":
        return _run_paste([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Get-Clipboard -Raw",
        ]) or ""
    for cmd in (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    ):
        text = _run_paste(cmd)
        if text:
            return text
    return ""

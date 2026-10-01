"""The only module that starts external processes (ffmpeg, ffprobe, yt-dlp, helper Pythons).

Rules enforced here:
- arguments are a list of strings, never a shell string (shell=False always);
- every call has a timeout; on timeout the whole process group is killed;
- declared output files are deleted when the call fails, so no half-written MP4 survives;
- failures raise ToolError with the tool name, exit code and the tail of stderr.
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from .errors import PipelineError

STDERR_TAIL = 2000


class ToolError(PipelineError):
    code = "tool_failed"

    def __init__(self, tool: str, message: str, returncode: int | None = None, stderr: str = "", hint: str = ""):
        super().__init__(f"{tool}: {message}", hint)
        self.tool = tool
        self.returncode = returncode
        self.stderr = stderr[-STDERR_TAIL:]

    def __str__(self) -> str:
        text = super().__str__()
        return text + (f"\n  stderr (tail): {self.stderr.strip()[-600:]}" if self.stderr.strip() else "")


class ToolTimeout(ToolError):
    code = "tool_timeout"


class ToolNotFound(ToolError):
    code = "tool_not_found"


def _check_args(args: Sequence) -> list[str]:
    if isinstance(args, (str, bytes)):
        raise TypeError("proc.run needs an argument list, not a shell string")
    out = []
    for a in args:
        if isinstance(a, os.PathLike):
            a = os.fspath(a)
        if not isinstance(a, str):
            raise TypeError(f"argument {a!r} is not a string")
        if "\x00" in a:
            raise ValueError("NUL byte in subprocess argument")
        out.append(a)
    if not out:
        raise ValueError("empty command")
    return out


def run(args: Sequence, *, timeout: float, cwd: Path | None = None, outputs: Sequence[Path] = (),
        env: Mapping[str, str] | None = None, check: bool = True, text: bool = True,
        tool: str | None = None) -> subprocess.CompletedProcess:
    """Run a tool and return the CompletedProcess (stdout/stderr captured)."""
    argv = _check_args(args)
    name = tool or Path(argv[0]).name
    if timeout is None or timeout <= 0:
        raise ValueError("every subprocess call needs a positive timeout")
    try:
        p = subprocess.Popen(argv, cwd=cwd, env=None if env is None else dict(env), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text,
                             errors="replace" if text else None, start_new_session=True)
    except FileNotFoundError as exc:
        raise ToolNotFound(name, f"executable not found ({argv[0]})", hint="install it or set the HAYCLIPS_* path") from exc
    try:
        stdout, stderr = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        stdout, stderr = p.communicate()
        _cleanup(outputs)
        raise ToolTimeout(name, f"timed out after {timeout:.0f}s", stderr=stderr or "",
                          hint="partial outputs were deleted; check the input or raise the timeout")
    except BaseException:
        _kill_group(p)
        p.wait()
        _cleanup(outputs)
        raise
    result = subprocess.CompletedProcess(argv, p.returncode, stdout, stderr)
    if check and p.returncode != 0:
        _cleanup(outputs)
        raise ToolError(name, f"exited with code {p.returncode}", p.returncode, stderr or "")
    return result


def _kill_group(p: subprocess.Popen) -> None:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _cleanup(outputs: Sequence[Path]) -> None:
    for o in outputs:
        try:
            Path(o).unlink(missing_ok=True)
        except IsADirectoryError:
            pass

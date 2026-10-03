"""External-process boundary for the personal VBT workflow (#86). Standard library only.

`Runner` is the only place analyze_lift.py starts a process; tests substitute a fake.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STDERR_TAIL_BYTES = 4096
ERROR_MESSAGE_CHARS = 500


class WorkflowError(RuntimeError):
    pass


def echo_stderr(chunk: bytes) -> None:
    buffer = getattr(sys.stderr, "buffer", None)
    if buffer is not None:
        buffer.write(chunk)
        buffer.flush()
    else:
        sys.stderr.write(chunk.decode("utf-8", errors="replace"))


def last_error_line(stderr: bytes) -> str:
    """The last non-empty stderr line (progress bars use `\\r`), e.g. track_gpu.py's `error: ...`."""
    lines = [line.strip() for line in re.split(r"[\r\n]+", stderr.decode("utf-8", errors="replace"))]
    meaningful = [line for line in lines if line]
    return meaningful[-1][-ERROR_MESSAGE_CHARS:] if meaningful else ""


class Runner:
    """External process boundary; tests substitute a fake."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def capture_bytes(self, argv: list[str]) -> bytes:
        try:
            completed = subprocess.run(self._resolve(argv), cwd=ROOT, check=False, capture_output=True)
        except OSError as error:
            raise WorkflowError(f"{argv[0]} could not be run: {error}") from error
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise WorkflowError(f"{argv[0]} failed: {stderr[-ERROR_MESSAGE_CHARS:]}")
        return completed.stdout

    def capture(self, argv: list[str]) -> str:
        return self.capture_bytes(argv).decode("utf-8", errors="replace")

    def succeeds(self, argv: list[str]) -> bool:
        try:
            return subprocess.run(self._resolve(argv), cwd=ROOT, check=False, capture_output=True).returncode == 0
        except OSError:
            return False

    def execute(self, argv: list[str]) -> None:
        """Run a step with its output shown live; on failure, quote the step's own last error line."""
        try:
            process = subprocess.Popen(self._resolve(argv), cwd=ROOT, stderr=subprocess.PIPE)
        except OSError as error:
            raise WorkflowError(f"{argv[0]} could not be run: {error}") from error
        tail = b""
        with process:
            assert process.stderr is not None
            for chunk in iter(lambda: process.stderr.read1(STDERR_TAIL_BYTES), b""):
                echo_stderr(chunk)
                tail = (tail + chunk)[-STDERR_TAIL_BYTES:]
            returncode = process.wait()
        if returncode != 0:
            message = last_error_line(tail)
            suffix = f": {message}" if message else ""
            raise WorkflowError(f"{argv[0]} exited with status {returncode}{suffix}")

    @staticmethod
    def _resolve(argv: list[str]) -> list[str]:
        # A repository-relative program path is resolved against the root, not the caller's cwd.
        program = Path(argv[0])
        if not program.is_absolute() and (ROOT / program).is_file():
            return [str(ROOT / program), *argv[1:]]
        return argv

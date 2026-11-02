"""run_python's sandbox: run untrusted, LLM-written code in a separate, limited process.

What it does (minimum required by the brief, plus a few cheap extras):
  - a separate Python process (`python -I`: isolated mode, ignores env vars and user site-packages)
  - a fresh temporary folder as the working directory, holding COPIES of the input files only
  - a hard timeout (10 s by default) and, on Linux/macOS, CPU and memory limits
  - an empty environment: no API keys or other secrets are visible to the code
  - network disabled inside the process (socket creation raises an error)
  - stdout/stderr captured and truncated, and the traceback returned so the LLM can fix its code

It is NOT as strong as a container. For real isolation run the same command inside
`docker run --rm --network none --memory 512m -v <tmp>:/work ...` or an E2B sandbox.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_OUTPUT = 12_000

# Prepended to every script: block the network from inside Python.
NO_NETWORK = '''
import socket as _socket
def _blocked(*a, **k):
    raise PermissionError("network access is disabled in the sandbox")
_socket.socket = _blocked
_socket.create_connection = _blocked
del _socket, _blocked
'''


def _limits():  # runs in the child just before exec (POSIX only)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    except Exception:
        pass


def run_sandboxed(code: str, input_files: dict[str, Path], timeout: float = 10.0) -> dict:
    """Run `code` with `input_files` ({name_in_sandbox: real_path}) copied next to it.

    Returns {"ok", "returncode", "stdout", "stderr", "timed_out"}.
    """
    work = Path(tempfile.mkdtemp(prefix="sandbox_"))
    try:
        for name, src in input_files.items():
            shutil.copy(src, work / name)
            os.chmod(work / name, 0o444)                 # read-only copies
        script = work / "main.py"
        script.write_text(NO_NETWORK + "\n" + code, encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "utf-8", "HOME": str(work)}
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(script)], cwd=work, env=env, capture_output=True, text=True,
                timeout=timeout, preexec_fn=_limits if os.name == "posix" else None)
            rc, out, err, to = proc.returncode, proc.stdout, proc.stderr, False
        except subprocess.TimeoutExpired as e:
            rc, out, err, to = -1, (e.stdout or "") if isinstance(e.stdout, str) else "", \
                f"TimeoutError: the code ran longer than {timeout:g} s and was stopped.", True
        # Hide the sandbox path from tracebacks (shorter, and leaks nothing about the host)
        err = err.replace(str(script), "main.py").replace(str(work), ".")
        return {"ok": rc == 0, "returncode": rc, "stdout": out[-MAX_OUTPUT:], "stderr": err[-4000:], "timed_out": to}
    finally:
        shutil.rmtree(work, ignore_errors=True)

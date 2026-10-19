"""Hold an exclusive lock in the same Linux filesystem as the MCP reader."""

from contextlib import contextmanager
import shutil
import subprocess
import time
import uuid


@contextmanager
def container_storage_lock():
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI is required for the storage lock test")
    marker = "/tmp/ict-lock-" + uuid.uuid4().hex
    # EOF always releases the lock; the deadline also bounds an abandoned exec.
    code = '''import pathlib,select,sqlite3,sys
connection=sqlite3.connect('/runtime/business.sqlite3',timeout=2)
try:
    connection.execute('BEGIN EXCLUSIVE')
    pathlib.Path(sys.argv[1]).write_text('LOCKED')
    select.select([sys.stdin],[],[],180)
finally:
    connection.rollback()
    connection.close()
    pathlib.Path(sys.argv[1]).unlink(missing_ok=True)
'''
    process = subprocess.Popen([docker, "exec", "-i", "gov-blank-mcp", "python", "-c", code, marker],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise RuntimeError("Container storage lock exited before acquisition")
            probe = subprocess.run([docker, "exec", "gov-blank-mcp", "test", "-f", marker],
                                   capture_output=True, timeout=5)
            if probe.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Container storage lock acquisition timed out")
            time.sleep(0.2)
        yield
    finally:
        # Closing stdin wakes select even when the Agent run fails.
        try:
            process.communicate(input=b"\n", timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
            raise RuntimeError("Container storage lock release was not confirmed")
        if process.returncode != 0:
            raise RuntimeError("Container storage lock process failed")

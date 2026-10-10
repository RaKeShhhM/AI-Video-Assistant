"""Cross-process lock for a job's embedded vector database.

The lock file is outside the removable index directory. OS locks release if a
worker dies, so a cleanup process cannot race an index writer after deletion.
"""
import os
import time
from contextlib import contextmanager
from pathlib import Path

from utils.job_ids import validate_job_id


@contextmanager
def vector_job_lock(base_dir, job_id, timeout=120):
    validate_job_id(job_id)
    root = Path(base_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / f".{job_id}.lock"
    deadline = time.monotonic() + timeout
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Vector job lock timed out") from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Vector job lock timed out") from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

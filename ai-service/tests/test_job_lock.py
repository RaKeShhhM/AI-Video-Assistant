"""An index writer and deletion worker serialize across processes."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from utils.job_lock import vector_job_lock

BASE = Path(__file__).resolve().parents[1]
SCRIPT = """
import sys
from utils.job_lock import vector_job_lock
with vector_job_lock(sys.argv[1], sys.argv[2]):
    print('locked', flush=True)
    sys.stdin.read(1)
"""


class JobLockTests(unittest.TestCase):
    def test_second_process_waits_for_writer_before_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="reel-r5-lock-") as directory:
            job_id = "a" * 24
            child = subprocess.Popen([sys.executable, "-c", SCRIPT, directory, job_id],
                cwd=BASE, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                with self.assertRaises(TimeoutError):
                    with vector_job_lock(directory, job_id, timeout=0.2):
                        pass
                child.communicate(input="x", timeout=5)
                self.assertEqual(child.returncode, 0)
                with vector_job_lock(directory, job_id, timeout=1):
                    pass
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)

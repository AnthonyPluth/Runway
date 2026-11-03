"""tools/test_jobs.sh's process count for the Postgres run. (The timing summary `make test-timings` prints is fleet's
`fleet test-timings`, tested in fleet; tests/timing_result.py writes what it reads.)"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestJobsTests(unittest.TestCase):
    def jobs(self, cpus) -> int:
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d, "nproc")
            fake.write_text(f"#!/bin/sh\necho {cpus}\n")
            fake.chmod(0o755)
            env = {**os.environ, "PATH": f"{d}{os.pathsep}{os.environ.get('PATH', '')}"}
            return int(subprocess.run([str(ROOT / "tools" / "test_jobs.sh")], env=env, capture_output=True, text=True,
                                      check=True).stdout)

    def test_one_process_per_cpu_between_4_and_16(self):
        self.assertEqual(self.jobs(2), 4)
        self.assertEqual(self.jobs(8), 8)
        self.assertEqual(self.jobs(64), 16)


if __name__ == "__main__":
    unittest.main()

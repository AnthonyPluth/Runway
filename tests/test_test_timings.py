"""The test-timing tooling: tools/test_timings.py's summary of what tests/timing_result.py writes, and
tools/test_jobs.sh's process count for the Postgres run."""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("test_timings_tool", ROOT / "tools" / "test_timings.py")
assert _spec and _spec.loader
test_timings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(test_timings)


class TimingsReportTests(unittest.TestCase):
    def test_sums_every_file_and_lists_the_slowest_first(self):
        with tempfile.TemporaryDirectory() as d:
            for name, tests, phases, ended in (
                    ("a", [{"module": "tests.test_a", "class": "A", "test": "test_slow", "seconds": 3.0,
                            "phases": {"schema": 2.5}},
                           {"module": "tests.test_a", "class": "A", "test": "test_quick", "seconds": 0.5, "phases": {}}],
                     {"schema": 2.75, "teardown": 0.25}, 1),
                    ("b", [{"module": "tests.test_b", "class": "B", "test": "test_middling", "seconds": 1.0,
                            "phases": {}}], {"schema": 0.25}, 0)):
                Path(d, f"{name}.json").write_text(json.dumps({"tests": tests, "phases": phases, "ended": ended}))
            timed, phases, ended = test_timings.load(Path(d))
        self.assertEqual(len(timed), 3)
        self.assertEqual(phases, {"schema": 3.0, "teardown": 0.25})
        self.assertEqual(ended, 1)
        out = test_timings.report(timed, phases, top=2, ended=ended)
        self.assertIn("3 tests, 4.5s of test time", out)
        self.assertIn("  schema: 3.0s", out)
        self.assertIn("Connections ended to give a schema back (a test left one open): 1", out)
        slowest = out.split("Slowest 2 tests\n")[1].split("\n\n")[0].splitlines()
        self.assertEqual(len(slowest), 2)
        self.assertIn("tests.test_a.A.test_slow  schema=2.50", slowest[0])
        self.assertIn("tests.test_b.B.test_middling", slowest[1])
        self.assertIn("3.5s     2 tests  tests.test_a.A", out)


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

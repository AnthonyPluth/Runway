"""A unittest-parallel result class (`--result tests.timing_result.TimingResult`, what `make test-timings` runs) that
writes each test's time, and the time spent making and giving back databases (tests/shared.py's phases), as JSON into
$TIMINGS_DIR, one file per module run. tools/test_timings.py sums them up. Not used by `make check` or CI's usual run.
The console output is unittest-parallel's usual."""
import json
import os
import time
import unittest
import uuid

from unittest_parallel.main import ParallelTextTestResult

from tests import shared


class TimingResult(ParallelTextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._timed: list[dict] = []
        self._started = 0.0
        self._phases: dict[str, float] = {}
        # What this module's run adds: the counters are per process, and a process runs several modules.
        self._run_phases = dict(shared.PHASES)
        self._run_ended = shared.ENDED[0]

    def startTest(self, test):
        self._phases = dict(shared.PHASES)
        self._started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        elapsed = time.perf_counter() - self._started
        super().stopTest(test)
        phases = {k: round(v - self._phases.get(k, 0.0), 4) for k, v in shared.PHASES.items()}
        self._timed.append({"module": type(test).__module__, "class": type(test).__qualname__,
                            "test": getattr(test, "_testMethodName", str(test)), "seconds": round(elapsed, 4),
                            "phases": {k: v for k, v in phases.items() if v}})

    def printErrors(self):
        unittest.TextTestResult.printErrors(self)   # unittest-parallel leaves printing the failures to a custom result

    def stopTestRun(self):
        super().stopTestRun()
        directory = os.environ.get("TIMINGS_DIR")
        if not directory or not self._timed:
            return
        os.makedirs(directory, exist_ok=True)
        # The whole run's phases, so set-up and clean-up a class shares (setUpClass, class cleanups) count too.
        phases = {k: round(v - self._run_phases.get(k, 0.0), 4) for k, v in shared.PHASES.items()}
        with open(os.path.join(directory, f"{uuid.uuid4().hex}.json"), "w") as f:
            json.dump({"tests": self._timed, "phases": phases, "ended": shared.ENDED[0] - self._run_ended}, f)

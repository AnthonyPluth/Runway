import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts", "coverage_badge.py")
spec = importlib.util.spec_from_file_location("coverage_badge", SCRIPT)
assert spec and spec.loader
coverage_badge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage_badge)


class CoverageBadgeTests(unittest.TestCase):
    def test_percent_reads_coverage_py(self):
        self.assertEqual(coverage_badge.percent({"totals": {"percent_covered": 91.99}}), 91)

    def test_percent_reads_vitest(self):
        self.assertEqual(coverage_badge.percent({"total": {"lines": {"pct": 78.4}}}), 78)

    def test_color_thresholds(self):
        expected = {100: "#4c1", 90: "#4c1", 89: "#97ca00", 80: "#97ca00", 79: "#dfb317", 70: "#dfb317",
                    69: "#fe7d37", 60: "#fe7d37", 59: "#e05d44", 0: "#e05d44"}
        for pct, color in expected.items():
            self.assertEqual(coverage_badge.color(pct), color, pct)

    def test_endpoint_schema(self):
        self.assertEqual(
            coverage_badge.endpoint(91, "backend coverage"),
            {"schemaVersion": 1, "label": "backend coverage", "message": "91%", "color": "#4c1"},
        )

    def test_command_line_writes_endpoint_json(self):
        with tempfile.TemporaryDirectory() as d:
            report, out = os.path.join(d, "r.json"), os.path.join(d, "b.json")
            with open(report, "w") as f:
                json.dump({"total": {"lines": {"pct": 85.7}}}, f)
            run = subprocess.run([sys.executable, SCRIPT, report, out, "frontend coverage"], capture_output=True, text=True, check=True)
            self.assertEqual(run.stdout.strip(), "85")
            with open(out) as f:
                self.assertEqual(json.load(f), {"schemaVersion": 1, "label": "frontend coverage", "message": "85%", "color": "#97ca00"})


if __name__ == "__main__":
    unittest.main()

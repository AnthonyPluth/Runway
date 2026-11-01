"""The merge gate's filter for open P1/P2 review threads (.github/scripts/merge-gate.sh), run on a made-up GraphQL reply."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / ".github/scripts/merge-gate.sh"


def thread(body: str | None, resolved: bool = False) -> dict:
    nodes = [] if body is None else [{"body": body}]
    return {"isResolved": resolved, "comments": {"nodes": nodes}}


@unittest.skipUnless(shutil.which("jq"), "jq isn't installed")
class OpenPriorityThreads(unittest.TestCase):
    def count(self, threads: list[dict]) -> int:
        m = re.search(r"^OPEN_PRIORITY_THREADS='(.*?)'$", SCRIPT.read_text(), re.M | re.S)
        assert m, "the filter isn't in merge-gate.sh"
        reply = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}
        out = subprocess.run(["jq", "-r", m.group(1)], input=json.dumps(reply), capture_output=True, text=True, check=True).stdout
        return len(out.splitlines())

    def test_counts_open_p1_and_p2_threads(self):
        self.assertEqual(self.count([thread("**<sub><sub>![P1 Badge](https://example.test/P1)</sub></sub> Add a check**"),
                                     thread("![P2 Badge](https://example.test/P2) Rename it")]), 2)

    def test_skips_resolved_lower_priority_plain_and_empty_threads(self):
        self.assertEqual(self.count([thread("![P1 Badge](https://example.test/P1)", resolved=True), thread("![P3 Badge](x) nit"),
                                     thread("a person mentioning P1"), thread(None)]), 0)


if __name__ == "__main__":
    unittest.main()

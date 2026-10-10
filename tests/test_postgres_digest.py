""".github/scripts/postgres_digest.py: whether the Postgres image pinned in docker.yml has fallen behind its tag, and
the issue the weekly workflow opens when it has. No network: the registries are stood in for by a fake fetch."""
import importlib.util
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("postgres_digest", ROOT / ".github" / "scripts" / "postgres_digest.py")
assert _spec and _spec.loader
pd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pd)

OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
WORKFLOW = f"""
    services:
      postgres:
        image: mirror.gcr.io/library/postgres:16@{OLD}   # 16.1
        env:
          POSTGRES_USER: runway
"""


def registries(mirror=NEW, hub=NEW):
    """A fake fetch: each registry answers with its digest, or fails when given an exception (or None for a bad answer)."""
    calls = []

    def get(url, headers, method):
        calls.append((url, method))
        on_mirror = urllib.parse.urlsplit(url).hostname == "mirror.gcr.io"
        answer = mirror if on_mirror else hub
        if isinstance(answer, Exception):
            raise answer
        if on_mirror:
            return ({"docker-content-digest": answer} if answer else {}), b""
        return {}, json.dumps({"digest": answer} if answer else {"name": "16"}).encode()

    get.calls = calls
    return get


class Pinned(unittest.TestCase):
    def test_reads_the_image_repo_tag_and_digest(self):
        self.assertEqual(pd.pinned(WORKFLOW), ("library/postgres", "16", OLD))

    def test_a_workflow_without_the_pin_is_an_error_not_a_pass(self):
        for text in ("services:\n  postgres:\n    image: postgres:16\n",   # back on Docker Hub, unpinned
                     f"image: mirror.gcr.io/library/postgres:16@{OLD}\nimage: mirror.gcr.io/library/postgres:17@{NEW}\n"):
            with self.assertRaises(ValueError):
                pd.pinned(text)

    def test_the_pin_in_docker_yml_is_found(self):
        repo, tag, digest = pd.pinned((ROOT / ".github" / "workflows" / "docker.yml").read_text())
        self.assertEqual((repo, tag), ("library/postgres", "16"))
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")


class Current(unittest.TestCase):
    def test_the_mirror_answers_first_and_docker_hub_is_not_asked(self):
        get = registries()
        self.assertEqual(pd.current("library/postgres", "16", get), (NEW, "mirror.gcr.io"))
        self.assertEqual(get.calls, [("https://mirror.gcr.io/v2/library/postgres/manifests/16", "HEAD")])

    def test_docker_hub_answers_when_the_mirror_cannot(self):
        for mirror in (urllib.error.URLError("down"), None, "not-a-digest"):
            with self.subTest(mirror=mirror), mock.patch("sys.stderr"):
                get = registries(mirror=mirror)
                self.assertEqual(pd.current("library/postgres", "16", get), (NEW, "Docker Hub"))
                self.assertEqual(get.calls[-1][0], "https://hub.docker.com/v2/repositories/library/postgres/tags/16")

    def test_neither_answering_raises_naming_both(self):
        with mock.patch("sys.stderr"), self.assertRaises(RuntimeError) as cm:
            pd.current("library/postgres", "16", registries(mirror=OSError("m"), hub=urllib.error.URLError("h")))
        self.assertIn("mirror.gcr.io: m", str(cm.exception))
        self.assertIn("Docker Hub", str(cm.exception))

    def test_a_hub_answer_that_is_not_an_object_is_no_digest(self):
        get = mock.Mock(return_value=({}, b"[1, 2]"))
        with self.assertRaises(ValueError):
            pd.hub_digest("library/postgres", "16", get)

    def test_only_https_is_fetched(self):
        with self.assertRaises(ValueError):
            pd.fetch("http://mirror.gcr.io/v2/library/postgres/manifests/16", {})


class Fetch(unittest.TestCase):
    def response(self, headers=None, body=b""):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.headers.items.return_value = (headers or {}).items()
        resp.read.return_value = body
        return resp

    def test_headers_come_back_lowercase(self):
        with mock.patch("urllib.request.urlopen", return_value=self.response({"Docker-Content-Digest": NEW}, b"x")):
            self.assertEqual(pd.fetch("https://example.com/", {}), ({"docker-content-digest": NEW}, b"x"))

    def test_a_server_error_is_retried_and_a_client_error_is_not(self):
        err = lambda code: urllib.error.HTTPError("https://example.com/", code, "x", {}, None)
        with mock.patch("time.sleep"):
            with mock.patch("urllib.request.urlopen", side_effect=[err(503), self.response(body=b"ok")]) as op:
                self.assertEqual(pd.fetch("https://example.com/", {})[1], b"ok")
                self.assertEqual(op.call_count, 2)
            with mock.patch("urllib.request.urlopen", side_effect=[err(429), self.response()]) as op:
                with self.assertRaises(urllib.error.HTTPError):
                    pd.fetch("https://example.com/", {})
                self.assertEqual(op.call_count, 1)
            with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")) as op:
                with self.assertRaises(OSError):
                    pd.fetch("https://example.com/", {}, attempts=3)
                self.assertEqual(op.call_count, 3)


class Main(unittest.TestCase):
    def run_main(self, mirror=NEW, hub=NEW, workflow=WORKFLOW):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        tmp = Path(tmpdir.name)
        (tmp / "docker.yml").write_text(workflow)
        out = tmp / "output"
        env = {"GITHUB_OUTPUT": str(out)}
        with mock.patch.dict(os.environ, env), mock.patch.object(pd, "fetch", registries(mirror, hub)), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            code = pd.main(["x", str(tmp / "docker.yml"), str(tmp / "body.md")])
        body = tmp / "body.md"
        return code, (body.read_text() if body.exists() else None), (out.read_text() if out.exists() else "")

    def test_in_step_writes_no_issue(self):
        code, body, output = self.run_main(mirror=OLD)
        self.assertEqual((code, body, output), (0, None, ""))

    def test_drift_writes_the_issue_and_the_output(self):
        code, body, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(output, "drift=true\n")
        for part in (OLD, NEW, "docker buildx imagetools inspect postgres:16",
                     f"docker buildx imagetools inspect mirror.gcr.io/library/postgres@{NEW}", "mirror.gcr.io"):
            self.assertIn(part, body)

    def test_the_issue_is_the_same_text_for_the_same_drift(self):
        # The workflow edits the open issue only when its body changed, so the text mustn't vary between runs.
        self.assertEqual(self.run_main()[1], self.run_main()[1])

    def test_no_answer_from_either_registry_is_a_failure(self):
        with self.assertRaises(RuntimeError):
            self.run_main(mirror=OSError("m"), hub=OSError("h"))

    def test_the_workflow_looks_for_the_issue_by_the_scripts_title(self):
        text = (ROOT / ".github" / "workflows" / "postgres-digest.yml").read_text()
        self.assertIn(f'TITLE: "{pd.TITLE}"', text)


if __name__ == "__main__":
    unittest.main()

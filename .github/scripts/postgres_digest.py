#!/usr/bin/env python3
"""Has the Postgres image pinned for CI's test service fallen behind its tag?

docker.yml pins `mirror.gcr.io/library/postgres:16@sha256:...`. Dependabot doesn't read a workflow's `services:`
images, so nothing moves that digest when Postgres 16 gets a patch release. This compares it with the digest `16`
points to now: the mirror's first (it serves what the tests pull), Docker Hub's tag API when the mirror can't answer.
Both give the multi-arch index's digest, the one the pin holds.

    postgres_digest.py <docker.yml> <issue-body-file>

In step with the tag: prints so, exits 0. Behind it: writes the issue's text to the body file, adds `drift=true` to
$GITHUB_OUTPUT (when set) and exits 0, so the workflow's next step can open or update the issue. Neither registry
answering is an error (exit 1). Standard library only; makes no change anywhere."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

# The issue's title; postgres-digest.yml looks for an open issue with it (a test keeps the two the same).
TITLE = "ci: CI's pinned Postgres image is behind postgres:16"

# The service image in docker.yml: the repository (on the mirror), its tag and the digest it is pinned to.
PIN = re.compile(r"^\s*image:\s*mirror\.gcr\.io/(?P<repo>library/postgres):(?P<tag>[\w.-]+)@(?P<digest>sha256:[0-9a-f]{64})",
                 re.MULTILINE)
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
INDEX_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])

# get(url, headers, method) -> (response headers, body); raises OSError (as urllib's errors are) when it can't.
Fetch = Callable[[str, dict, str], "tuple[dict, bytes]"]


def fetch(url: str, headers: dict, method: str = "GET", attempts: int = 3) -> tuple[dict, bytes]:
    """One HTTPS request, tried again after a network error or a 5xx; a 4xx (including Docker Hub's 429) is final."""
    if not url.startswith("https://"):
        raise ValueError(f"not an https URL: {url}")
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "runway-postgres-digest-check", **headers}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return {k.lower(): v for k, v in resp.headers.items()}, resp.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == attempts:
                raise
        except OSError:
            if attempt == attempts:
                raise
        time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def pinned(workflow_text: str) -> tuple[str, str, str]:
    """(repository, tag, digest) of the mirrored Postgres service image; exactly one must be there."""
    found = list(PIN.finditer(workflow_text))
    if len(found) != 1:
        raise ValueError("expected one `image: mirror.gcr.io/library/postgres:<tag>@sha256:...` in docker.yml, "
                         f"found {len(found)}")
    return found[0]["repo"], found[0]["tag"], found[0]["digest"]


def mirror_digest(repo: str, tag: str, get: Fetch = fetch) -> str:
    """The index digest mirror.gcr.io's registry (anonymous) reports for the tag."""
    headers, _ = get(f"https://mirror.gcr.io/v2/{repo}/manifests/{tag}", {"Accept": INDEX_TYPES}, "HEAD")
    return _digest(headers.get("docker-content-digest", ""), "mirror.gcr.io")


def hub_digest(repo: str, tag: str, get: Fetch = fetch) -> str:
    """The index digest Docker Hub's tag API reports (its `digest` is the tag's index, not one platform's)."""
    _, body = get(f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}", {"Accept": "application/json"}, "GET")
    answer = json.loads(body)
    return _digest(str(answer.get("digest", "")) if isinstance(answer, dict) else "", "Docker Hub")


def _digest(value: str, source: str) -> str:
    if not DIGEST.match(value):
        raise ValueError(f"{source} gave no usable digest ({value[:80]!r})")
    return value


def current(repo: str, tag: str, get: Fetch = fetch) -> tuple[str, str]:
    """(digest, where it came from): the mirror, else Docker Hub. Raises when neither answers."""
    errors = []
    for source, resolve in (("mirror.gcr.io", mirror_digest), ("Docker Hub", hub_digest)):
        try:
            return resolve(repo, tag, get), source
        except (OSError, ValueError) as e:   # the network, an HTTP status, or an answer that isn't a digest (bad JSON too)
            errors.append(f"{source}: {e}")
            print(f"::warning::{source} couldn't tell the digest of {repo}:{tag}: {e}", file=sys.stderr)
    raise RuntimeError("couldn't resolve the current digest: " + "; ".join(errors))


def issue_body(repo: str, tag: str, pin: str, now: str, source: str) -> str:
    name = repo.split("/")[-1]
    return "\n".join([
        f"The Postgres service in `.github/workflows/docker.yml` is pinned to `mirror.gcr.io/{repo}:{tag}@{pin}`, but "
        f"`{name}:{tag}` now points to a different image, so CI's Postgres is missing its newer releases.",
        "",
        f"- Pinned: `{pin}`",
        f"- Current: `{now}` (from {source})",
        "",
        "Dependabot doesn't read a workflow's `services:` images, so the pin moves by hand:",
        "",
        "```",
        f"docker buildx imagetools inspect {name}:{tag}",
        f"docker buildx imagetools inspect mirror.gcr.io/{repo}@{now}",
        "```",
        "",
        "The first prints the digest to pin (the index's, not one platform's); the second checks that the mirror serves "
        f"it. Then replace `{pin}` in `docker.yml`'s `image:` line with it and update the version in that line's comment "
        f"(`docker run --rm {name}:{tag} postgres --version` prints it). "
        "The comment above that line has the details, and how to go back to Docker Hub.",
        "",
        "Opened by the weekly Postgres digest check (`.github/workflows/postgres-digest.yml`), which keeps this issue "
        "current while the digests differ. Close it once the pin is bumped.",
        "",
    ])


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    workflow, body_file = Path(argv[1]), Path(argv[2])
    repo, tag, pin = pinned(workflow.read_text())
    now, source = current(repo, tag, fetch)
    if now == pin:
        print(f"{repo}:{tag} is still {pin} ({source}): nothing to do.")
        return 0
    print(f"{repo}:{tag} is now {now} ({source}); docker.yml pins {pin}.")
    body_file.write_text(issue_body(repo, tag, pin, now, source))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as out:
            out.write("drift=true\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (RuntimeError, ValueError, OSError) as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)

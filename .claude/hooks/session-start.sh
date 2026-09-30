#!/bin/bash
# Claude Code on the web: get a fresh session's container ready to run what CI runs (make check), before the session
# starts. The backend needs Python 3.14 (Poetry), which the container doesn't come with; the web app needs its npm
# packages. Safe to run again: each step is quick when there's nothing to do.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0   # on your own machine, docs/development.md says how to set up
fi
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Python 3.14 for Poetry's environment (uv fetches a build if the container has none).
if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet --user uv
  export PATH="$HOME/.local/bin:$PATH"
fi
py=$(command -v python3.14 || uv python find 3.14 2>/dev/null || true)
if [ -z "$py" ]; then
  uv python install 3.14
  py=$(uv python find 3.14)
fi
if ! command -v poetry >/dev/null 2>&1; then
  python3 -m pip install --quiet --user "poetry==2.5.1"
  export PATH="$HOME/.local/bin:$PATH"
fi
poetry env use --quiet "$py" >/dev/null
poetry install --no-root --no-interaction

# The tools CI pins (docker.yml): ruff and mypy for `make lint`, unittest-parallel for running tests as CI does.
poetry run pip install --quiet --disable-pip-version-check \
  "ruff==0.16.9" "mypy==2.3.1" "types-python-dateutil==2.9.0.20260807" "unittest-parallel==1.8.6"

# The web app's packages (npm install, not ci: the container's cache keeps them between sessions).
(cd frontend && npm install --no-audit --no-fund)

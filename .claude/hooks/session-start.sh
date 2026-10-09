#!/bin/bash
# Claude Code on the web: get a fresh session's container ready to run what CI runs (make check), before the session
# starts. The backend needs Python 3.14 (Poetry), which the container doesn't come with; the web app needs its npm
# packages. Safe to run again: each step is quick when there's nothing to do.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0   # on your own machine, docs/src/content/docs/contributing/development.md says how to set up
fi
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Python 3.14 for Poetry's environment (uv fetches a build if the container has none).
if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet --user "uv==0.12.21"
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
# Runway's dependencies and the tools CI runs (ruff, mypy, coverage, ...: pyproject.toml's dev group).
poetry install --no-root --no-interaction

# The web app's packages (npm install, not ci: the container's cache keeps them between sessions).
(cd frontend && npm install --no-audit --no-fund)

# A node_modules restored from the container's cache can be cut short or stale, and `npm install` takes it as done: ESLint
# then fails to start ('Could not find "svelte" in plugin "svelte"') and svelte-check can't find esrap's types, so lint
# and type errors only show up in CI. Check the two packages those errors name, and reinstall from the lock file if
# either is incomplete.
node_modules_ok() {
  (cd frontend &&
    test -f node_modules/esrap/types/public.d.ts &&
    node --input-type=module -e 'const p = await import("eslint-plugin-svelte"); if (!(p.default ?? p).processors?.svelte) process.exit(1)' &&
    npx --no-install eslint --version >/dev/null) 2>/dev/null
}
if ! node_modules_ok; then
  echo "frontend/node_modules is incomplete; reinstalling from the lock file" >&2
  (cd frontend && rm -rf node_modules && npm ci --no-audit --no-fund)
  node_modules_ok || echo "frontend/node_modules is still incomplete: ESLint or svelte-check will fail (see .claude/hooks/session-start.sh)" >&2
fi

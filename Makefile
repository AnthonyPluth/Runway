# Everything CI checks, runnable before you push: `make check`. The other targets run one part of it.
# Python commands go through Poetry, as in the README's Development section.

PYTHON ?= poetry run python
NPM ?= npm
# Ruff isn't one of Runway's dependencies, so use the one in the Poetry environment if you installed it there, else
# the one on your PATH, else pipx's copy of the version CI pins.
RUFF ?= $(shell if poetry run ruff --version >/dev/null 2>&1; then echo "poetry run ruff"; \
	elif command -v ruff >/dev/null 2>&1; then echo ruff; else echo "pipx run ruff==0.16.9"; fi)

.PHONY: check lint test fix frontend-check

check: lint test frontend-check

# Ruff for Python, and a syntax check of the plain JavaScript that isn't built (the service worker, the extension).
lint:
	$(RUFF) check .
	find runway/static extension -name '*.js' -print0 | xargs -0 -n1 node --check

test:
	$(PYTHON) -m unittest discover tests

# Applies ruff's safe fixes (unused imports and the like); review the diff before committing.
fix:
	$(RUFF) check --fix .

frontend/node_modules:
	cd frontend && $(NPM) ci --no-audit --no-fund

# `npm run lint` and `npm test` run only if frontend/package.json defines them: `--if-present` skips a missing script.
frontend-check: frontend/node_modules
	cd frontend && $(NPM) run --if-present lint
	cd frontend && $(NPM) run check
	cd frontend && $(NPM) run --if-present test
	cd frontend && $(NPM) run build

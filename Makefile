# Everything CI checks, runnable before you push: `make check`. The other targets run one part of it.
# Python commands go through Poetry, as in docs/development.md.

PYTHON ?= poetry run python
NPM ?= npm
# The tools come from the Poetry environment (pyproject.toml's dev group, which `poetry install` adds), at the
# versions CI runs. mypy has to run there anyway, to see Runway's dependencies.
RUFF ?= poetry run ruff
MYPY ?= poetry run mypy

.PHONY: check lint test fix frontend-check

check: lint test frontend-check

# Ruff and mypy for Python, and ESLint over the web app, the extension and runway/static (the same lint CI runs).
lint: frontend/node_modules
	$(RUFF) check .
	$(MYPY)
	cd frontend && $(NPM) run lint

test:
	$(PYTHON) -m unittest discover tests

# Applies ruff's safe fixes (unused imports and the like); review the diff before committing.
fix:
	$(RUFF) check --fix .

# Reinstalled whenever package-lock.json is newer, so a pull that adds a dependency picks it up.
frontend/node_modules: frontend/package-lock.json
	cd frontend && $(NPM) ci --no-audit --no-fund
	touch frontend/node_modules

# The web app's type-check, ESLint, Vitest tests and build, as CI runs them.
frontend-check: frontend/node_modules
	cd frontend && $(NPM) run check
	cd frontend && $(NPM) run lint
	cd frontend && $(NPM) test
	cd frontend && $(NPM) run build

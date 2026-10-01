# What CI checks, runnable before you push: `make check` runs ruff, mypy, the Python tests, the web app's type-check,
# ESLint, Vitest tests and build, Bandit and zizmor, each once. The other targets run one part of it. `make audit`
# (pip-audit and npm audit) needs the network, so it isn't part of check; Semgrep, Trivy and CodeQL run only in CI.
# Python commands go through Poetry, as in docs/development.md.

PYTHON ?= poetry run python
NPM ?= npm
# The tools come from the Poetry environment (pyproject.toml's dev group, which `poetry install` adds), at the
# versions CI runs. mypy has to run there anyway, to see Runway's dependencies.
RUFF ?= poetry run ruff
MYPY ?= poetry run mypy
BANDIT ?= poetry run bandit
ZIZMOR ?= poetry run zizmor
PIP_AUDIT ?= poetry run pip-audit

.PHONY: check lint python-lint frontend-lint test fix security audit \
	frontend-check frontend-typecheck frontend-test frontend-build

# frontend-lint is a prerequisite of both lint and frontend-check, and make runs it once.
check: lint test frontend-check security

# Ruff and mypy for Python, and ESLint over the web app, the extension and runway/static (the same lint CI runs).
lint: python-lint frontend-lint

python-lint:
	$(RUFF) check .
	$(MYPY)

test:
	$(PYTHON) -m unittest discover tests

# Applies ruff's safe fixes (unused imports and the like); review the diff before committing.
fix:
	$(RUFF) check --fix .

# Bandit over runway/ (settings in pyproject.toml) and zizmor over the repository (the workflows and Dependabot's
# settings), as security.yml runs them. zizmor runs offline here (CI's also asks GitHub about the actions it uses), and
# fails on a finding CI would report.
security:
	$(BANDIT) -c pyproject.toml -r runway -ll
	$(ZIZMOR) --offline .

# Known vulnerabilities in the Python and the web app's dependencies, as CI checks them; needs the network.
audit:
	$(PIP_AUDIT)
	cd frontend && $(NPM) audit --omit=dev --audit-level=high

# Reinstalled whenever package-lock.json is newer, so a pull that adds a dependency picks it up.
frontend/node_modules: frontend/package-lock.json
	cd frontend && $(NPM) ci --no-audit --no-fund
	touch frontend/node_modules

# The web app's type-check, ESLint, Vitest tests and build, as CI runs them.
frontend-check: frontend-typecheck frontend-lint frontend-test frontend-build

frontend-typecheck: frontend/node_modules
	cd frontend && $(NPM) run check

frontend-lint: frontend/node_modules
	cd frontend && $(NPM) run lint

frontend-test: frontend/node_modules
	cd frontend && $(NPM) test

frontend-build: frontend/node_modules
	cd frontend && $(NPM) run build

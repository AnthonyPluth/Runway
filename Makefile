# The checks to run before you push: `make check` runs ruff, mypy, the Python tests (in parallel, as CI does), the web app's type-check,
# ESLint, Vitest tests and build, and the docs site's build, each once. The other targets run one part of it. The security
# scans (Semgrep, Trivy, zizmor, pip-audit, npm audit, CodeQL) run only in CI, on the pull requests they can affect.
# Python commands go through Poetry, as in docs/src/content/docs/contributing/development.md.

PYTHON ?= poetry run python
NPM ?= npm
# The tools come from the Poetry environment (pyproject.toml's dev group, which `poetry install` adds), at the
# versions CI runs. mypy has to run there anyway, to see Runway's dependencies.
RUFF ?= poetry run ruff
MYPY ?= poetry run mypy
UNITTEST_PARALLEL ?= poetry run unittest-parallel -t . -s tests -j 4

.PHONY: check lint python-lint frontend-lint test test-parallel test-pg fix \
	frontend-check frontend-typecheck frontend-test frontend-build docs docs-build verify

# frontend-lint is a prerequisite of both lint and frontend-check, and make runs it once.
check: lint test-parallel frontend-check docs-build

# Ruff and mypy for Python, and ESLint over the web app, the extension and runway/static (the same lint CI runs).
lint: python-lint frontend-lint

python-lint:
	$(RUFF) check .
	$(MYPY)

test:
	$(PYTHON) -m unittest discover tests

# The tests across 4 processes, the command CI runs (about 3x faster than `make test`).
test-parallel:
	$(UNITTEST_PARALLEL)

# The same, against the Postgres at $$DATABASE_URL (an empty database is fine; see docs/src/content/docs/contributing/development.md).
test-pg:
	@test -n "$$DATABASE_URL" || { echo "Set DATABASE_URL, e.g. postgresql://runway:runway@127.0.0.1:5432/runway" >&2; exit 1; }
	$(UNITTEST_PARALLEL)

# Applies ruff's safe fixes (unused imports and the like); review the diff before committing.
fix:
	$(RUFF) check --fix .

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

docs/node_modules: docs/package-lock.json
	cd docs && $(NPM) ci --no-audit --no-fund
	touch docs/node_modules

# The documentation site (Astro Starlight), with live reload at http://localhost:4321/Runway/.
docs: docs/node_modules
	cd docs && $(NPM) run dev

# Builds the site into docs/dist and checks every link between its pages, as docs.yml does.
docs-build: docs/node_modules
	cd docs && $(NPM) run build

# Runs the real app on made-up demo data and drives it with Playwright: each page at phone, tablet and desktop widths, plus
# the scripted flows in frontend/verify/flows. Screenshots, console errors and failed requests go to artifacts/verify/; it
# fails on a console error or a 5xx. `make verify PAGES="budget setup"` visits only those pages.
verify: frontend/node_modules frontend-build
	$(PYTHON) run.py verify $(PAGES)

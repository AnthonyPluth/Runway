# The checks to run before you push: `make check` runs ruff, mypy, Runway's own Semgrep rules, the feature map's and the API
# contract's checks, the Python tests (in parallel, as CI does), the web app's type-check, ESLint, Vitest tests and build,
# and the docs site's build, each once. The other targets run one part of it. The security scans (Semgrep's registry packs, Trivy, zizmor, pip-audit, npm audit, CodeQL)
# run only in CI, on the pull requests they can affect.
# Python commands go through Poetry, as in docs/src/content/docs/contributing/development.md.

PYTHON ?= poetry run python
NPM ?= npm
# The tools come from the Poetry environment (pyproject.toml's dev group, which `poetry install` adds), at the
# versions CI runs. mypy has to run there anyway, to see Runway's dependencies.
RUFF ?= poetry run ruff
MYPY ?= poetry run mypy
UNITTEST_PARALLEL ?= poetry run unittest-parallel -t . -s tests -j 4
# Runway's own Semgrep rules (.semgrep/runway.yml), at the version .github/workflows/security.yml pins (change both
# together). Semgrep isn't in the Poetry environment: it pins dependencies of its own and doesn't run on Python 3.14,
# so it goes through pipx, on pipx's own Python.
SEMGREP ?= pipx run semgrep==1.146.0

.PHONY: check lint python-lint frontend-lint semgrep test test-parallel test-pg fix \
	frontend-check frontend-typecheck frontend-test frontend-build docs docs-build feature-map feature-map-check \
	api-contract api-contract-check

# frontend-lint is a prerequisite of both lint and frontend-check, and make runs it once.
check: lint feature-map-check api-contract-check test-parallel frontend-check docs-build

# Ruff and mypy for Python, ESLint over the web app, the extension and runway/static (the same lint CI runs), and
# Runway's own Semgrep rules.
lint: python-lint frontend-lint semgrep

python-lint:
	$(RUFF) check .
	$(MYPY)

# Runway's own rules for the paved paths (.semgrep/runway.yml): first that each rule flags its failing examples and only
# those (.semgrep/examples/), then the code. The ESLint rules' examples are frontend/src/lint-rules.test.ts.
semgrep:
	$(PYTHON) .semgrep/check_examples.py $(SEMGREP)
	$(SEMGREP) scan --metrics=off --disable-version-check --error --config .semgrep/runway.yml runway run.py

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

# The feature map (docs/feature-map.json and its docs page): each route's handler, web app callers, tests and docs.
# feature-map-check fails when it is out of date or a route has no test (tools/feature_map_allowlist.txt only shrinks).
feature-map:
	$(PYTHON) tools/feature_map.py

feature-map-check:
	$(PYTHON) tools/feature_map.py --check

# The API contract (docs/openapi.json and frontend/src/lib/api-types.ts), generated from the routes and the types their
# handlers are annotated with (runway/server/contract.py). api-contract-check fails when either is out of date.
api-contract:
	$(PYTHON) tools/api_contract.py

api-contract-check:
	$(PYTHON) tools/api_contract.py --check

# The checks to run before you push: `make check` runs ruff, mypy, import-linter, Runway's own Semgrep rules, the fleet
# checks, the feature map's and the API contract's checks, the Python tests (in parallel, as CI does), the web app's
# type-check, ESLint, Vitest tests and build, and the docs site's build, each once. The other targets run one part of it. The security scans (Semgrep's registry packs, Trivy, zizmor, pip-audit, npm audit, CodeQL)
# run only in CI, on the pull requests they can affect.
# Python commands go through Poetry, as in docs/src/content/docs/contributing/development.md.

PYTHON ?= poetry run python
NPM ?= npm
# The tools come from the Poetry environment (pyproject.toml's dev group, which `poetry install` adds), at the
# versions CI runs. mypy has to run there anyway, to see Runway's dependencies.
RUFF ?= poetry run ruff
MYPY ?= poetry run mypy
LINT_IMPORTS ?= poetry run lint-imports
UNITTEST_PARALLEL_BASE ?= poetry run unittest-parallel -t . -s tests
UNITTEST_PARALLEL ?= $(UNITTEST_PARALLEL_BASE) -j 4
# The Postgres run uses as many processes as the machine has CPUs, at least 4 and at most 16 (tools/test_jobs.sh): its
# processes wait on the database more than on the CPU. Its Postgres needs max_connections of at least 30 per process
# plus 100 (docs/src/content/docs/contributing/development.md).
PG_JOBS ?= $(shell tools/test_jobs.sh)
# Runway's own Semgrep rules (.semgrep/runway.yml), at the version .github/workflows/security.yml pins (change both
# together). Semgrep isn't in the Poetry environment: it pins dependencies of its own and doesn't run on Python 3.14,
# so it goes through pipx, on pipx's own Python.
SEMGREP ?= pipx run semgrep==1.146.0

.PHONY: check lint python-lint frontend-lint semgrep test test-parallel test-pg test-timings fix \
	frontend-check frontend-typecheck frontend-test frontend-build docs docs-build verify feature-map feature-map-check \
	fleet-checks api-contract api-contract-check screenshots verify-twice pre-push setup verify-skill control-smoke

# frontend-lint is a prerequisite of both lint and frontend-check, and make runs it once.
check: lint feature-map-check api-contract-check fleet-checks test-parallel frontend-check docs-build

# Ruff, mypy and import-linter's boundaries (the contracts in pyproject.toml) for Python, ESLint over the web app, the
# extension and runway/static (the same lint CI runs), and Runway's own Semgrep rules.
lint: python-lint frontend-lint semgrep

python-lint:
	$(RUFF) check .
	$(MYPY)
	$(LINT_IMPORTS)

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
	$(UNITTEST_PARALLEL_BASE) -j $(PG_JOBS)

# The same tests (on SQLite, or on Postgres when $$DATABASE_URL is set), printing where the time goes: the slowest tests
# and classes, and the time spent making and giving back databases (tests/timing_result.py, tools/test_timings.py).
# Not part of `make check`. TEST_ARGS passes more arguments, e.g. TEST_ARGS="-k tests.test_backup.".
test-timings:
	rm -rf .timings
	TIMINGS_DIR=.timings PYTHONPATH=. $(UNITTEST_PARALLEL_BASE) -j $(if $(DATABASE_URL),$(PG_JOBS),4) \
		--result tests.timing_result.TimingResult $(TEST_ARGS) || status=$$?; \
	$(PYTHON) tools/test_timings.py .timings || report=$$?; exit $${status:-$${report:-0}}

# Applies ruff's safe fixes (unused imports and the like); review the diff before committing.
fix:
	$(RUFF) check --fix .

# A fresh checkout's dependencies: Poetry's environment (Python and its dev tools) and the web app's packages. The cloud
# container does this itself (.claude/hooks/session-start.sh); on your own machine it's the first command to run.
setup:
	poetry install --no-root --no-interaction
	cd frontend && $(NPM) ci --no-audit --no-fund

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

# Runs the real app on made-up demo data and drives it with Playwright: each page at phone and desktop widths, plus
# the scripted flows in frontend/verify/flows. Full-page screenshots (and each page's first screen), console errors and
# failed requests go to artifacts/verify/ (only when every page and flow passed); it fails on a console error, a 5xx, a
# failed step or a page that never finished loading, after retrying what failed. One run at a time per machine: a second
# waits for the first. `make verify PAGES="budget setup budget-edit"` runs only those pages (with the flows that start
# on them) and flows.
verify: frontend/node_modules frontend-build
	$(PYTHON) run.py verify $(PAGES)

# make verify, run twice on fresh data: fails on any image that isn't byte for byte the same both times (nondeterminism).
verify-twice: frontend/node_modules frontend-build
	$(PYTHON) run.py verify --twice $(PAGES)

# tools/control's command reference in the verify-runway skill (.claude/skills/verify-runway/references/cli.md),
# generated from the CLI's own help: run it after changing a command. make fleet-checks fails when it's out of date.
verify-skill:
	node frontend/verify/control.mjs help --markdown > .claude/skills/verify-runway/references/cli.md

# tools/control end to end on the real demo server under the frozen clock: doctor, start, snapshot, screenshot (checked
# byte for byte against screenshots/overview-phone.png), a flow, reset and stop (frontend/verify/control-smoke.mjs; the
# Verify flows workflow runs it too). Needs what make verify needs; writes only under artifacts/control-smoke/.
control-smoke: frontend/node_modules frontend-build
	RUNWAY_PYTHON="$$($(PYTHON) -c 'import sys; print(sys.executable)')" node frontend/verify/control-smoke.mjs

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

# Runs make verify, then makes screenshots/ (committed: the one directory of every page's and flow's full-page
# screenshot at each width, and each page's first screen, which the README and the docs show) match it
# (tools/sync_screenshots.py). A UI change commits the result, so what changed shows as image diffs on the pull request.
# Demo data only: the repository is public. Nothing is copied unless every page and flow passed; `make screenshots
# PAGES="budget budget-edit"` updates only those pages' and flows' images and leaves the rest.
screenshots: frontend/node_modules frontend-build
	$(PYTHON) run.py verify $(PAGES)
	$(PYTHON) tools/sync_screenshots.py

# Checks that replaced instructions (tools/fleet_checks.py): one Alembic head, a test for each new migration, the
# workflows' conventions, and the Co-Authored-By trailer on your commits since main, as CI checks a pull request's.
fleet-checks:
	$(PYTHON) tools/fleet_checks.py --commits origin/main..HEAD

# The quick checks for what fails CI most often, a minute or so, to run before each push (it doesn't replace make check,
# which you run once before the last one): the feature map, API contract and fleet checks, and the tests that read
# shared fixtures (the demo seed, the golden forecast, the contract's sample data, migrations, backups).
SHARED_FIXTURE_TESTS = test_demo test_api_contract test_forecast_golden test_migrations test_backup test_api_handlers \
	test_api_queries test_api_validation test_budget_months test_budget_suggest test_churning_api
pre-push: feature-map-check api-contract-check fleet-checks
	poetry run unittest-parallel -t . -s tests -j 4 $(foreach m,$(SHARED_FIXTURE_TESTS),-k tests.$(m).)

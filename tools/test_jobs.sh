#!/usr/bin/env bash
# How many processes the Postgres tests run in (make test-pg, CI): one per CPU, at least 4 and at most 16. They wait on
# the database more than on the CPU, so a small runner still gets 4; past 16 they mostly queue for the same locks.
set -eo pipefail
cpus="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"
floor=4
cap=16
echo "$(( cpus < floor ? floor : cpus > cap ? cap : cpus ))"

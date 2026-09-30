"""Write a shields.io endpoint badge:  python coverage_badge.py REPORT.json badge.json [label]

REPORT is coverage.py's JSON report (the backend) or Vitest's coverage-summary.json (the frontend). CI publishes the
result on GitHub Pages, and the README's badges are shields.io endpoint badges that read it. Prints the percentage."""
import json
import sys


def color(pct: int) -> str:
    return "#4c1" if pct >= 90 else "#97ca00" if pct >= 80 else "#dfb317" if pct >= 70 else "#fe7d37" if pct >= 60 else "#e05d44"


def percent(report: dict) -> int:
    """Rounded down, so the badge never claims more."""
    if "totals" in report:   # coverage.py
        return int(report["totals"]["percent_covered"])
    return int(report["total"]["lines"]["pct"])   # Vitest (istanbul's json-summary)


def endpoint(pct: int, label: str = "coverage") -> dict:
    """The JSON shields.io's endpoint badge reads."""
    return {"schemaVersion": 1, "label": label, "message": f"{pct}%", "color": color(pct)}


if __name__ == "__main__":
    report, out = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else "coverage"
    with open(report) as f:
        pct = percent(json.load(f))
    with open(out, "w") as f:
        json.dump(endpoint(pct, label), f)
        f.write("\n")
    print(pct)

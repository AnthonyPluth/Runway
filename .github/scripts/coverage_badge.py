"""Draw the README's coverage badge from coverage.py's JSON report:  python coverage_badge.py coverage.json badge.svg

The repository is private, so a badge service can't read the number; the badge is an SVG in the repository instead,
redrawn by CI on main when the whole-number percentage changes. Prints the percentage."""
import json
import sys

LABEL = "coverage"
CHAR = 6.6   # average width of a character at 11px Verdana, near enough for these few characters


def color(pct: int) -> str:
    return "#4c1" if pct >= 90 else "#97ca00" if pct >= 80 else "#dfb317" if pct >= 70 else "#fe7d37" if pct >= 60 else "#e05d44"


def badge(pct: int) -> str:
    value = f"{pct}%"
    lw, vw = round(len(LABEL) * CHAR + 12), round(len(value) * CHAR + 12)
    w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{LABEL}: {value}">
<title>{LABEL}: {value}</title>
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/><rect x="{lw}" width="{vw}" height="20" fill="{color(pct)}"/><rect width="{w}" height="20" fill="url(#s)"/></g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
<text x="{lw / 2}" y="15" fill="#010101" fill-opacity=".3">{LABEL}</text><text x="{lw / 2}" y="14">{LABEL}</text>
<text x="{lw + vw / 2}" y="15" fill="#010101" fill-opacity=".3">{value}</text><text x="{lw + vw / 2}" y="14">{value}</text>
</g></svg>
"""


if __name__ == "__main__":
    report, out = sys.argv[1], sys.argv[2]
    with open(report) as f:
        pct = int(json.load(f)["totals"]["percent_covered"])   # rounded down, so the badge never claims more
    with open(out, "w") as f:
        f.write(badge(pct))
    print(pct)

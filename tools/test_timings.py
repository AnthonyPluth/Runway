"""Sums up the timings tests/timing_result.py wrote (`make test-timings` runs both): the tests' total time, the time
spent making and giving back databases, and the slowest tests and classes.

    python tools/test_timings.py .timings [--top 25]
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def load(directory: Path) -> tuple[list[dict], dict[str, float], int]:
    """Every file's tests, their phases summed, and the connections the schema pool had to end."""
    timed: list[dict] = []
    phases: dict[str, float] = defaultdict(float)
    ended = 0
    for p in sorted(directory.glob("*.json")):
        data = json.loads(p.read_text())
        timed.extend(data["tests"])
        ended += data.get("ended", 0)
        for name, seconds in data["phases"].items():
            phases[name] += seconds
    return timed, dict(phases), ended


def report(timed: list[dict], phases: dict[str, float], top: int, ended: int = 0) -> str:
    total = sum(t["seconds"] for t in timed)
    classes: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for t in timed:
        entry = classes[f"{t['module']}.{t['class']}"]
        entry[0] += t["seconds"]
        entry[1] += 1
    lines = [f"{len(timed)} tests, {total:.1f}s of test time summed over all processes",
             "Database set-up and clean-up, summed over all processes (including what a whole class shares):"]
    lines += [f"  {name}: {seconds:.1f}s" for name, seconds in sorted(phases.items())]
    lines.append(f"Connections ended to give a schema back (a test left one open): {ended}")
    lines += ["", f"Slowest {top} tests"]
    for t in sorted(timed, key=lambda t: -t["seconds"])[:top]:
        extra = " ".join(f"{k}={v:.2f}" for k, v in t["phases"].items())
        lines.append(f"  {t['seconds']:7.2f}s  {t['module']}.{t['class']}.{t['test']}  {extra}".rstrip())
    lines += ["", f"Slowest {top} classes (their tests' time summed)"]
    for name, (seconds, count) in sorted(classes.items(), key=lambda kv: -kv[1][0])[:top]:
        lines.append(f"  {seconds:7.1f}s  {int(count):4d} tests  {name}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sum up the timings tests.timing_result.TimingResult wrote")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()
    timed, phases, ended = load(args.directory)
    print(report(timed, phases, args.top, ended))


if __name__ == "__main__":
    main()

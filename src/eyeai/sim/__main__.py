"""`python -m eyeai.sim <scenario>`: replay a scenario and print its timeline.

Exit status 0 when every expected derived kind was produced, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys

from eyeai.schema import Event
from eyeai.sim.replay import run
from eyeai.sim.scenarios import JAKARTA, SCENARIOS, get_scenario


def _describe(step: Event | object) -> str:
    if isinstance(step, Event):
        return f"{step.device_id:<14} {step.provenance.value:<11} {step.kind:<16} {step.label or ''}"
    return "tick"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eyeai.sim", description=__doc__)
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS), help="scenario name")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    args = parser.parse_args(argv)

    if args.list or args.scenario is None:
        for name in sorted(SCENARIOS):
            print(f"{name:<24} {get_scenario(name).description}")
        return 0 if args.list else 2

    scenario = get_scenario(args.scenario)
    result = run(scenario)

    print(f"scenario: {scenario.name}")
    print(scenario.description)
    print()
    for record in result.records:
        when = record.at.astimezone(JAKARTA).strftime("%a %H:%M:%S")
        print(f"{when}  {_describe(record.step)}")
        if record.outcome:
            print(f"          outcome: {', '.join(record.outcome)}")
        for d in record.derived:
            print(f"    -> {d.kind:<20} {d.severity:<8} {d.label or ''}  {d.attributes}")
    print()
    print(f"expected: {', '.join(scenario.expect)}")
    missing = result.missing()
    print(f"missing:  {', '.join(missing) if missing else 'none'}")
    print("stats:    " + ", ".join(f"{k}={v}" for k, v in sorted(result.stats.items())))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())

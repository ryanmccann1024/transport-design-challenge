"""Command line interface for the lab."""

import argparse
from collections import Counter
from functools import partial
import json
from pathlib import Path
import sys

from .checks import case_problems, outage_exceptions
from .metrics import with_metrics
from .model import load_policy, load_scenario, simulate
from .scenarios import PUBLIC
from .timeline import timeline, update_name


def print_summary(scenario, result, *, checks=None, stream=None):
    write = partial(print, file=stream)
    def mark(group):
        if checks is None:
            return ""
        return " [FAIL]" if checks[group] else " [PASS]"

    status = "" if checks is None else (" [FAIL]" if any(checks.values()) else " [PASS]")
    write(f'Case: {result["scenario"]}{status}')
    write("\nVideo" + mark("video"))
    complete = result["upload_complete_ms"]
    limit = result["limit_ms"] / 1_000
    size = (f"{scenario.upload_bytes // 1_000_000} MB"
            if scenario.upload_bytes >= 1_000_000 and scenario.upload_bytes % 1_000_000 == 0
            else f"{scenario.upload_bytes:,} B")
    write(f"  Target: {size} by {limit:g} s")
    if complete is None:
        write(f'  Incomplete at {limit:g} s '
              f'({result["upload_received_bytes"]:,}/{result["upload_bytes"]:,} B)')
    else:
        write(f"  Completed in {complete / 1_000:.3f} s (limit {limit:g} s)")
    write(f'  Peak shared queue: {result["peak_queue_bytes"]:,} / '
          f'{scenario.router_queue_bytes:,} B' + mark("queue"))

    updates = result["commands"]
    metrics = result["command_metrics"]
    write("\nCrew updates" + mark("updates"))
    if checks is not None:
        exceptions = outage_exceptions(scenario)
        deadlines = Counter(command.ttl_ms for command in scenario.commands
                            if command.id not in exceptions)
        for deadline, count in sorted(deadlines.items()):
            updates_due = "1 update" if count == 1 else f"{count} updates"
            each = "" if count == 1 else " each"
            write(f"  Target: {updates_due} must arrive in under {deadline:,} ms{each}")
        if exceptions:
            write(f"  {len(exceptions)} additional updates follow the outage rules")
        if checks["updates"]:
            write(f'  {len(checks["updates"])} updates missed the case requirements. '
                  "Use --log to inspect those outcomes.")
    write(f'  Delivered: {metrics["delivered"]}/{len(updates)}')
    write("  " + "  |  ".join(
        f"{key.capitalize()}: {metrics[key]}" for key in ("expired", "dropped", "pending")
    ))
    if metrics["delivered"]:
        write("  Delivered latency: " + "  |  ".join(
            f"p{p} {metrics[f'p{p}_ms']} ms" for p in (50, 90, 99)
        ))
    if checks is None and 0 < len(updates) <= 10:
        write()
        for id_, record in updates.items():
            delay = record.get("latency_ms")
            timing = "" if delay is None else f" in {delay} ms"
            attempts = record["attempts"]
            unit = "attempt" if attempts == 1 else "attempts"
            label = update_name(scenario, id_)
            write(f'  {label}: {record["status"]}{timing} ({attempts} {unit})')


def save_log(path, runs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as log:
        for scenario, result, events in runs:
            updates = []
            for at_ms, kind, detail in timeline(scenario, result, events=events):
                if kind != "tick":
                    updates.append(detail)
                    continue
                fields = [f"case={scenario.name}", f"{at_ms / 1_000:.3f} s", detail]
                if updates:
                    fields.append("events: " + "; ".join(updates))
                print(" | ".join(fields), file=log)
                updates.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case", choices=PUBLIC, help="run one published scenario")
    group.add_argument("--all", action="store_true", help="run all five published scenarios")
    group.add_argument("--scenario-file", type=Path, help="run a scenario defined in JSON")
    parser.add_argument("--policy", type=Path, default=Path("policy.json"))
    parser.add_argument("--out", type=Path, help="save detailed JSON evidence")
    parser.add_argument("--log", type=Path, help="save every simulation step, with queue, sends, and garage arrivals")
    parser.add_argument("--trace", metavar="UPDATE_ID", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.all and args.trace is not None:
        parser.error("--trace requires a single case. Use --log to follow events in all cases.")
    try:
        if args.all:
            scenarios = tuple(PUBLIC.values())
        else:
            scenario = PUBLIC[args.case] if args.case else load_scenario(args.scenario_file)
            scenarios = (scenario,)
            if args.trace is not None and all(item.id != args.trace for item in scenario.commands):
                raise ValueError(f"no update {args.trace!r} in case {scenario.name!r}")
        if args.log and args.out and args.log.resolve() == args.out.resolve():
            raise ValueError("--log and --out need different paths")

        policy = load_policy(args.policy)
        runs = []
        for scenario in scenarios:
            events = []
            observation = events.append if args.log else None
            result = simulate(scenario, policy, log=observation)
            result = with_metrics(result)
            runs.append((scenario, result, events))
        if args.out:
            evidence = {scenario.name: result for scenario, result, _ in runs} if args.all else runs[0][1]
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        if args.log:
            save_log(args.log, runs)
    except (OSError, TypeError, ValueError) as exc:
        print(f"netlab: {exc}", file=sys.stderr)
        return 2

    failed = False
    for index, (scenario, result, _) in enumerate(runs):
        if index:
            print()
        checks = None if args.scenario_file else case_problems(scenario, result)
        if checks is not None and any(checks.values()):
            failed = True
        print_summary(scenario, result, checks=checks)
    if args.trace is not None:
        print(f"\nTrace: {update_name(scenario, args.trace)}\n")
        for at_ms, _, detail in timeline(scenario, result, focus=args.trace):
            print(f"  {at_ms / 1_000:7.3f} s  {detail}")
    if args.out:
        print(f"\nSaved evidence to {args.out}")
    if args.log:
        print(f"\nSaved timeline log to {args.log}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

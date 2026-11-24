"""Check the published workloads and show the measurements behind each result."""

import os
from pathlib import Path
import sys
import unittest

from netlab.checks import case_problems
from netlab.metrics import with_metrics
from netlab.model import load_policy, simulate
from netlab.scenarios import PUBLIC


def describe_problems(problems):
    shown = problems[:3]
    if len(problems) > len(shown):
        shown.append(f"Plus {len(problems) - len(shown)} more failed checks in this case.")
    return " ".join(shown)


def show_report(results, problems):
    headings = ("Case", "Check", "Clip time / limit (s)", "Delivered", "p50 / p90 / p99 (ms)")
    rows = []
    for name, result in results.items():
        complete = result["upload_complete_ms"]
        time = "incomplete" if complete is None else f"{complete / 1_000:.3f}"
        metrics = result["command_metrics"]
        latency = " / ".join(
            str(metrics[f"p{p}_ms"]) if metrics["delivered"] else "—" for p in (50, 90, 99)
        )
        rows.append((
            name, "FAIL" if problems[name] else "PASS",
            f"{time} / {result['limit_ms'] / 1_000:g}",
            f"{metrics['delivered']}/{len(result['commands'])}", latency,
        ))

    notes = [
        "Percentiles use delivered updates only.",
    ]
    notes.append("The outage case allows some updates to expire or be dropped. See the log for details.")

    widths = [max(map(len, column)) for column in zip(headings, *rows)]

    def terminal_row(row):
        return "  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()

    print("\nPublic case results", terminal_row(headings), *map(terminal_row, rows),
          *notes, sep="\n", file=sys.stderr)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as report:
            print("## Public case results\n", file=report)
            for row in (headings, ("---",) * len(headings), *rows):
                print("| " + " | ".join(row) + " |", file=report)
            print("\n" + "  \n".join(notes), file=report)
            for case, failures in problems.items():
                if failures:
                    print(f"\n- {case}: {describe_problems(failures)}", file=report)


class PublicChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        policy_path = Path(__file__).resolve().parents[1] / "policy.json"
        policy = load_policy(policy_path)
        cls.results = {
            name: with_metrics(simulate(scenario, policy))
            for name, scenario in PUBLIC.items()
        }
        cls.problems = {
            name: [detail for group in case_problems(PUBLIC[name], result).values() for detail in group]
            for name, result in cls.results.items()
        }

    @classmethod
    def tearDownClass(cls):
        show_report(cls.results, cls.problems)

    def assert_case(self, name):
        problems = self.problems[name]
        if problems:
            self.fail(describe_problems(problems))

    def test_healthy(self):
        self.assert_case("healthy")

    def test_busy(self):
        self.assert_case("busy")

    def test_outage(self):
        self.assert_case("outage")

    def test_medium(self):
        self.assert_case("medium")

    def test_large(self):
        self.assert_case("large")

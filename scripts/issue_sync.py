#!/usr/bin/env python3
"""Turn the run report into exactly one GitHub issue per broken adapter.

The contract is deliberately narrow: one open issue per source, a comment when
the error changes, and an automatic close when the source reports again. Anything
noisier gets ignored by whoever maintains this, which defeats the point of
noticing at all.

Runs through the `gh` CLI so it needs no extra dependency in the workflow.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

LABEL = "adapter-failure"
REPORT = Path("data/quality/run-report.json")


def gh(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def ensure_label() -> None:
    gh(
        "label",
        "create",
        LABEL,
        "--description",
        "A data source stopped reporting",
        "--color",
        "d03b3b",
        check=False,
    )


def open_issues() -> dict[str, dict]:
    raw = gh("issue", "list", "--label", LABEL, "--state", "open", "--json", "number,title,body")
    issues = json.loads(raw or "[]")
    by_source: dict[str, dict] = {}
    for issue in issues:
        # Title format is fixed so the mapping back to a source is unambiguous.
        if issue["title"].startswith("Adapter failing: "):
            by_source[issue["title"].removeprefix("Adapter failing: ")] = issue
    return by_source


def error_fingerprint(result: dict) -> str:
    return hashlib.sha256(
        f"{result.get('error_type')}|{result.get('error')}".encode()
    ).hexdigest()[:12]


def body_for(result: dict, run_id: str) -> str:
    return "\n".join(
        [
            f"`{result['source_id']}` did not report on run `{run_id}`.",
            "",
            f"**{result.get('error_type')}**",
            "",
            "```",
            (result.get("error") or "no message").strip(),
            "```",
            "",
            "The site keeps serving this source's last-good values with a stale badge, "
            "so nothing on the public page is silently frozen. This issue closes itself "
            "when the adapter reports again.",
            "",
            f"<!-- fingerprint:{error_fingerprint(result)} -->",
        ]
    )


def main() -> int:
    if not REPORT.exists():
        print("no run report — nothing to sync", file=sys.stderr)
        return 0

    report = json.loads(REPORT.read_text())
    run_id = report.get("run_id", "unknown")
    results = {r["source_id"]: r for r in report.get("results", [])}

    ensure_label()
    existing = open_issues()

    for source_id, result in sorted(results.items()):
        issue = existing.get(source_id)

        if result["status"] == "failed":
            if issue is None:
                gh(
                    "issue",
                    "create",
                    "--title",
                    f"Adapter failing: {source_id}",
                    "--label",
                    LABEL,
                    "--body",
                    body_for(result, run_id),
                )
                print(f"opened issue for {source_id}", file=sys.stderr)
            elif error_fingerprint(result) not in (issue.get("body") or ""):
                # Only comment when the failure actually changed; a weekly
                # "still broken" comment on an unchanged error is pure noise.
                gh(
                    "issue",
                    "comment",
                    str(issue["number"]),
                    "--body",
                    f"New failure mode on run `{run_id}`:\n\n```\n{result.get('error')}\n```\n\n"
                    f"<!-- fingerprint:{error_fingerprint(result)} -->",
                )
                print(f"commented on {source_id}", file=sys.stderr)

        elif issue is not None and result["status"] in {"ok", "skipped", "partial"}:
            gh(
                "issue",
                "close",
                str(issue["number"]),
                "--comment",
                f"`{source_id}` reported again on run `{run_id}` "
                f"({result['records']} records, latest observation "
                f"{result.get('latest_observation') or 'unknown'}). Closing automatically.",
            )
            print(f"closed issue for {source_id}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

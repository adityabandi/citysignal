"""citysignal — command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

from .framework.adapter import RunContext
from .framework.config import load_config
from .framework.fetch import Fetcher, StateStore
from .framework.quality import AdapterResult, HealthStore, write_run_report
from .framework.registry import discover_adapters, get_adapter


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s | %(message)s",
        stream=sys.stderr,
    )


def _context(args: argparse.Namespace) -> tuple[RunContext, Fetcher]:
    config = load_config(Path(args.root) if args.root else None)
    data_dir = config.data_dir
    fetcher = Fetcher(offline=getattr(args, "offline", False))
    ctx = RunContext(
        config=config,
        fetcher=fetcher,
        state=StateStore(data_dir / "quality" / "state.json"),
        health=HealthStore(data_dir / "quality" / "health.json"),
        force=getattr(args, "force", False),
        accept_revisions=set(getattr(args, "accept_revision", []) or []),
        dry_run=getattr(args, "dry_run", False),
    )
    return ctx, fetcher


def cmd_fetch(args: argparse.Namespace) -> int:
    ctx, fetcher = _context(args)
    adapters = discover_adapters()

    if args.source:
        selected = {sid: get_adapter(sid) for sid in args.source}
    else:
        selected = {
            sid: cls
            for sid, cls in adapters.items()
            if ctx.config.sources.get(sid, {}).get("enabled", True)
        }

    if not selected:
        print("no adapters selected", file=sys.stderr)
        return 1

    results: list[AdapterResult] = []
    for source_id, adapter_cls in selected.items():
        print(f"→ {source_id}", file=sys.stderr)
        result = adapter_cls().run(ctx)
        results.append(result)
        detail = (
            f"{result.status:<8} records={result.records} added={result.added} "
            f"revised={result.revised} unchanged={result.unchanged} "
            f"skipped={result.skipped_unchanged} {result.duration_s}s"
        )
        print(f"  {detail}", file=sys.stderr)
        if result.error:
            print(f"  ! {result.error_type}: {result.error}", file=sys.stderr)

    ctx.state.save()
    ctx.health.save()
    write_run_report(
        ctx.data_dir / "quality" / "run-report.json",
        results,
        run_id=uuid.uuid4().hex[:12],
    )
    fetcher.close()

    failed = [r.source_id for r in results if r.status == "failed"]
    if failed:
        print(f"\n{len(failed)} adapter(s) failed: {', '.join(failed)}", file=sys.stderr)
    # A failing adapter is a data-level event, not a pipeline failure: the run
    # report carries it to the issue bot and the site keeps its last-good values.
    return 0


def cmd_derive(args: argparse.Namespace) -> int:
    from .derive.engine import run_derive

    config = load_config(Path(args.root) if args.root else None)
    summary = run_derive(config)
    print(
        f"derived {summary['metrics']} metrics across {summary['cities']} cities "
        f"→ {summary['output_files']} files",
        file=sys.stderr,
    )
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    import json

    config = load_config(Path(args.root) if args.root else None)
    path = config.data_dir / "quality" / "health.json"
    if not path.exists():
        print("no health.json yet — run `citysignal fetch` first", file=sys.stderr)
        return 1
    data = json.loads(path.read_text())
    rows = sorted(data.get("sources", {}).values(), key=lambda r: (r.get("status") != "failed", r["source_id"]))
    width = max((len(r["source_id"]) for r in rows), default=10)
    print(f"{'source':<{width}}  {'status':<8} {'latest obs':<11} {'stale(d)':>8}  last checked")
    for row in rows:
        stale = row.get("staleness_days")
        print(
            f"{row['source_id']:<{width}}  {row.get('status', '?'):<8} "
            f"{str(row.get('latest_observation') or '—'):<11} "
            f"{'—' if stale is None else stale:>8}  {row.get('last_checked', '—')}"
        )
        if row.get("last_error"):
            print(f"{'':<{width}}  ! {row['last_error'][:110]}")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    code = cmd_fetch(args)
    if code != 0:
        return code
    return cmd_derive(args)


def cmd_list(args: argparse.Namespace) -> int:
    config = load_config(Path(args.root) if args.root else None)
    for source_id, cls in discover_adapters().items():
        enabled = config.sources.get(source_id, {}).get("enabled", True)
        manifest = cls.manifest
        flag = "" if enabled else "  (disabled)"
        print(f"{source_id:<18} {manifest.cadence:<10} {manifest.geo_level:<13} {manifest.kind}{flag}")
    return 0


def cmd_forecast(args: argparse.Namespace) -> int:
    """Evaluate every target, freeze what can be forecast, score what has matured.

    Freezing and scoring are separate passes on purpose: a forecast is written
    once and never touched again, and scoring only ever appends. Running this
    twice in the same period must change nothing.
    """
    from .forecast.engine import ForecastEngine

    config = load_config(Path(args.root) if args.root else None)
    engine = ForecastEngine(config)
    payload = engine.run(city_slug=args.city)

    issued = [f for f in payload["forecasts"] if f.get("status") == "issued"]
    blocked = [f for f in payload["forecasts"] if f.get("status") != "issued"]

    print(f"vintage {payload['data_vintage']}")
    basis = payload["vintage_basis"]
    print(
        f"  {basis['published_at']} rows carry a real publication date, "
        f"{basis['declared_lag']} rest on a declared lag"
    )

    for record in issued:
        skill = record["skill"].get(record["model"], {})
        print(
            f"  {record['target_id']:<22} {record['for_period']:<8} "
            f"{record['model']:<15} {record['model_verdict']:<24} "
            f"ratio={skill.get('pinball_ratio', float('nan')):.3f}"
        )
    for record in blocked:
        detail = (
            f"{record.get('observations', '?')} obs, needs {record.get('needed', '?')}"
            if record["status"] == "insufficient_data"
            else record.get("kind", "")
        )
        print(f"  {record['target_id']:<22} {record['status']:<18} {detail}")

    if not args.no_freeze:
        written = engine.freeze(payload)
        print(f"froze {len(written)} new forecast(s); existing files untouched")

    scored = engine.score_matured()
    print(f"scored {scored['scored']} matured, {scored['pending']} still pending")

    out = config.data_dir / "derived" / "forecasts.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="citysignal", description=__doc__)
    parser.add_argument("--root", help="repository root (defaults to the installed package's repo)")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="fetch sources into data/history")
    fetch.add_argument("--source", action="append", help="one source_id; repeatable")
    fetch.add_argument("--all", action="store_true", help="every enabled adapter (default)")
    fetch.add_argument("--force", action="store_true", help="ignore content-hash skipping")
    fetch.add_argument("--offline", action="store_true", help="fail rather than hit the network")
    fetch.add_argument("--dry-run", action="store_true", help="validate but do not write history")
    fetch.add_argument(
        "--accept-revision",
        action="append",
        help="allow this source to restate previously published values this run",
    )
    fetch.set_defaults(func=cmd_fetch)

    derive = sub.add_parser("derive", help="rebuild indices, regimes and site JSON")
    derive.set_defaults(func=cmd_derive)

    forecast = sub.add_parser("forecast", help="evaluate targets, freeze forecasts, score matured")
    forecast.add_argument("--city", default="madrid", help="city slug (default madrid)")
    forecast.add_argument(
        "--no-freeze", action="store_true", help="evaluate without writing new frozen forecasts"
    )
    forecast.set_defaults(func=cmd_forecast)

    health = sub.add_parser("health", help="print source health")
    health.set_defaults(func=cmd_health)

    listing = sub.add_parser("list", help="list registered adapters")
    listing.set_defaults(func=cmd_list)

    everything = sub.add_parser("all", help="fetch then derive")
    everything.add_argument("--source", action="append")
    everything.add_argument("--force", action="store_true")
    everything.add_argument("--offline", action="store_true")
    everything.add_argument("--dry-run", action="store_true")
    everything.add_argument("--accept-revision", action="append")
    everything.set_defaults(func=cmd_all)

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

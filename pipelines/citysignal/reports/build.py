"""Report generator for CitySignal: PDF briefs for investors and estate agents."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Agent:
    """Real estate agent branding for district reports."""

    name: str
    company: str
    phone: str
    email: str
    logo_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_metrics(config_path: Path) -> dict[str, dict[str, Any]]:
    """Load metrics.yml and return a dict of metric_id -> metric config."""
    with open(config_path) as f:
        config = yaml.safe_load(f)
    return config.get("metrics", {})


def _get_metric_description(metric_id: str, metrics: dict) -> str:
    """Get the 'plain' one-line description of a metric."""
    return metrics.get(metric_id, {}).get("plain", "")


def _load_forecasts(data_dir: Path) -> dict[str, Any]:
    """Load the current forecast round."""
    path = data_dir / "derived" / "forecasts.json"
    if not path.exists():
        logger.warning("forecasts.json not found; proceeding with empty forecasts")
        return {"forecasts": [], "as_of": "", "city": "madrid"}
    with open(path) as f:
        return json.load(f)


def _load_track_record(data_dir: Path) -> dict[str, Any]:
    """Load the frozen forecasts and scores."""
    path = data_dir / "derived" / "track-record.json"
    if not path.exists():
        logger.warning("track-record.json not found; proceeding with empty track record")
        return {"frozen": [], "scores": [], "pending": 0}
    with open(path) as f:
        return json.load(f)


def _load_madrid(data_dir: Path) -> dict[str, Any]:
    """Load the Madrid city data (regime, indices, districts, etc.)."""
    path = data_dir / "derived" / "cities" / "madrid.json"
    if not path.exists():
        raise FileNotFoundError(f"madrid.json not found at {path}")
    with open(path) as f:
        return json.load(f)


def _load_agent(agent_path: Path | None) -> Agent:
    """Load agent branding from YAML, or return a sample agent."""
    if not agent_path or not agent_path.exists():
        logger.info("Using sample agent branding")
        return Agent(
            name="Agente de Ejemplo",
            company="Inmobiliaria Ejemplo S.L.",
            phone="000-000-0000",
            email="info@example.com",
            logo_path="",
        )
    with open(agent_path) as f:
        cfg = yaml.safe_load(f) or {}
    return Agent(
        name=cfg.get("name", ""),
        company=cfg.get("company", ""),
        phone=cfg.get("phone", ""),
        email=cfg.get("email", ""),
        logo_path=cfg.get("logo_path", ""),
    )


def _build_brief_payload(
    forecasts: dict[str, Any],
    track_record: dict[str, Any],
    madrid: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Build the JSON payload for the investor brief."""

    # Collect issued forecasts (those with quantiles and model verdict)
    issued = [f for f in forecasts.get("forecasts", []) if f.get("status") == "issued"]

    # Prepare forecast records with descriptions and model verdict
    forecast_records = []
    for f in issued:
        skill = f.get("skill", {}).get(f.get("model", ""), {})
        verdict = skill.get("verdict", "unknown")
        is_baseline = verdict == "baseline" or verdict == "no_better_than_baseline"

        record = {
            "target_id": f.get("target_id", ""),
            "question": f.get("question", ""),
            "description": _get_metric_description(f.get("metric_id", ""), metrics),
            "p10": f.get("quantiles", {}).get("p10"),
            "p50": f.get("quantiles", {}).get("p50"),
            "p90": f.get("quantiles", {}).get("p90"),
            "unit": f.get("unit", ""),
            "model": f.get("model", ""),
            "model_verdict": f.get("model_verdict", ""),
            "skill_verdict": verdict,
            "skill_ratio": skill.get("pinball_ratio"),
            "does_not_beat_baseline": is_baseline,
        }
        forecast_records.append(record)

    # Track record summary
    frozen = track_record.get("frozen", [])
    scores = track_record.get("scores", [])
    pending = track_record.get("pending", 0)

    track_record_summary = {
        "total_issued": len(frozen),
        "scored": len(scores),
        "pending": pending,
        "forecasts": scores,  # Include scored forecasts for detail
    }

    # Regime and indices
    regime = madrid.get("regime", {})
    indices = madrid.get("indices", {})

    index_verdicts = {}
    for idx_name, idx_data in indices.items():
        index_verdicts[idx_name] = {
            "label": idx_data.get("label", ""),
            "question": idx_data.get("question", ""),
            "value": idx_data.get("value"),
            "period": idx_data.get("period", ""),
        }

    # Districts ranked by rent
    dists_data = madrid.get("districts", {})
    dist_codes = dists_data.get("codes", [])
    metrics_by_district = dists_data.get("metrics", {})

    district_records = []
    rent_metric = metrics_by_district.get("rent_district_m2", {})
    padron_metric = metrics_by_district.get("padron_district", {})
    vacancy_metric = metrics_by_district.get("locales_vacancy_rate", {})
    horeca_metric = metrics_by_district.get("horeca_stock", {})
    vut_metric = metrics_by_district.get("vut_licensed", {})

    for code in dist_codes:
        rent = rent_metric.get("districts", {}).get(code, {}).get("latest", {}).get("value")
        pop = padron_metric.get("districts", {}).get(code, {}).get("latest", {}).get("value")
        vacancy = vacancy_metric.get("districts", {}).get(code, {}).get("latest", {}).get("value")
        horeca = horeca_metric.get("districts", {}).get(code, {}).get("latest", {}).get("value")
        vut = vut_metric.get("districts", {}).get(code, {}).get("latest", {}).get("value")

        if rent is not None:  # Sort by rent only if available
            district_records.append(
                {
                    "code": code,
                    "rent_m2": rent,
                    "population": pop,
                    "vacancy_rate": vacancy,
                    "horeca_stock": horeca,
                    "vut_licensed": vut,
                }
            )

    # Sort by rent descending
    district_records.sort(key=lambda x: x["rent_m2"] or 0, reverse=True)

    return {
        "city": "madrid",
        "month": forecasts.get("as_of", ""),
        "generated_at": forecasts.get("generated_at", ""),
        "regime": {
            "rule_id": regime.get("rule_id", ""),
            "label": regime.get("label", ""),
            "reading": regime.get("reading", ""),
        },
        "indices": index_verdicts,
        "forecasts": forecast_records,
        "track_record": track_record_summary,
        "districts": district_records,
    }


def _build_district_payload(
    district_code: str,
    madrid: dict[str, Any],
    forecasts: dict[str, Any],
    agent: Agent,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Build the JSON payload for a single district report."""

    dists_data = madrid.get("districts", {})
    dist_codes = dists_data.get("codes", [])
    metrics_by_district = dists_data.get("metrics", {})

    # Get all district data for this code
    rent_metric = metrics_by_district.get("rent_district_m2", {})
    padron_metric = metrics_by_district.get("padron_district", {})
    vacancy_metric = metrics_by_district.get("locales_vacancy_rate", {})
    horeca_metric = metrics_by_district.get("horeca_stock", {})
    vut_metric = metrics_by_district.get("vut_licensed", {})
    licences_metric = metrics_by_district.get("licences_granted", {})

    # Extract this district's data
    district_metrics = {
        "rent_m2": rent_metric.get("districts", {}).get(district_code, {}).get("latest", {}).get("value"),
        "population": padron_metric.get("districts", {}).get(district_code, {}).get("latest", {}).get("value"),
        "population_yoy": padron_metric.get("districts", {}).get(district_code, {}).get("yoy"),
        "vacancy_rate": vacancy_metric.get("districts", {}).get(district_code, {}).get("latest", {}).get("value"),
        "horeca_stock": horeca_metric.get("districts", {}).get(district_code, {}).get("latest", {}).get("value"),
        "vut_licensed": vut_metric.get("districts", {}).get(district_code, {}).get("latest", {}).get("value"),
        "licences_granted": licences_metric.get("districts", {}).get(district_code, {}).get("latest", {}).get("value"),
    }

    # Build ranking table (this district vs all others)
    all_districts = []
    for code in dist_codes:
        r = rent_metric.get("districts", {}).get(code, {}).get("latest", {}).get("value")
        if r is not None:
            all_districts.append({"code": code, "rent": r})

    all_districts.sort(key=lambda x: x["rent"] or 0, reverse=True)
    rank = next((i + 1 for i, d in enumerate(all_districts) if d["code"] == district_code), None)

    # Forecast panel (Madrid-level)
    issued = [f for f in forecasts.get("forecasts", []) if f.get("status") == "issued"]
    forecast_records = []
    for f in issued:
        record = {
            "target_id": f.get("target_id", ""),
            "question": f.get("question", ""),
            "p10": f.get("quantiles", {}).get("p10"),
            "p50": f.get("quantiles", {}).get("p50"),
            "p90": f.get("quantiles", {}).get("p90"),
            "unit": f.get("unit", ""),
        }
        forecast_records.append(record)

    return {
        "city": "madrid",
        "district_code": district_code,
        "month": forecasts.get("as_of", ""),
        "generated_at": forecasts.get("generated_at", ""),
        "agent": agent.to_dict(),
        "district_metrics": district_metrics,
        "ranking": {
            "rank": rank,
            "total": len(all_districts),
        },
        "forecasts": forecast_records,
    }


def build_reports(
    kind: str = "all",
    district: str | None = None,
    agent_path: Path | None = None,
    out_dir: Path | None = None,
    root: Path | None = None,
) -> int:
    """Build JSON payloads and optionally compile PDFs.

    Args:
        kind: 'brief', 'district', or 'all'
        district: specific district code (default: all)
        agent_path: path to agent.yml (default: use sample)
        out_dir: output directory (default: reports/out)
        root: repository root (default: current directory)

    Returns:
        0 on success, 1 on error
    """

    if root is None:
        root = Path.cwd()
    if out_dir is None:
        out_dir = root / "reports" / "out"

    out_dir.mkdir(parents=True, exist_ok=True)

    # Load all data
    logger.info("Loading data...")
    forecasts = _load_forecasts(root / "data")
    track_record = _load_track_record(root / "data")
    madrid = _load_madrid(root / "data")
    metrics_cfg = _load_metrics(root / "config" / "metrics.yml")
    agent = _load_agent(agent_path)

    dists_data = madrid.get("districts", {})
    dist_codes = dists_data.get("codes", [])

    # Build and write payloads
    if kind in ("brief", "all"):
        logger.info("Building Madrid brief...")
        brief_payload = _build_brief_payload(forecasts, track_record, madrid, metrics_cfg)
        brief_json = out_dir / "madrid-brief.json"
        with open(brief_json, "w") as f:
            json.dump(brief_payload, f, indent=2, ensure_ascii=False)
        logger.info(f"  wrote {brief_json}")

    if kind in ("district", "all"):
        target_districts = [district] if district else dist_codes
        for dist_code in target_districts:
            logger.info(f"Building district {dist_code}...")
            dist_payload = _build_district_payload(dist_code, madrid, forecasts, agent, metrics_cfg)
            dist_json = out_dir / f"district-{dist_code}.json"
            with open(dist_json, "w") as f:
                json.dump(dist_payload, f, indent=2, ensure_ascii=False)
            logger.info(f"  wrote {dist_json}")

    # Try to compile with typst
    logger.info("Looking for typst compiler...")
    typst_bin = shutil.which("typst")
    if not typst_bin:
        logger.info("typst not found on PATH. To compile PDFs, install typst:")
        logger.info("  macOS (homebrew): brew install typst")
        logger.info("  Linux: download https://github.com/typst/typst/releases")
        logger.info("  Then run: typst compile reports/madrid-brief.typ reports/out/madrid-brief.pdf --input data=reports/out/madrid-brief.json")
        return 0

    logger.info(f"Found typst at {typst_bin}")

    # Compile PDFs
    reports_dir = root / "reports"
    if kind in ("brief", "all"):
        _compile_pdf("madrid-brief", reports_dir, out_dir, typst_bin)
    if kind in ("district", "all"):
        target_districts = [district] if district else dist_codes
        for dist_code in target_districts:
            _compile_pdf(
                f"district-{dist_code}", reports_dir, out_dir, typst_bin,
                template_name="district-report",
            )

    return 0


def _compile_pdf(
    report_name: str,
    reports_dir: Path,
    out_dir: Path,
    typst_bin: str,
    template_name: str | None = None,
) -> None:
    """Compile one report.

    `report_name` names the data file and the output PDF; `template_name` names
    the typst source. They differ for districts, where a single template renders
    all twenty-one from twenty-one different payloads.
    """
    template = reports_dir / f"{template_name or report_name}.typ"
    if not template.exists():
        logger.warning(f"Template not found: {template}")
        return

    json_file = out_dir / f"{report_name}.json"
    pdf_file = out_dir / f"{report_name}.pdf"

    # Make json_file path relative to reports_dir for typst input resolution
    try:
        json_rel = json_file.relative_to(reports_dir)
    except ValueError:
        # If not relative, use absolute path
        json_rel = json_file

    cmd = [
        typst_bin,
        "compile",
        str(template),
        str(pdf_file),
        f"--input=data={json_rel}",
    ]

    logger.info(f"  compiling {report_name}.typ -> {pdf_file.name}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.error(f"typst compilation failed for {report_name}:")
            logger.error(result.stderr)
        else:
            logger.info(f"  wrote {pdf_file}")
    except subprocess.TimeoutExpired:
        logger.error(f"typst compilation timed out for {report_name}")
    except Exception as e:
        logger.error(f"Error compiling {report_name}: {e}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for report generation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["brief", "district", "all"], default="all",
                        help="Report type to generate")
    parser.add_argument("--district", help="Specific district code (e.g., 01)")
    parser.add_argument("--agent", type=Path, help="Path to agent.yml for branding")
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: reports/out)")
    parser.add_argument("--root", type=Path, default=None, help="Repository root")

    args = parser.parse_args(argv)

    return build_reports(
        kind=args.kind,
        district=args.district,
        agent_path=args.agent,
        out_dir=args.out,
        root=args.root,
    )


if __name__ == "__main__":
    raise SystemExit(main())

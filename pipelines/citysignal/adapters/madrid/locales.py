"""Censo de Locales y Actividades — business-premises stock, flow and vacancy.

The Censo de Locales (200085) is a daily-refreshed CURRENT-STATE export: every
row is "this local's situation as of today," with no history and no per-row
change date (its ``fx_carga`` column is the file's own generation date, not a
per-local timestamp — every row carries today's date, even a local that has
not changed in years). Openings and closures are a *rate of change*, which a
current-state file cannot answer by itself. So this adapter builds its own
history:

1. **Base snapshot** (``data/madrid/locales/base-YYYY-MM.csv``): committed once,
   the first time this adapter ever runs, holding one row per local — ``id,
   epigraph, district, barrio, state``. A local can carry more than one
   registered activity (epigraph); the row picked for it prefers a horeca
   epigraph if it has one, since that is the classification the derived
   metrics care about, else the first activity encountered.
2. **Monthly diffs** (``data/madrid/locales/diffs/YYYY-MM.csv``): each later run
   fetches the current census, reconstructs the state as of the last recorded
   period by replaying the base and every diff since, and writes only what
   changed — one row per local that was ``added``, ``removed``,
   ``state_changed`` (its ``desc_situacion_local`` flipped, e.g. Abierto ->
   Cerrado) or ``epigraph_changed``. ``old``/``new`` hold the full
   ``epigraph|district|barrio|state`` record on both sides (empty on the
   missing side for added/removed) rather than just the one field that moved
   — that is what lets a diff be replayed on its own, without needing the
   original fetched snapshot, and it is what "reconstructible by replay" in
   the brief actually requires.
3. **Chain** (``data/madrid/locales/chain.json``): records, for the base and for
   every diff, the sha256 of that file's own bytes and the sha256 of the full
   reconstructed state it produces. Reconstructing any past month is then
   "replay the base, apply diffs up to that month, and check the state hash
   still matches" — exactly what ``_reconstruct`` below does on every run,
   which means a tampered or corrupted diff fails loudly instead of silently
   producing a wrong count.

Only the seven district-level aggregates this dataset is asked to feed are
ever committed to canonical history (``config/sources.yml`` marks
``madrid_locales`` ``redistribute: false`` for exactly this reason) — the
per-local rows live only in the base/diff files under ``data/madrid/locales/``,
never in ``data/history/``.

**Horeca** is defined as CNAE-based Madrid epigraph codes starting ``561``
(restaurantes, cafeterías, autoservicio, comida rápida — CNAE 56.1) or ``563``
(bares, tabernas, bares especiales — CNAE 56.3), found by inspecting the
``id_seccion``/``id_division``/``id_epigrafe`` breakdown in the file by hand.
Division ``562`` (institutional catering — school, hospital and workplace
canteens, event banqueting) sits in the same "HOSTELERÍA" section but is
deliberately excluded: it is not the restaurants-and-bars footprint this
metric is asking about. Division ``55`` (SERVICIOS DE ALOJAMIENTO — hotels)
is a different section entirely and was never in scope.

**First run**: there is no prior snapshot, so ``locales_openings``,
``locales_closures``, ``horeca_openings`` and ``horeca_closures`` are not
emitted at all that run (not zero — genuinely absent; a fabricated first-month
delta would be worse than a gap). ``locales_stock``, ``horeca_stock`` and
``locales_vacancy_rate`` do not need a delta and are emitted from the first
run onward.

**Vacancy rate** = Cerrado / (Abierto + Cerrado) per district. ``Baja`` and
``Baja Reunificación`` (deregistered — demolished, merged into another local)
and ``Uso vivienda`` (converted to residential, no longer a commercial
premise) are excluded from both numerator and denominator: they are not empty
storefronts available to let, they are units that have left the commercial
stock altogether.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
from collections import Counter
from pathlib import Path
from typing import Iterable

import pandas as pd

from ...framework.adapter import AdapterFailure, BaseAdapter, RunContext, SourceManifest
from ...framework.fetch import FetchPlan, RawPayload
from ...framework.record import CanonicalRecord, district, utc_today
from ._shared import MADRID_DISTRICTS, decode_spanish_text

log = logging.getLogger(__name__)

CENSO_URL = (
    "https://datos.madrid.es/dataset/200085-0-censo-locales/resource/"
    "200085-5-censo-locales/download/200085-5-censo-locales.csv"
)

HORECA_PREFIXES = ("561", "563")  # CNAE 56.1 restaurants/cafeterías, 56.3 bars
OPEN_STATE = "Abierto"
CLOSED_STATE = "Cerrado"

_NEEDED_COLUMNS = (
    "id_local",
    "id_distrito_local",
    "id_barrio_local",
    "desc_situacion_local",
    "id_epigrafe",
    "fx_carga",
)

Snapshot = dict[str, tuple[str, str, str, str]]  # id -> (epigraph, district, barrio, state)


def _is_horeca(epigraph: str) -> bool:
    return epigraph.strip().startswith(HORECA_PREFIXES)


def _record_str(rec: tuple[str, str, str, str]) -> str:
    return "|".join(rec)


def _parse_record(s: str) -> tuple[str, str, str, str] | None:
    if not s:
        return None
    parts = s.split("|")
    return (parts[0], parts[1], parts[2], parts[3]) if len(parts) == 4 else None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _serialize_state(state: Snapshot) -> bytes:
    lines = [f"{lid}|{_record_str(rec)}" for lid, rec in sorted(state.items())]
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def _state_hash(state: Snapshot) -> str:
    return _sha256(_serialize_state(state))


def _snapshot_dir(ctx: RunContext) -> Path:
    return ctx.data_dir / "madrid" / "locales"


def _chain_path(ctx: RunContext) -> Path:
    return _snapshot_dir(ctx) / "chain.json"


def _load_chain(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"base": None, "diffs": []}


def _save_chain(path: Path, chain: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(chain, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_snapshot_csv(path: Path, state: Snapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["id", "epigraph", "district", "barrio", "state"])
        for local_id, (epigraph, dist, barrio, state) in sorted(state.items()):
            writer.writerow([local_id, epigraph, dist, barrio, state])
    os.replace(tmp, path)


def _read_snapshot_csv(path: Path) -> Snapshot:
    state: Snapshot = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            state[row["id"]] = (row["epigraph"], row["district"], row["barrio"], row["state"])
    return state


def _write_diff_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["local_id", "change", "old", "new"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def _read_diff_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _compute_diff(prev: Snapshot, curr: Snapshot) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    prev_ids, curr_ids = set(prev), set(curr)
    for local_id in sorted(curr_ids - prev_ids):
        rows.append({"local_id": local_id, "change": "added", "old": "", "new": _record_str(curr[local_id])})
    for local_id in sorted(prev_ids - curr_ids):
        rows.append({"local_id": local_id, "change": "removed", "old": _record_str(prev[local_id]), "new": ""})
    for local_id in sorted(curr_ids & prev_ids):
        old_rec, new_rec = prev[local_id], curr[local_id]
        if old_rec == new_rec:
            continue
        change = "state_changed" if old_rec[3] != new_rec[3] else "epigraph_changed"
        rows.append({"local_id": local_id, "change": change, "old": _record_str(old_rec), "new": _record_str(new_rec)})
    return rows


def _apply_diff(state: Snapshot, diff_rows: list[dict[str, str]]) -> Snapshot:
    new_state = dict(state)
    for row in diff_rows:
        if row["change"] == "removed":
            new_state.pop(row["local_id"], None)
            continue
        rec = _parse_record(row["new"])
        if rec is not None:
            new_state[row["local_id"]] = rec
    return new_state


class MadridLocalesAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id="madrid_locales",
        publisher="Ayuntamiento de Madrid",
        license="Reuse permitted (attribution)",
        attribution="Source: Ayuntamiento de Madrid, Censo de Locales y Actividades",
        docs_url="https://datos.madrid.es/dataset/200085-0-censo-locales",
        cadence="monthly",
        geo_level="district",
        max_age_days=45,
        formats=("csv",),
        kind="official",
        redistribute=False,
        # This is a live current-state export: re-running mid-month can
        # legitimately see a handful of locals flip since the last fetch.
        # Flow metrics stay stable across a same-month re-run (see below) but
        # stock/vacancy are recomputed fresh every time and may restate.
        revisions_allowed=True,
        expected_columns=("id_local", "id_distrito_local", "desc_situacion_local", "id_epigrafe"),
        min_rows=1000,
        notes=(
            "Censo de Locales (epigraph extract): daily-refreshed current state, "
            "no native history. This adapter maintains its own base snapshot + "
            "monthly diff chain under data/madrid/locales/ and commits only the "
            "resulting district aggregates to canonical history."
        ),
    )

    def discover(self, ctx: RunContext) -> list[FetchPlan]:
        return [FetchPlan(url=CENSO_URL, fmt="csv", label="censo-locales-epigrafe")]

    def parse(self, payload: RawPayload, ctx: RunContext) -> pd.DataFrame:
        text = decode_spanish_text(payload.content)
        frame = pd.read_csv(io.StringIO(text), sep=";", dtype=str)
        frame.columns = [c.strip().strip('"') for c in frame.columns]
        missing = [c for c in _NEEDED_COLUMNS if c not in frame.columns]
        if missing:
            raise AdapterFailure(f"censo de locales: missing expected columns {missing}")
        return frame[list(_NEEDED_COLUMNS)]

    def normalize(
        self, frame: pd.DataFrame, plan: FetchPlan, ctx: RunContext
    ) -> Iterable[CanonicalRecord]:
        city = ctx.config.city("madrid")
        period = self._period_from_frame(frame)
        curr = self._build_snapshot(frame)
        if not curr:
            raise AdapterFailure("censo de locales: zero locals parsed into the snapshot")

        base_dir = _snapshot_dir(ctx)
        chain_path = _chain_path(ctx)
        chain = _load_chain(chain_path)

        diff_rows, emit_flow = self._diff_against_chain(base_dir, chain, chain_path, curr, period)

        records = list(self._emit_stock_metrics(curr, period, city))
        if emit_flow:
            records.extend(self._emit_flow_metrics(diff_rows, period, city))
        else:
            log.info(
                "madrid_locales: no prior period to diff against yet for %s — "
                "openings/closures not emitted this run",
                period,
            )
        return records

    # ---- chain management ---------------------------------------------

    def _diff_against_chain(
        self, base_dir: Path, chain: dict, chain_path: Path, curr: Snapshot, period: str
    ) -> tuple[list[dict[str, str]], bool]:
        if chain["base"] is None:
            base_file = base_dir / f"base-{period}.csv"
            _write_snapshot_csv(base_file, curr)
            chain["base"] = {
                "period": period,
                "file": base_file.name,
                "sha256": _sha256(base_file.read_bytes()),
                "state_sha256": _state_hash(curr),
                "rows": len(curr),
            }
            _save_chain(chain_path, chain)
            log.info("madrid_locales: first run — committed %s (%d locals)", base_file.name, len(curr))
            return [], False

        base_period = chain["base"]["period"]
        existing = next((d for d in chain["diffs"] if d["period"] == period), None)
        if existing is not None:
            # Same-month re-run: reuse the already-committed diff so flow
            # metrics stay identical instead of flapping on every re-fetch.
            return _read_diff_csv(base_dir / existing["file"]), True

        if period == base_period:
            # Still inside the base snapshot's own month — no distinct prior
            # state exists yet to compute a delta against.
            return [], False

        latest_known = max([base_period, *(d["period"] for d in chain["diffs"])])
        if period <= latest_known:
            raise AdapterFailure(
                f"madrid_locales: refusing to write a diff for {period}, at or before the "
                f"latest recorded period {latest_known} — the chain only advances forward"
            )

        prev_state = self._reconstruct(base_dir, chain, upto_period_exclusive=period)
        diff_rows = _compute_diff(prev_state, curr)
        diff_file = base_dir / "diffs" / f"{period}.csv"
        _write_diff_csv(diff_file, diff_rows)
        chain["diffs"].append(
            {
                "period": period,
                "file": f"diffs/{period}.csv",
                "diff_sha256": _sha256(diff_file.read_bytes()),
                "prev_state_sha256": _state_hash(prev_state),
                "state_sha256": _state_hash(curr),
                "added": sum(1 for r in diff_rows if r["change"] == "added"),
                "removed": sum(1 for r in diff_rows if r["change"] == "removed"),
                "state_changed": sum(1 for r in diff_rows if r["change"] == "state_changed"),
                "epigraph_changed": sum(1 for r in diff_rows if r["change"] == "epigraph_changed"),
            }
        )
        _save_chain(chain_path, chain)
        return diff_rows, True

    def _reconstruct(self, base_dir: Path, chain: dict, *, upto_period_exclusive: str) -> Snapshot:
        base_info = chain["base"]
        state = _read_snapshot_csv(base_dir / base_info["file"])
        if _state_hash(state) != base_info["state_sha256"]:
            raise AdapterFailure(
                "madrid_locales: base snapshot hash mismatch — "
                "data/madrid/locales/base-*.csv does not match chain.json"
            )
        for entry in sorted(chain["diffs"], key=lambda d: d["period"]):
            if entry["period"] >= upto_period_exclusive:
                break
            diff_path = base_dir / entry["file"]
            diff_bytes = diff_path.read_bytes()
            if _sha256(diff_bytes) != entry["diff_sha256"]:
                raise AdapterFailure(f"madrid_locales: diff hash mismatch for {entry['period']}")
            state = _apply_diff(state, _read_diff_csv(diff_path))
            if _state_hash(state) != entry["state_sha256"]:
                raise AdapterFailure(
                    f"madrid_locales: reconstructed state for {entry['period']} does not "
                    "match the hash recorded in chain.json — history may be corrupted"
                )
        return state

    # ---- parsing --------------------------------------------------------

    @staticmethod
    def _period_from_frame(frame: pd.DataFrame) -> str:
        sample = frame["fx_carga"].dropna()
        if not sample.empty:
            raw = str(sample.iloc[0]).strip()
            try:
                if "/" in raw:
                    day, month, year = raw.split("/")[:3]
                    return f"{int(year[:4]):04d}-{int(month):02d}"
                if "-" in raw:
                    year, month = raw.split("-")[:2]
                    return f"{int(year):04d}-{int(month):02d}"
            except (ValueError, IndexError):
                pass
        return utc_today().isoformat()[:7]

    @staticmethod
    def _build_snapshot(frame: pd.DataFrame) -> Snapshot:
        best: Snapshot = {}
        best_is_horeca: dict[str, bool] = {}
        for row in frame.itertuples(index=False):
            local_id = str(row.id_local or "").strip()
            dist_raw = str(row.id_distrito_local or "").strip()
            if not local_id or not dist_raw or not dist_raw.isdigit():
                continue
            code = int(dist_raw)
            if not 1 <= code <= 21:
                continue
            epigraph = str(row.id_epigrafe or "").strip()
            record = (
                epigraph,
                f"{code:02d}",
                str(row.id_barrio_local or "").strip(),
                str(row.desc_situacion_local or "").strip(),
            )
            horeca = _is_horeca(epigraph)
            if local_id not in best or (horeca and not best_is_horeca.get(local_id, False)):
                best[local_id] = record
                best_is_horeca[local_id] = horeca
        return best

    # ---- metric emission --------------------------------------------------

    def _emit_stock_metrics(self, curr: Snapshot, period: str, city) -> Iterable[CanonicalRecord]:
        stock: Counter[str] = Counter()
        closed: Counter[str] = Counter()
        horeca_stock: Counter[str] = Counter()
        for epigraph, dist, _barrio, state in curr.values():
            if state == OPEN_STATE:
                stock[dist] += 1
                if _is_horeca(epigraph):
                    horeca_stock[dist] += 1
            elif state == CLOSED_STATE:
                closed[dist] += 1

        out: list[CanonicalRecord] = []
        for code, _name in MADRID_DISTRICTS:
            geo_id = district(city.ine_mun, code)
            open_n, closed_n = stock.get(code, 0), closed.get(code, 0)
            denom = open_n + closed_n
            vacancy = (closed_n / denom * 100.0) if denom else 0.0
            out.append(self._record("locales_stock", geo_id, period, float(open_n), "premises"))
            out.append(self._record("horeca_stock", geo_id, period, float(horeca_stock.get(code, 0)), "premises"))
            out.append(self._record("locales_vacancy_rate", geo_id, period, vacancy, "percent"))
        return out

    def _emit_flow_metrics(self, diff_rows: list[dict[str, str]], period: str, city) -> Iterable[CanonicalRecord]:
        openings: Counter[str] = Counter()
        closures: Counter[str] = Counter()
        horeca_openings: Counter[str] = Counter()
        horeca_closures: Counter[str] = Counter()

        for row in diff_rows:
            old_rec = _parse_record(row.get("old", ""))
            new_rec = _parse_record(row.get("new", ""))
            old_open = old_rec is not None and old_rec[3] == OPEN_STATE
            new_open = new_rec is not None and new_rec[3] == OPEN_STATE
            if new_open and not old_open:
                openings[new_rec[1]] += 1
            if old_open and not new_open:
                closures[old_rec[1]] += 1
            old_horeca = old_open and _is_horeca(old_rec[0])
            new_horeca = new_open and _is_horeca(new_rec[0])
            if new_horeca and not old_horeca:
                horeca_openings[new_rec[1]] += 1
            if old_horeca and not new_horeca:
                horeca_closures[old_rec[1]] += 1

        out: list[CanonicalRecord] = []
        for code, _name in MADRID_DISTRICTS:
            geo_id = district(city.ine_mun, code)
            out.append(self._record("locales_openings", geo_id, period, float(openings.get(code, 0)), "premises"))
            out.append(self._record("locales_closures", geo_id, period, float(closures.get(code, 0)), "premises"))
            out.append(self._record("horeca_openings", geo_id, period, float(horeca_openings.get(code, 0)), "premises"))
            out.append(self._record("horeca_closures", geo_id, period, float(horeca_closures.get(code, 0)), "premises"))
        return out

    def _record(self, metric_id: str, geo_id: str, period: str, value: float, unit: str) -> CanonicalRecord:
        return CanonicalRecord(
            metric_id=metric_id,
            geo_id=geo_id,
            period=period,
            value=value,
            unit=unit,
            source_id=self.manifest.source_id,
        )

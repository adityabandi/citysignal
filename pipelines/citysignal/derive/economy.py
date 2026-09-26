"""Country dashboard, transparent activity breadth, and research-ready exports.

The activity reading is a descriptive diffusion measure, not a fitted nowcast.
Alternative data remains separate so its incremental information can be tested.
"""
from __future__ import annotations

import csv
import hashlib
import gzip
import io
import json
from datetime import date, timedelta
from statistics import mean

from ..adapters.eurostat_macro import SERIES
from ..countries import COUNTRIES, MARKETS
from ..adapters.worldbank import INDICATORS
from ..framework.history import read_history, current_view
from ..framework.record import period_end, period_shift, utc_today
from .transforms import yoy
from .assessment import assess_country, family, historical_position

FALLBACKS = {'macro_gdp':'oecd_gdp', 'macro_industry':'oecd_industry',
             'macro_retail':'oecd_retail', 'macro_unemployment':'oecd_unemployment'}

DISPLAY = {
    'oecd_cli': ('Cycle', 'trend = 100', 'Reported', 3, 100),
    'bis_cpi': ('Prices', '% y/y', 'Reported', 3, 100),
    'bis_policy_rate': ('Monetary policy', '%', 'Reported', 3, 100),
    'bis_euro_policy_rate': ('Monetary policy', '%', 'Reported', 3, 100),
    'geopolitical_risk': ('Geopolitics', '% of articles', 'Reported', 3, 75),

    'macro_gdp': ('Growth', '% q/q', 'Reported', 1, 200),
    'oecd_consumption': ('Consumption', '% q/q', 'Reported', 1, 200),
    'oecd_investment': ('Investment', '% q/q', 'Reported', 1, 200),
    'oecd_bond_yield': ('Markets', '% · monthly average', 'Reported', 3, 75),
    'oecd_equities': ('Markets', '% over 3 months', '3-month growth', 3, 75),
    'oecd_fx': ('Markets', '% vs USD over 3 months', '3-month currency appreciation', 3, 75),
    'oecd_euro_fx': ('Markets', '% vs USD over 3 months', '3-month currency appreciation', 3, 75),
    'macro_unemployment': ('Labour', '% of labour force', 'Reported', 3, 100),
    'macro_retail': ('Consumption', '% y/y', 'Annual change', 3, 100),
    'macro_industry': ('Production', '% y/y', 'Annual change', 3, 100),
    'macro_inflation': ('Prices', '% y/y', 'Reported', 3, 75),
    'macro_sentiment': ('Expectations', 'long-run average = 100', 'Reported', 3, 75),
    'macro_employment': ('Labour', '% q/q', 'Reported', 1, 200),
    'macro_house_prices': ('Housing', '% y/y', 'Reported', 1, 230),
    'ecb_mortgage_rate': ('Financing', '% annual rate', 'Reported', 3, 100),
    'wb_gdp_growth': ('Annual macro', '% y/y · annual', 'Reported', 1, 900),
    'wb_inflation': ('Annual macro', '% · annual average', 'Reported', 1, 900),
    'wb_unemployment': ('Annual macro', '% of labour force · annual', 'Reported', 1, 900),
    'wb_population': ('Structure', 'people', 'Reported', 1, 900),
    'wb_gdp_per_capita': ('Structure', '2021 international $', 'Reported', 1, 900),
    'wb_trade': ('Structure', '% of GDP', 'Reported', 1, 900),
    'power_demand': ('Energy exposure', '% y/y', 'Annual change', 3, 150),
    'power_fossil_share': ('Energy exposure', '% of generation', 'Reported', 3, 150),
    'power_carbon_intensity': ('Energy exposure', 'gCO₂ / kWh', 'Reported', 3, 150),
    'power_price': ('Energy costs', '€ / MWh', 'Reported', 3, 150),
    'shipping_imports': ('Trade', '% y/y', 'Annual change', 3, 100),
    'shipping_exports': ('Trade', '% y/y', 'Annual change', 3, 100),
    'shipping_calls': ('Logistics', '% y/y', 'Annual change', 3, 100),
    'hiring_total': ('Hiring', 'Feb 2020 = 100', 'Reported', 28, 21),
    'hiring_new': ('Hiring', 'Feb 2020 = 100', 'Reported', 28, 21),
    'electricity_demand': ('Energy exposure', '% vs 364 days earlier', '28-day demand growth', 28, 14),
    'electricity_large_users': ('Business electricity', '% y/y', 'Annual change', 3, 100),
    'de_truck_mileage': ('Logistics', '2021 = 100', '28-day mean', 28, 21),
    'us_withheld_tax': ('Tax receipts', '% y/y', 'Annual change', 3, 75),
    'us_withheld_tax_daily': ('Tax receipts', 'USD million', 'Reported', 28, 10),
    'uk_supplier_payment_days': ('Corporate payments', 'days', 'Reported', 3, 75),
    'uk_supplier_overdue': ('Corporate payments', '% of invoices · median company', 'Reported', 3, 75),
    'uk_supplier_panel': ('Corporate payments', 'companies', 'Reported', 3, 75),
}
for _kind in ('upi', 'card', 'bank_toll'):
    for _measure in ('volume', 'value'):
        DISPLAY[f'in_{_kind}_{_measure}'] = ('Payments' if _kind != 'bank_toll' else 'Logistics', '% y/y', 'Annual change', 3, 90)
for _vessel in ('container', 'tanker'):
    for _direction in ('exports', 'imports'):
        DISPLAY[f'shipping_{_vessel}_{_direction}'] = ('Trade', '% y/y', 'Annual change', 3, 100)

ACTIVITY = ('macro_gdp', 'macro_unemployment', 'macro_retail', 'macro_industry')
ALTERNATIVE = ('geopolitical_risk', 'shipping_container_exports', 'shipping_container_imports', 'shipping_tanker_exports', 'shipping_tanker_imports', 'power_demand', 'shipping_exports', 'shipping_imports', 'shipping_calls', 'hiring_total', 'hiring_new', 'electricity_demand', 'electricity_large_users', 'power_fossil_share', 'power_carbon_intensity', 'power_price')
ALTERNATIVE += ('de_truck_mileage', 'us_withheld_tax', 'us_withheld_tax_daily', 'uk_supplier_payment_days', 'uk_supplier_overdue', 'uk_supplier_panel')
ALTERNATIVE += tuple(f'in_{kind}_{measure}' for kind in ('upi', 'card', 'bank_toll') for measure in ('volume', 'value'))


def rolling_mean(values, days=28):
    result = {}
    for p in sorted(values):
        window = [period_shift(p, -i) for i in range(days)]
        if all(q in values for q in window):
            result[p] = mean(values[q] for q in window)
    return result


def three_month_growth(values, inverse=False):
    result = {}
    for p, value in values.items():
        prior = values.get(period_shift(p, -3))
        if prior is not None and prior > 0 and value > 0:
            result[p] = 100 * ((prior / value if inverse else value / prior) - 1)
    return result


def electricity_growth(values):
    """Complete 28-day windows versus 364 days earlier align weekdays.

    Missing days invalidate a window; a partial month never becomes a full month.
    Weather and Easter are deliberately NOT claimed to be controlled here.
    """
    result = {}
    for p in sorted(values):
        end = date.fromisoformat(p)
        current = [(end - timedelta(days=d)).isoformat() for d in range(28)]
        prior = [(end - timedelta(days=d + 364)).isoformat() for d in range(28)]
        if all(d in values for d in current + prior):
            denominator = sum(values[d] for d in prior)
            if denominator > 0:
                result[p] = 100 * (sum(values[d] for d in current) / denominator - 1)
    return result


def activity_reading(metrics):
    by_id = {m['id']: m for m in metrics}
    components = []
    for key in ACTIVITY:
        m = by_id.get(key)
        if not m or m['freshness'] != 'current' or m['quality'] == 'suspect':
            continue
        value = -m['change'] if key == 'macro_unemployment' and m['change'] is not None else m['value'] if key != 'macro_unemployment' else None
        if value is None:
            continue
        components.append({'id': key, 'label': m['label'], 'positive': value > 0, 'value': round(value, 3), 'period': m['period']})
    count = len(components)
    positive = sum(c['positive'] for c in components)
    return {'positive': positive, 'available': count, 'total': len(ACTIVITY),
            'value': round(100 * positive / count) if count >= 3 else None,
            'label': 'Insufficient coverage' if count < 3 else 'Broad expansion' if positive / count >= .75 else 'Broad weakness' if positive / count <= .25 else 'Mixed activity',
            'components': components}


class EconomyBuilder:
    def __init__(self, config, store, health=None, today=None):
        self.config, self.store, self.health = config, store, health or {}
        self.today = today or utc_today()

    def card(self, metric, country):
        geo = 'grid-es-mainland' if metric == 'electricity_large_users' and country == 'es' else country
        if metric in {'bis_euro_policy_rate', 'oecd_euro_fx'}:
            if country not in {'es', 'pt', 'fr', 'de', 'it', 'nl', 'ie'}:
                return None
            geo = 'euro-area'
        if metric == 'bis_policy_rate' and country in {'es', 'pt', 'fr', 'de', 'it', 'nl', 'ie'}:
            return None
        if metric == 'oecd_fx' and country in {'es', 'pt', 'fr', 'de', 'it', 'nl', 'ie'}:
            return None
        raw_metric = metric
        s = self.store.get(raw_metric, geo)
        if s is None and metric in FALLBACKS:
            raw_metric = FALLBACKS[metric]
            s = self.store.get(raw_metric, geo)
        if s is None:
            return None
        raw = {p: v for p, v in s.values.items() if period_end(p) < self.today}
        if not raw:
            return None
        group, unit, transform, delta, max_age = DISPLAY[metric]
        values = yoy(raw) if transform == 'Annual change' else electricity_growth(raw) if transform == '28-day demand growth' else rolling_mean(raw) if transform == '28-day mean' else raw
        if transform in ('3-month growth', '3-month currency appreciation'):
            values = three_month_growth(raw, inverse=transform == '3-month currency appreciation')
        values = {p: v for p, v in values.items() if v is not None}
        if not values:
            return None
        period = max(values)
        before = values.get(period_shift(period, -delta))
        meta = self.config.metrics[raw_metric]
        source = self.config.sources.get(s.source_id, {})
        health = self.health.get(s.source_id, {})
        age = (self.today - period_end(period)).days
        def quality_at(p):
            flags = [(s.quality_flags or {}).get(p, 'ok')]
            if transform == 'Annual change':
                flags.append((s.quality_flags or {}).get(period_shift(p, -12), 'ok'))
            elif transform == '28-day demand growth':
                flags.extend((s.quality_flags or {}).get(period_shift(p, -offset), 'ok') for offset in [*range(28), *range(364, 392)])
            elif transform == '28-day mean':
                flags.extend((s.quality_flags or {}).get(period_shift(p, -offset), 'ok') for offset in range(28))
            elif transform in ('3-month growth', '3-month currency appreciation'):
                flags.append((s.quality_flags or {}).get(period_shift(p, -3), 'ok'))
            return 'suspect' if 'suspect' in flags else 'estimated' if 'estimated' in flags else 'ok'
        quality = quality_at(period)
        cadence = meta['cadence']
        limit = len(values)  # All-history chart controls must actually include the full record
        observation = (getattr(s, 'observations', None) or {}).get(period, {})
        card = {'id': metric, 'raw_metric_id':raw_metric, 'label': meta['label'], 'description': meta.get('plain'), 'group': group,
            'value': round(values[period], 4), 'period': period, 'unit': unit,
            'raw_value': raw.get(period), 'raw_unit': meta['unit'], 'cadence': cadence,
            'change': round(values[period] - before, 4) if before is not None else None,
            'change_unit': ('index pts' if meta['unit'] == 'index' else 'pp' if meta['unit'] == 'percent' else unit) if transform in ('Reported', '28-day mean') else 'pp',
            'change_window': '28 days' if cadence == 'daily' else 'prior quarter' if cadence == 'quarterly' else 'prior year' if cadence == 'annual' else '3 months',
            'series': [{'period': p, 'value': round(v, 4)} for p, v in sorted(values.items())[-limit:]],
            'screening': [{'period': p, 'value': round(v, 4), 'change': round(v - values[prior], 4) if (prior := period_shift(p, -delta)) in values else None, 'quality': quality_at(p), 'observation_end': period_end(p).isoformat()} for p, v in sorted(values.items())[-(90 if cadence == 'daily' else 36 if cadence == 'monthly' else 16):]],
            'source_id': s.source_id, 'source': source.get('publisher', s.source_id),
            'source_url': f'https://ec.europa.eu/eurostat/databrowser/view/{SERIES[metric][0]}/default/table?lang=en' if metric in SERIES and raw_metric == metric else source.get('docs_url'),
            'license': source.get('license'), 'kind': source.get('kind', 'official'),
            'scope': ('Euro area · shared currency' if metric == 'oecd_euro_fx' else 'Euro area · shared monetary policy') if geo == 'euro-area' else 'Spain · mainland large power users' if geo == 'grid-es-mainland' else COUNTRIES[country] + (' · covered ports, incl. transshipment' if metric.startswith('shipping_') else ' · national'),
            'signal_type': 'alternative' if metric in ALTERNATIVE else 'official',
            'geo_id': geo, 'transform': transform, 'quality': quality,
            'freshness': 'stale' if age > max_age else 'current', 'age_days': age,
            'max_age_days': max_age, 'last_checked': health.get('last_checked'),
            'source_status': health.get('status', 'unknown'),
            'published_at':observation.get('published_at'), 'fetched_at':observation.get('fetched_at') or s.fetched_at,
            'historical_only':age > max_age}
        card['family'] = family(card)
        card['historical_position'] = historical_position(card)
        return card

    def build(self):
        countries = []
        for code, name in COUNTRIES.items():
            metrics = [c for key in DISPLAY if key not in ALTERNATIVE and (c := self.card(key, code))]
            alternative = [c for key in ALTERNATIVE if (c := self.card(key, code))]
            alternative.extend(self.recipes(code, alternative))
            countries.append({'code': code, **MARKETS[code], 'metrics': metrics,
                'activity': activity_reading(metrics), 'alternative': alternative,
                'assessment':assess_country(metrics + alternative, self.today.isoformat()),
                'cities': [{'slug': c.slug, 'name': c.name, 'region': c.ccaa_name, 'districts': c.deep_dive} for c in self.config.cities] if code == 'es' else []})
        return {'version': 'economy-v3', 'as_of': self.today.isoformat(), 'countries': countries,
            'method': 'Share of four current activity signals that are positive: real GDP q/q, industrial production y/y, real retail sales y/y, and falling unemployment over three months. Equal weights; at least three required. Latest observations have different dates. Inflation, housing, survey sentiment and alternative data are context, not inputs. This is a descriptive reading, not a recession probability or a forecast.',
            'sources': sorted({c['source_id'] for country in countries for c in country['metrics'] + country['alternative']})}

    def recipes(self, code, alternative):
        """Transparent same-date spreads; candidate signals, not fitted forecasts."""
        cards = {m['id']: m for m in alternative}
        recipes = []
        definitions = [
            ('hiring_flow_gap', 'Hiring flow gap', 'hiring_new', 'hiring_total',
             '28-day growth in new postings minus 28-day growth in total postings. Positive means fresh hiring intent is growing faster than the existing pool. Does not measure vacancies filled.', 'Hiring'),
            ('trade_flow_gap', 'Export–import growth gap', 'shipping_exports', 'shipping_imports',
             'Export tonnage annual growth minus import tonnage annual growth, on matching months. A physical trade divergence; not a currency-value trade balance or a GDP contribution.', 'Trade'),
        ]
        for key, label, left, right, description, group in definitions:
            a, b = cards.get(left), cards.get(right)
            if not a or not b:
                continue
            av = {p['period']: p['value'] for p in a['series']}
            bv = {p['period']: p['value'] for p in b['series']}
            points = []
            for period in sorted(av.keys() & bv.keys()):
                if key == 'hiring_flow_gap':
                    before = period_shift(period, -28)
                    if av.get(before, 0) <= 0 or bv.get(before, 0) <= 0:
                        continue
                    value = 100 * (av[period] / av[before] - bv[period] / bv[before])
                else:
                    value = av[period] - bv[period]
                points.append({'period': period, 'value': round(value, 4)})
            if not points:
                continue
            def recipe_quality(p):
                flags = []
                for m in (a, b):
                    raw_series = self.store.get(m.get('raw_metric_id', m['id']), m.get('geo_id', code)) if self.store else None
                    if raw_series is None:
                        flags.append(m['quality'])
                        continue
                    periods = [p, period_shift(p, -28 if key == 'hiring_flow_gap' else -12)]
                    flags.extend((raw_series.quality_flags or {}).get(q, 'ok') for q in periods)
                return 'suspect' if 'suspect' in flags else 'estimated' if 'estimated' in flags else 'ok'
            period = points[-1]['period']
            age = (self.today - period_end(period)).days
            recipes.append({**a, 'id': key, 'label': label, 'description': description,
                'group': group, 'value': points[-1]['value'], 'period': period,
                'screening': [{'period': p['period'], 'value': p['value'], 'change': None, 'quality': recipe_quality(p['period']), 'observation_end': period_end(p['period']).isoformat()} for p in points[-(90 if a['cadence'] == 'daily' else 36):]],
                'unit': 'pp', 'series': points, 'change': None, 'change_unit':'pp', 'change_window':'', 'raw_value': None, 'raw_unit': None,
                'signal_type': 'derived', 'transform': 'Difference of growth rates',
                'age_days': age, 'freshness': 'stale' if age > min(a['max_age_days'], b['max_age_days']) else 'current',
                'quality': recipe_quality(period), 'raw_metric_id':None,
                'historical_only':age > min(a['max_age_days'], b['max_age_days']),
                'published_at':None, 'historical_position':None, 'inputs': [left, right]})
        return recipes

    def export(self, out_dir):
        """Ship both current values and captured revisions. No invented releases.

        fetched_at dates the first capture of THAT revision. Historical backfills
        do not imply historical availability; published_at remains blank unless
        supplied. This enables honest as-of queries from collection start onward.
        """
        metrics = set(DISPLAY) | set(FALLBACKS.values())
        rows = []
        for path in sorted((self.config.data_dir / 'history').glob('*/*.csv')):
            if path.stem in metrics:
                rows.extend(read_history(path))
        columns = ['metric_id', 'geo_id', 'geo_level', 'period', 'value', 'unit', 'source_id', 'observation_end', 'published_at', 'fetched_at', 'quality_flag', 'revision']
        rows.sort(key=lambda r: (r['metric_id'], r['geo_id'], r['period'], int(r['revision'] or 0)))
        def encode(records):
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator='\n', extrasaction='ignore')
            writer.writeheader()
            writer.writerows(records)
            return buffer.getvalue()
        # current_view keys by (geo, period) because a history file contains one
        # metric. Keep that boundary when exporting a multi-metric panel.
        current = [row for metric in sorted(metrics)
                   for row in current_view([r for r in rows if r['metric_id'] == metric])]
        payloads = {'economy-panel.csv': encode(current), 'economy-vintages.csv': encode(rows)}
        files = []
        for name, content in payloads.items():
            storage_name = name + '.gz'
            (out_dir / storage_name).write_bytes(gzip.compress(content.encode(), mtime=0))
            (out_dir / name).unlink(missing_ok=True)
            files.append({'file': name, 'storage_file': storage_name, 'storage_encoding': 'gzip', 'sha256': hashlib.sha256(content.encode()).hexdigest(), 'bytes': len(content.encode())})
        manifest = {'schema_version': 1, 'as_of': self.today.isoformat(), 'files': files,
            'current_observations': len(current), 'captured_rows': len(rows),
            'countries': COUNTRIES, 'metrics': {k: self.config.metrics[k] for k in sorted(metrics)},
            'sources': {sid: self.config.sources[sid] for sid in sorted({r['source_id'] for r in rows})},
            'availability_policy': 'Use fetched_at as the first capture of each revision, separately from source publication. Missing published_at means unknown. Backfilled observations were not available to this system before capture. New captures carry UTC timestamps; legacy date-only captures have daily precision.',
            'derived_metrics': {'hiring_flow_gap': '28-day new-posting growth minus total-posting growth (pp). Inputs: hiring_new, hiring_total.', 'trade_flow_gap': 'Export tonnage y/y growth minus import tonnage y/y growth (pp), matching months. Inputs: shipping_exports, shipping_imports. These computed spreads are available in the dashboard; CSV files preserve their raw inputs.'},
            'commercial_status': 'Source-specific attribution and redistribution terms are recorded in the source registry.'}
        (out_dir / 'economy-manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
        return manifest

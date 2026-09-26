"""Publisher monthly AIS aggregates, never official customs trade values.

Use Monthly_TradeNow's five AIS vessel categories in physical tonnes and calls.
Do not infer units for its separate modelled value/volume indices. The publisher
aggregates daily estimates; revisions, AIS coverage and transshipment remain
limitations. Only closed calendar months enter the panel.
"""
import math

import pandas as pd

from ..countries import MARKETS, ISO3_TO_ISO2
from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord, period_end, utc_today

BASE = 'https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Monthly_TradeNow/FeatureServer/0/query'
VESSELS = ('container', 'general_cargo', 'dry_bulk', 'roro', 'tanker')
MEASURES = {'imports': ('ais_import', 'tonnes'), 'exports': ('ais_export', 'tonnes'), 'calls': ('ais_portcalls', 'calls')}


class PortWatchAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id='portwatch', publisher='IMF PortWatch / UN Global Platform',
        license='IMF terms of use; confirm applicable commercial redistribution rights',
        attribution='Sources: UN Global Platform; IMF PortWatch (portwatch.imf.org). Country aggregates calculated by CitySignal.',
        docs_url='https://portwatch.imf.org/pages/data-and-methodology',
        cadence='monthly', geo_level='nation', max_age_days=100,
        formats=('json',), kind='research', revisions_allowed=True,
        notes='Monthly AIS-estimated physical imports, exports and port calls. Covered ports only; includes transshipment. Not customs trade, dollar values, seasonally adjusted data or a GDP forecast.',
    )

    def discover(self, ctx):
        fields = ['ISO3', 'date'] + [f'{prefix}_{v}' for prefix, _ in MEASURES.values() for v in VESSELS]
        return [FetchPlan(url=BASE, fmt='json', optional=True, label=f'ports-{code}',
            params={'f': 'json', 'where': f"ISO3='{meta['iso3']}'", 'outFields': ','.join(fields),
                'orderByFields': 'date', 'resultRecordCount': 1000, 'returnGeometry': 'false'},
            meta={'geo': code}) for code, meta in MARKETS.items()]

    def parse(self, payload, ctx):
        data = payload.json()
        if 'error' in data or 'features' not in data or data.get('exceededTransferLimit'):
            raise AdapterFailure('PortWatch error or truncated response; no partial panel accepted')
        return pd.DataFrame([f['attributes'] for f in data['features']])

    def normalize(self, frame, plan, ctx):
        seen = set()
        for row in frame.to_dict('records'):
            geo = ISO3_TO_ISO2.get(row.get('ISO3'))
            if geo != plan.meta['geo']:
                raise AdapterFailure('PortWatch country mismatch')
            period = str(row['date'])[:7]
            if period_end(period) >= utc_today():
                continue
            key = geo, period
            if key in seen:
                raise AdapterFailure('Duplicate PortWatch month')
            seen.add(key)
            for suffix, (prefix, unit) in MEASURES.items():
                fields = [f'{prefix}_{v}' for v in VESSELS]
                if not set(fields).issubset(row):
                    raise AdapterFailure('PortWatch vessel schema changed')
                values = [row[f] for f in fields]
                if any(v is None or pd.isna(v) for v in values):
                    continue  # Missing vessel categories must not become zeros.
                if any(not math.isfinite(float(v)) or float(v) < 0 for v in values):
                    raise AdapterFailure('Invalid shipping estimate')
                yield CanonicalRecord(metric_id=f'shipping_{suffix}', geo_id=geo, period=period,
                    value=sum(float(v) for v in values), unit=unit, source_id='portwatch', quality_flag='estimated')

            for vessel in ('container', 'tanker'):
                for suffix, prefix in (('imports', 'ais_import'), ('exports', 'ais_export')):
                    value = row[f'{prefix}_{vessel}']
                    if value is None or pd.isna(value):
                        continue
                    if not math.isfinite(float(value)) or float(value) < 0:
                        raise AdapterFailure('Invalid vessel category estimate')
                    yield CanonicalRecord(metric_id=f'shipping_{vessel}_{suffix}', geo_id=geo,
                        period=period, value=float(value), unit='tonnes', source_id='portwatch', quality_flag='estimated')

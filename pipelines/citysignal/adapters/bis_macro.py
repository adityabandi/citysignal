"""BIS monthly national CPI and end-of-period policy rates."""
import csv
import io
import math
import re
import zipfile
import pandas as pd
from ..countries import COUNTRIES
from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord

class BISMacroAdapter(BaseAdapter):
    manifest = SourceManifest(source_id='bis_macro', publisher='Bank for International Settlements',
        license='BIS terms of permitted use of statistics', attribution='Source: BIS Data Portal',
        docs_url='https://data.bis.org/bulkdownload', cadence='monthly', geo_level='nation',
        max_age_days=100, formats=('zip',), revisions_allowed=True,
        notes='National CPI year-on-year changes and end-of-month policy rates. Euro-area monetary policy retains euro_area geography. Policy instrument definitions follow BIS country metadata.')
    def discover(self, ctx):
        return [FetchPlan(url=f'https://data.bis.org/static/bulk/{dataset}_csv_col.zip', fmt='zip', label=metric, optional=True, meta={'metric':metric}) for metric,dataset in [('bis_cpi','WS_LONG_CPI'),('bis_policy_rate','WS_CBPOL')]]
    def parse(self, payload, ctx):
        with zipfile.ZipFile(io.BytesIO(payload.content)) as archive:
            names=[n for n in archive.namelist() if n.endswith('.csv')]
            if len(names)!=1:raise AdapterFailure('Unexpected BIS archive')
            rows=list(csv.DictReader(io.StringIO(archive.read(names[0]).decode('utf-8-sig'))))
        if not rows or not {'FREQ','REF_AREA'}.issubset(rows[0]):raise AdapterFailure('BIS schema changed')
        # Select only monthly columns before constructing a DataFrame; daily rate
        # columns otherwise create a very wide, mostly empty table.
        keys=[k for k in rows[0] if re.fullmatch(r'20\d\d-\d\d', k) and k>='2015-01']
        keys+=['FREQ','REF_AREA','UNIT_MEASURE']
        return pd.DataFrame([{k:r.get(k) for k in keys} for r in rows if r['FREQ']=='M'])
    def normalize(self, frame, plan, ctx):
        metric=plan.meta['metric'];seen=set()
        for row in frame.to_dict('records'):
            geo=row['REF_AREA'].lower()
            if metric=='bis_cpi' and row['UNIT_MEASURE']!='771':continue
            if geo=='xm' and metric=='bis_policy_rate':geo='euro-area';key_metric='bis_euro_policy_rate'
            elif geo in COUNTRIES:key_metric=metric
            else:continue
            for period,raw in row.items():
                if not re.fullmatch(r'20\d\d-\d\d',period) or raw in ('',None) or pd.isna(raw):continue
                value=float(raw);key=(key_metric,geo,period)
                if math.isnan(value):continue
                if not math.isfinite(value) or key in seen:raise AdapterFailure('Invalid or duplicate BIS observation')
                seen.add(key)
                yield CanonicalRecord(metric_id=key_metric,geo_id=geo,period=period,value=value,unit='percent',source_id='bis_macro')

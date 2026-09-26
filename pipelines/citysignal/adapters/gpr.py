"""Country geopolitical-news share from Caldara and Iacoviello."""
import io
import math
import pandas as pd
from ..countries import ISO3_TO_ISO2
from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord, period_end, utc_today

class GPRAdapter(BaseAdapter):
    manifest=SourceManifest(source_id='gpr',publisher='Caldara & Iacoviello',license='Creative Commons BY; author attribution',
        attribution='Caldara and Iacoviello (2022), Measuring Geopolitical Risk; country extension by Caldara et al. (2023).',
        docs_url='https://www.matteoiacoviello.com/gpr_country.htm',cadence='monthly',geo_level='nation',max_age_days=75,
        formats=('xls',),kind='research',revisions_allowed=True,
        notes='Country GPRC series: share of newspaper articles covering geopolitical risk and mentioning the country or major cities. US newspaper perspective; recent-index methodology, not historical GPRHC.')
    def discover(self,ctx):
        return [FetchPlan(url='https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls',fmt='xls',label='country-gpr')]
    def parse(self,payload,ctx):
        frame=pd.read_excel(io.BytesIO(payload.content))
        if 'month' not in frame or not any(c.startswith('GPRC_') for c in frame.columns):raise AdapterFailure('GPR schema changed')
        return frame
    def normalize(self,frame,plan,ctx):
        seen=set()
        for row in frame.to_dict('records'):
            if pd.isna(row['month']):continue
            period=pd.Timestamp(row['month']).strftime('%Y-%m')
            if period<'2015-01' or period_end(period)>=utc_today():continue
            for iso3,geo in ISO3_TO_ISO2.items():
                raw=row.get('GPRC_'+iso3)
                if raw is None or pd.isna(raw):continue
                value=float(raw);key=geo,period
                if not math.isfinite(value) or not 0<=value<=100 or key in seen:raise AdapterFailure('Invalid GPR observation')
                seen.add(key)
                yield CanonicalRecord(metric_id='geopolitical_risk',geo_id=geo,period=period,value=value,unit='percent',source_id='gpr',quality_flag='estimated')

"""Amplitude-adjusted OECD composite leading indicators."""
import io
import math
import pandas as pd
from ..countries import ISO3_TO_ISO2
from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord

class OECDCLIAdapter(BaseAdapter):
    manifest=SourceManifest(source_id='oecd_cli',publisher='OECD',license='OECD terms of use; attribution',
        attribution='Source: OECD, Composite Leading Indicators',docs_url='https://www.oecd.org/en/data/datasets/oecd-composite-leading-indicators-clis.html',
        cadence='monthly',geo_level='nation',max_age_days=100,formats=('csv',),revisions_allowed=True,
        notes='Amplitude-adjusted composite leading index, long-run trend=100. OECD harmonised methodology; turning points relative to trend.')
    def discover(self,ctx):
        return [FetchPlan(url='https://sdmx.oecd.org/public/rest/v1/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/.?startPeriod=2015-01&dimensionAtObservation=AllDimensions',fmt='csv',label='cli',headers={'Accept': 'text/csv'})]
    def parse(self,payload,ctx):
        frame=pd.read_csv(io.StringIO(payload.text()))
        required={'REF_AREA','FREQ','MEASURE','ADJUSTMENT','TRANSFORMATION','METHODOLOGY','UNIT_MEASURE','UNIT_MULT','TIME_PERIOD','OBS_VALUE','ACTIVITY','TIME_HORIZ'}
        if not required.issubset(frame.columns):raise AdapterFailure('OECD CLI schema changed')
        masks={'FREQ':'M','MEASURE':'LI','ADJUSTMENT':'AA','TRANSFORMATION':'IX','METHODOLOGY':'H','UNIT_MEASURE':'IX','ACTIVITY':'_Z','TIME_HORIZ':'_Z'}
        for field,value in masks.items():frame=frame[frame[field].eq(value)]
        return frame[frame['REF_AREA'].isin(ISO3_TO_ISO2)]
    def normalize(self,frame,plan,ctx):
        seen=set()
        for row in frame.to_dict('records'):
            if pd.isna(row['OBS_VALUE']):continue
            value=float(row['OBS_VALUE']);geo=ISO3_TO_ISO2[row['REF_AREA']];period=str(row['TIME_PERIOD']);key=geo,period
            if int(row['UNIT_MULT'])!=0 or not math.isfinite(value) or key in seen:raise AdapterFailure('Invalid OECD CLI observation')
            seen.add(key)
            yield CanonicalRecord(metric_id='oecd_cli',geo_id=geo,period=period,value=value,unit='index',source_id='oecd_cli',quality_flag='estimated' if row.get('OBS_STATUS') in ('P','E') else 'ok')
        if not seen:raise AdapterFailure('No OECD CLI observations')

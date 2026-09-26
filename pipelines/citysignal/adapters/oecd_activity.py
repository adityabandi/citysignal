"""OECD national accounts, real activity and harmonised unemployment.

Select every SDMX dimension explicitly. Quarterly growth is not annualised;
nominal sales, unadjusted indices and annual GDP rates cannot enter this panel.
"""
import io
import math

import pandas as pd

from ..countries import ISO3_TO_ISO2
from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord

BASE = 'https://sdmx.oecd.org/public/rest/v1/data/'
NAD = 'OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_OECD,1.1'
STES = 'OECD.SDD.STES,DSD_STES@DF_INDSERV,4.3'
LFS = 'OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0'
FIN = 'OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0'
GDP_FILTERS = dict(FREQ='Q', ADJUSTMENT='Y', COUNTERPART_SECTOR='S1',
    INSTR_ASSET='_Z', UNIT_MEASURE='PC', PRICE_BASE='L', TRANSFORMATION='G1',
    TABLE_IDENTIFIER='T0102', CURRENCY='_Z')
SPECS = {
    'oecd_gdp': (NAD, 'Q', 'percent', {**GDP_FILTERS, 'SECTOR':'S1', 'TRANSACTION':'B1GQ', 'ACTIVITY':'_Z', 'EXPENDITURE':'_Z'}),
    'oecd_consumption': (NAD, 'Q', 'percent', {**GDP_FILTERS, 'SECTOR':'S1M', 'TRANSACTION':'P3', 'ACTIVITY':'_Z', 'EXPENDITURE':'_T'}),
    'oecd_investment': (NAD, 'Q', 'percent', {**GDP_FILTERS, 'SECTOR':'S1', 'TRANSACTION':'P51G', 'ACTIVITY':'_T', 'EXPENDITURE':'_Z'}),
    'oecd_industry': (STES, 'M', 'index', dict(FREQ='M', MEASURE='PRVM', UNIT_MEASURE='IX', ACTIVITY='BTE', ADJUSTMENT='Y', TRANSFORMATION='_Z', TIME_HORIZ='_Z', METHODOLOGY='N')),
    'oecd_retail': (STES, 'M', 'index', dict(FREQ='M', MEASURE='TOVM', UNIT_MEASURE='IX', ACTIVITY='G47', ADJUSTMENT='Y', TRANSFORMATION='_Z', TIME_HORIZ='_Z', METHODOLOGY='N')),
    'oecd_unemployment': (LFS, 'M', 'percent', dict(REF_AREA=None, MEASURE='UNE_LF_M', UNIT_MEASURE='PT_LF_SUB', TRANSFORMATION='_Z', ADJUSTMENT='Y', SEX='_T', AGE='Y_GE15', ACTIVITY='_Z', FREQ='M')),
}
for _metric, _measure, _unit_code, _unit in [('oecd_bond_yield', 'IRLT', 'PA', 'percent'),
        ('oecd_fx', 'CC', 'XDC_USD', 'local currency per USD'), ('oecd_equities', 'SHARE', 'IX', 'index')]:
    SPECS[_metric] = (FIN, 'M', _unit, dict(FREQ='M', MEASURE=_measure, UNIT_MEASURE=_unit_code,
        ACTIVITY='_Z', ADJUSTMENT='_Z', TRANSFORMATION='_Z', TIME_HORIZ='_Z', METHODOLOGY='N'))


class OECDActivityAdapter(BaseAdapter):
    manifest = SourceManifest(source_id='oecd_activity', publisher='OECD',
        license='OECD terms of use; attribution', attribution='Source: OECD, national accounts and short-term statistics',
        docs_url='https://data-explorer.oecd.org/', cadence='monthly', geo_level='nation',
        max_age_days=120, formats=('csv',), revisions_allowed=True, read_timeout=90,
        notes='Seasonally adjusted real GDP, household consumption, investment, industry, retail volume and harmonised unemployment. GDP growth is quarter-on-quarter, not annualised.')

    def discover(self, ctx):
        countries = '+'.join(ISO3_TO_ISO2)
        queries = [
            ('accounts', NAD, f'Q.Y.{countries}.S1+S1M.S1.B1GQ+P3+P51G._Z.._Z+_T.PC.L.G1.T0102', '2015-Q1'),
            ('industry', STES, f'{countries}.M.PRVM.IX.BTE.Y._Z._Z.N', '2015-01'),
            ('retail', STES, f'{countries}.M.TOVM.IX.G47.Y._Z._Z.N', '2015-01'),
            ('unemployment', LFS, f'{countries}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15._Z.M', '2015-01'),
            ('markets', FIN, f'{countries}+EA20.M.IRLT+CC+SHARE.._Z._Z._Z._Z.N', '2015-01'),
        ]
        return [FetchPlan(f'{BASE}{flow}/{key}?startPeriod={start}&dimensionAtObservation=AllDimensions',
            'csv', label=label, headers={'Accept':'text/csv'}, meta={'flow':flow}, optional=True)
            for label, flow, key, start in queries]

    def parse(self, payload, ctx):
        frame = pd.read_csv(io.StringIO(payload.text()), dtype={'REF_AREA':str})
        if not {'REF_AREA', 'TIME_PERIOD', 'OBS_VALUE', 'UNIT_MULT', 'OBS_STATUS'}.issubset(frame.columns):
            raise AdapterFailure('OECD activity schema changed')
        return frame[frame.REF_AREA.isin([*ISO3_TO_ISO2, 'EA20'])]

    def normalize(self, frame, plan, ctx):
        seen = set()
        for metric, (flow, freq, unit, filters) in SPECS.items():
            if flow != plan.meta['flow']:
                continue
            rows = frame
            for field, value in filters.items():
                if value is None:
                    continue
                if field not in rows:
                    raise AdapterFailure(f'OECD dimension missing: {field}')
                rows = rows[rows[field].eq(value)]
            for row in rows.to_dict('records'):
                euro_fx = row['REF_AREA'] == 'EA20'
                if euro_fx and metric != 'oecd_fx':
                    continue
                if pd.isna(row['OBS_VALUE']):
                    continue
                value = float(row['OBS_VALUE'])
                key = metric, row['REF_AREA'], str(row['TIME_PERIOD'])
                if key in seen or not math.isfinite(value) or int(row['UNIT_MULT']) != 0:
                    raise AdapterFailure(f'Ambiguous OECD observation: {key}')
                seen.add(key)
                yield CanonicalRecord('oecd_euro_fx' if euro_fx else metric, 'euro-area' if euro_fx else ISO3_TO_ISO2[row['REF_AREA']], str(row['TIME_PERIOD']),
                    value, unit, self.manifest.source_id,
                    quality_flag='estimated' if row['OBS_STATUS'] in ('P', 'E') else 'ok')
        if not seen:
            raise AdapterFailure(f'No matching OECD activity observations: {plan.label}')

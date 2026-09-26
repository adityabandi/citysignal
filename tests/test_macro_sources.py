"""Source units, geography and dated cross-sections are part of the data contract."""
import gzip
import io
import json
import zipfile
from datetime import date
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest

from citysignal.adapters.bis_macro import BISMacroAdapter
from citysignal.adapters.gpr import GPRAdapter
from citysignal.adapters.oecd_cli import OECDCLIAdapter
from citysignal.derive.economy import EconomyBuilder
from citysignal.derive.store import Series, SeriesKey
from citysignal.framework.adapter import AdapterFailure
from citysignal.framework.fetch import Fetcher, FetchPlan, RawPayload


def test_csv_accept_and_cache_identity_keep_conditional_headers():
    csv = FetchPlan('https://example.test/data', 'csv', headers={'Accept':'text/csv'})
    xml = FetchPlan('https://example.test/data', 'csv')
    assert csv.cache_key != xml.cache_key
    received = []
    def handler(request):
        received.append(request)
        return httpx.Response(200, content=b'country,value\nUS,1\n')
    with Fetcher(min_interval=0) as fetcher:
        fetcher._client.close()
        fetcher._client = httpx.Client(transport=httpx.MockTransport(handler))
        fetcher.get(csv, headers={'If-None-Match':'previous'})
    assert received[0].headers['Accept'] == 'text/csv'
    assert received[0].headers['If-None-Match'] == 'previous'


def test_bis_cpi_filters_index_units_and_skips_publisher_nan():
    content = b'FREQ,REF_AREA,UNIT_MEASURE,2025-09,2025-10\nM,US,771,3.0,NaN\nM,US,628,130,131\nA,US,771,9,9\n'
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('cpi.csv', content)
    adapter = BISMacroAdapter(); plan = adapter.discover(None)[0]
    frame = adapter.parse(RawPayload(plan, archive.getvalue(), 'x'), None)
    rows = list(adapter.normalize(frame, plan, None))
    assert [(r.geo_id,r.period,r.value,r.unit) for r in rows] == [('us','2025-09',3,'percent')]
    with pytest.raises(AdapterFailure):
        list(adapter.normalize(pd.concat([frame,frame]), plan, None))


def test_bis_euro_area_policy_retains_shared_scope(config):
    adapter = BISMacroAdapter(); plan = adapter.discover(None)[1]
    rows = list(adapter.normalize(pd.DataFrame([{'REF_AREA':'XM','UNIT_MEASURE':'','2025-09':'2.25'}]),plan,None))
    assert rows[0].geo_id == 'euro-area' and rows[0].geo_level == 'euro_area'
    assert rows[0].metric_id == 'bis_euro_policy_rate'
    series = Series(SeriesKey('bis_euro_policy_rate','euro-area','euro_area'), {'2025-09':2.25},'bis_macro','percent')
    store = SimpleNamespace(get=lambda metric,geo:series if metric=='bis_euro_policy_rate' and geo=='euro-area' else None)
    builder=EconomyBuilder(config,store,today=date(2025,11,1))
    assert builder.card('bis_euro_policy_rate','es')['scope']=='Euro area · shared monetary policy'
    assert builder.card('bis_euro_policy_rate','us') is None


def test_oecd_cli_excludes_other_adjustment_and_transformation_variants():
    base=dict(REF_AREA='USA',FREQ='M',MEASURE='LI',ADJUSTMENT='AA',TRANSFORMATION='IX',METHODOLOGY='H',UNIT_MEASURE='IX',UNIT_MULT=0,TIME_PERIOD='2025-09',OBS_VALUE=101,ACTIVITY='_Z',TIME_HORIZ='_Z',OBS_STATUS='P')
    variants=[base,dict(base,ADJUSTMENT='NOR',OBS_VALUE=50),dict(base,TRANSFORMATION='GY',OBS_VALUE=3),dict(base,FREQ='Q',OBS_VALUE=80)]
    adapter=OECDCLIAdapter();plan=adapter.discover(None)[0]
    assert plan.headers['Accept']=='text/csv'
    frame=adapter.parse(RawPayload(plan,pd.DataFrame(variants).to_csv(index=False).encode(),'x'),None)
    rows=list(adapter.normalize(frame,plan,None))
    assert len(rows)==1 and rows[0].value==101 and rows[0].quality_flag=='estimated'


def test_gpr_country_share_does_not_take_global_index_or_historical_variant():
    frame=pd.DataFrame([{'month':pd.Timestamp('2025-09-01'),'GPR':120,'GPRC_USA':.15,'GPRHC_USA':17,'GPRC_CHN':None}])
    rows=list(GPRAdapter().normalize(frame,None,None))
    assert len(rows)==1 and rows[0].geo_id=='us' and rows[0].value==.15 and rows[0].unit=='percent'
    with pytest.raises(AdapterFailure):
        list(GPRAdapter().normalize(pd.concat([frame,frame]),None,None))


def test_screener_dates_changes_and_history_match_country_files(repo_root):
    d=repo_root/'data/derived'
    index=json.loads(gzip.decompress((d/'screener.json.gz').read_bytes()))
    for code in ('us','es','cn','in'):
        country=json.loads(gzip.decompress((d/'countries'/f'{code}.json.gz').read_bytes()))
        for metric in country['metrics']+country['alternative']:
            points=index[metric['id']]['countries'][code]
            assert points and len({p['period'] for p in points})==len(points)
            assert points[-1]['period']==metric['period']
            assert points[-1]['value']==metric['value']
            assert points[-1]['change']==metric['change']
            history={p['period']:p['value'] for p in metric['series']}
            assert all(history[p['period']]==p['value'] for p in points)
    us=index['bis_cpi']['countries']['us']
    assert '2025-10' not in {p['period'] for p in us}

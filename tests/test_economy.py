import json
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from citysignal.adapters.eurostat_macro import EurostatMacroAdapter, SERIES
from citysignal.adapters.indeed_daily import IndeedDailyAdapter
from citysignal.adapters.worldbank import WorldBankAdapter
from citysignal.derive.economy import EconomyBuilder, activity_reading, electricity_growth
from citysignal.derive.research import monthly, walk_forward
from citysignal.derive.store import Series, SeriesKey
from citysignal.framework.adapter import AdapterFailure
from citysignal.framework.fetch import RawPayload, FetchPlan
from citysignal.framework.jsonstat import observations
from citysignal.framework.record import CanonicalRecord, period_shift


def cube():
    # Time is deliberately NOT the last dimension. One missing cell and one
    # zero distinguish true row-major decoding from naive time-index lookup.
    return {'id':['time','geo','unit'], 'size':[2,2,1],
        'dimension':{'time':{'category':{'index':{'2026-01':0,'2026-02':1}}},
        'geo':{'category':{'index':{'ES':0,'PT':1}}}, 'unit':{'category':{'index':['PC']}}},
        'value':{'0':0,'1':5,'3':7}, 'status':{'3':'p'}}


def test_jsonstat_sparse_row_major_and_zero():
    rows=list(observations(cube()))
    assert [(r['geo'],r['time'],r['value']) for r in rows]==[('ES','2026-01',0),('PT','2026-01',5),('PT','2026-02',7)]
    assert rows[-1]['status']=='p'


def test_jsonstat_dense_arrays_and_missing_values():
    data=cube();data['value']=[0,5,None,7];data['status']=['','','','e']
    assert [r['value'] for r in observations(data)]==[0,5,7]


@pytest.mark.parametrize('change',[lambda d:d['size'].__setitem__(2,2),lambda d:d['value'].__setitem__('99',1),lambda d:d['value'].__setitem__('1',float('nan'))])
def test_jsonstat_refuses_ambiguous_and_invalid_cells(change):
    data=cube();change(data)
    with pytest.raises(AdapterFailure):list(observations(data))


def test_country_records_and_scope_are_explicit():
    assert CanonicalRecord('x','pt','2026-01',1,'percent','x').geo_level=='nation'
    assert CanonicalRecord('x','grid-es-mainland','2026-01',1,'index','x').geo_level=='electricity_system'


def test_country_queries_filter_dimensions(config):
    plans=EurostatMacroAdapter().discover(SimpleNamespace(config=config))
    assert len(plans)==len(SERIES)
    assert all('geo=ES&geo=PT' in p.url for p in plans)
    inflation=next(p for p in plans if p.meta['metric_id']=='macro_inflation')
    assert 'prc_hicp_minr' in inflation.url and 'coicop18=TOTAL' in inflation.url


def test_electricity_growth_aligned_windows_require_complete_days():
    start=date(2024,1,1)
    values={(start+timedelta(days=i)).isoformat():100 for i in range(420)}
    end=max(values)
    for i in range(28):values[(date.fromisoformat(end)-timedelta(days=i)).isoformat()]=110
    assert electricity_growth(values)[end]==pytest.approx(10)
    values.pop((date.fromisoformat(end)-timedelta(days=10)).isoformat())
    assert end not in electricity_growth(values)


def activity_metric(key,value=1,change=1,freshness='current',quality='ok'):
    return dict(id=key,label=key,value=value,change=change,freshness=freshness,quality=quality,period='2026-07')


def test_activity_does_not_treat_rising_unemployment_as_expansion():
    ms=[activity_metric(k) for k in ('macro_gdp','macro_unemployment','macro_retail','macro_industry')]
    result=activity_reading(ms)
    assert result['positive']==3 and result['value']==75
    ms[1]['change']=-.2
    assert activity_reading(ms)['value']==100


def test_activity_excludes_missing_stale_suspect_and_context():
    ms=[activity_metric('macro_gdp'),activity_metric('macro_retail',freshness='stale'),activity_metric('macro_industry',quality='suspect'),activity_metric('macro_inflation',value=20),activity_metric('macro_unemployment',change=None)]
    assert activity_reading(ms)['available']==1
    assert activity_reading(ms)['value'] is None


def test_card_changes_rates_in_points_and_keeps_foreign_geography(config):
    series=Series(SeriesKey('macro_unemployment','pt','nation'),{'2026-04':7,'2026-07':6.5},'eurostat_macro','percent')
    store=SimpleNamespace(get=lambda metric,geo:series if geo=='pt' else None)
    card=EconomyBuilder(config,store,today=date(2026,9,26)).card('macro_unemployment','pt')
    assert card['value']==6.5 and card['change']==-.5
    assert card['change_unit']=='pp' and card['scope']=='Portugal · national'
    assert card['freshness']=='current'
    assert EconomyBuilder(config,store,today=date(2027,1,1)).card('macro_unemployment','pt')['freshness']=='stale'


def test_card_skips_future_period(config):
    series=Series(SeriesKey('macro_gdp','es','nation'),{'2026-Q2':.7,'2026-Q4':100},'eurostat_macro','percent')
    store=SimpleNamespace(get=lambda *args:series)
    card=EconomyBuilder(config,store,today=date(2026,9,26)).card('macro_gdp','es')
    assert card['period']=='2026-Q2' and card['value']==.7


def test_daily_hiring_parser_requires_adjusted_column():
    payload=RawPayload(FetchPlan('https://example.test','csv'),b'date,jobcountry,value,variable\n2026-01-01,ES,100,total postings\n','x')
    with pytest.raises(AdapterFailure):IndeedDailyAdapter().parse(payload,None)


def test_hiring_normalization_keeps_new_and_total_separate():
    frame=pd.DataFrame([{'date':'2026-01-01','jobcountry':'ES','indeed_job_postings_index_SA':100,'variable':'total postings'},{'date':'2026-01-01','jobcountry':'ES','indeed_job_postings_index_SA':90,'variable':'new postings'}])
    rows=list(IndeedDailyAdapter().normalize(frame,FetchPlan('https://example.test','csv',meta={'geo':'es'}),None))
    assert {r.metric_id:r.value for r in rows}=={'hiring_total':100,'hiring_new':90}


def test_worldbank_pagination_cannot_silently_truncate():
    payload=RawPayload(FetchPlan('https://example.test','json'),json.dumps([{'pages':2},[]]).encode(),'x')
    with pytest.raises(AdapterFailure):WorldBankAdapter().parse(payload,None)


def test_research_monthly_means_require_complete_calendar_month():
    daily={f'2025-01-{i:02d}':100 for i in range(1,32)}
    assert monthly(daily)=={'2025-01':100}
    daily.pop('2025-01-12')
    assert monthly(daily)=={}


def test_research_refuses_short_holdout_and_no_baseline_variance():
    short={period_shift('2020-01',i):float(i) for i in range(30)}
    assert walk_forward(short,short)['skill'] is None
    constant={period_shift('2010-01',i):100 for i in range(100)}
    result=walk_forward(constant,constant)
    assert result['skill'] is None and result['baseline_rmse']==0


def test_exports_hashes_and_capture_date_contract(repo_root):
    import hashlib
    import gzip
    manifest=json.loads((repo_root/'data/derived/economy-manifest.json').read_text())
    for f in manifest['files']:
        assert hashlib.sha256(gzip.decompress((repo_root/'data/derived'/f['storage_file']).read_bytes())).hexdigest()==f['sha256']
    assert 'published_at' in manifest['availability_policy']
    import csv
    panel = list(csv.DictReader(gzip.open(repo_root/'data/derived/economy-panel.csv.gz', 'rt')))
    keys = [(r['metric_id'], r['geo_id'], r['period']) for r in panel]
    assert len(keys) == len(set(keys))
    assert {'macro_gdp', 'macro_inflation', 'hiring_total', 'hiring_new'} <= {r['metric_id'] for r in panel}
    assert sum(r['geo_id'] == 'es' and r['period'] == '2026-01' for r in panel) >= 5


def test_capture_replay_never_backdates_a_backfill():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('export_as_of',Path(__file__).resolve().parents[1]/'scripts/export_as_of.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = [
        dict(metric_id='one',geo_id='es',period='2020-01',value='100',fetched_at='2026-09-26',revision='0'),
        dict(metric_id='one',geo_id='es',period='2020-01',value='101',fetched_at='2026-09-27',revision='1'),
        dict(metric_id='two',geo_id='es',period='2020-01',value='5',fetched_at='2026-09-26',revision='0'),
    ]
    assert module.as_of(rows,'2026-09-25') == []
    assert [r['value'] for r in module.as_of(rows,'2026-09-26')] == ['100','5']
    assert [r['value'] for r in module.as_of(rows,'2026-09-27')] == ['101','5']

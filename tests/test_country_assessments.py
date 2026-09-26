"""Economic interpretation, source units, source selection and vintage timing."""
import io
import json
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from citysignal.adapters.oecd_activity import OECDActivityAdapter, NAD, FIN
from citysignal.adapters.rbi_payments import RBIPaymentsAdapter
from citysignal.adapters.treasury_receipts import TreasuryReceiptsAdapter, CATEGORY
from citysignal.adapters.uk_payments import UKPaymentsAdapter
from citysignal.derive.assessment import assess_country, historical_position
from citysignal.derive.economy import EconomyBuilder, three_month_growth, rolling_mean
from citysignal.derive.store import Series, SeriesKey
from citysignal.derive.updates import build_updates
from citysignal.framework.adapter import AdapterFailure
from citysignal.framework.fetch import FetchPlan, RawPayload
from citysignal.framework.history import merge_records, read_history
from citysignal.framework.record import CanonicalRecord, period_shift


def test_oecd_accounts_do_not_confuse_annual_growth_or_nominal_units():
    row = dict(FREQ='Q', ADJUSTMENT='Y', REF_AREA='USA', SECTOR='S1', COUNTERPART_SECTOR='S1',
        TRANSACTION='B1GQ', INSTR_ASSET='_Z', ACTIVITY='_Z', EXPENDITURE='_Z', UNIT_MEASURE='PC',
        PRICE_BASE='L', TRANSFORMATION='G1', TABLE_IDENTIFIER='T0102', CURRENCY='_Z',
        TIME_PERIOD='2026-Q2', OBS_VALUE=.5, UNIT_MULT=0, OBS_STATUS='P')
    variants = [row, dict(row, TRANSFORMATION='GY', OBS_VALUE=2), dict(row, PRICE_BASE='V', OBS_VALUE=6)]
    plan = FetchPlan('https://example.test', 'csv', meta={'flow':NAD})
    adapter = OECDActivityAdapter()
    records = list(adapter.normalize(pd.DataFrame(variants), plan, None))
    assert [(r.metric_id, r.value, r.quality_flag) for r in records] == [('oecd_gdp', .5, 'estimated')]
    with pytest.raises(AdapterFailure):
        list(adapter.normalize(pd.DataFrame([row, row]), plan, None))


def test_euro_exchange_rate_is_one_shared_observation():
    row = dict(FREQ='M', REF_AREA='EA20', MEASURE='CC', UNIT_MEASURE='XDC_USD', ACTIVITY='_Z',
        ADJUSTMENT='_Z', TRANSFORMATION='_Z', TIME_HORIZ='_Z', METHODOLOGY='N',
        TIME_PERIOD='2026-08', OBS_VALUE=.9, OBS_STATUS='A', UNIT_MULT=0)
    records = list(OECDActivityAdapter().normalize(pd.DataFrame([row]), FetchPlan('https://example.test', 'csv', meta={'flow':FIN}), None))
    assert len(records) == 1 and records[0].geo_id == 'euro-area' and records[0].metric_id == 'oecd_euro_fx'
    assert three_month_growth({'2026-05':1, '2026-08':.9}, inverse=True)['2026-08'] == pytest.approx(100/9)
    assert three_month_growth({'2026-05':100, '2026-08':110})['2026-08'] == pytest.approx(10)


def test_fallback_preserves_source_definition_and_raw_metric(config):
    s = Series(SeriesKey('oecd_industry', 'us', 'nation'), {'2025-06':100, '2026-06':106}, 'oecd_activity', 'index')
    store = SimpleNamespace(get=lambda metric, geo:s if (metric, geo)==('oecd_industry','us') else None)
    card = EconomyBuilder(config, store, today=date(2026,9,26)).card('macro_industry', 'us')
    assert card['value'] == 6 and card['raw_metric_id'] == 'oecd_industry'
    assert card['source'] == 'OECD' and 'eurostat' not in card['source_url']
    assert 'ISIC B–E' in card['description']


def test_rbi_units_annual_columns_and_header_variants():
    headers = [('Payment System Indicators', 'PART I', 'label', '', '', '')]
    for unit in ['Volume (lakh)', 'Value (₹ crore)']:
        headers.extend([('Payment System Indicators','PART I',unit,'FY 2025-26','FY 2025-26','1'),
            ('Payment System Indicators','PART I',unit,'2025 August','2025 August','2'),
            ('Payment System Indicators','PART I',unit,'2026','August','4')])
    frame = pd.DataFrame([[label, 99999, 100, 200, 88888, 1000, 1500] for label in
        ['2.6 UPI @','4.1 Credit Cards','3.3 NETC (linked to bank account) @']], columns=pd.MultiIndex.from_tuples(headers))
    plan = FetchPlan('https://example.test', 'html')
    parsed = RBIPaymentsAdapter().parse(RawPayload(plan, frame.to_html(index=False).encode(), 'x'), None)
    values = {(r['metric'],r['period']):r['value'] for r in parsed.to_dict('records')}
    assert len(values) == 12
    assert values['in_upi_volume','2026-08'] == 20  # 200 lakh = 20 million
    assert values['in_card_value','2026-08'] == 15  # 1,500 crore = INR 15 billion
    assert not any('FY' in period for _, period in values)


def test_treasury_uses_complete_month_to_date_not_partial_daily_sum():
    base = dict(transaction_type='Deposits', transaction_catg=CATEGORY, account_type='Treasury General Account (TGA)')
    frame = pd.DataFrame([dict(base, record_date='2025-01-30',transaction_today_amt='10',transaction_mtd_amt='90'),
        dict(base,record_date='2025-01-31',transaction_today_amt='10',transaction_mtd_amt='100'),
        dict(base,record_date='2025-02-14',transaction_today_amt='20',transaction_mtd_amt='50')])
    rows = list(TreasuryReceiptsAdapter().normalize(frame, None, None))
    monthly = [r for r in rows if r.metric_id == 'us_withheld_tax']
    assert [(r.period, r.value) for r in monthly] == [('2025-01',100)]
    payload = RawPayload(FetchPlan('https://example.test','json'),json.dumps({'data':[{}], 'meta':{'total-pages':2}}).encode(),'x')
    with pytest.raises(AdapterFailure):
        TreasuryReceiptsAdapter().parse(payload, None)


def test_supplier_panel_deduplicates_companies_and_excludes_late_filings():
    rows = [dict(zip(['Report Id','Company number','Start date','End date','Filing date','Average time to pay','% Invoices not paid within agreed terms'],
        [i,f'C{i}','2024-01-01','2024-06-30','2024-07-10',30,20])) for i in range(100)]
    rows += [dict(rows[0], **{'Report Id':1000,'Filing date':'2024-08-10','Average time to pay':90})]
    frame = pd.DataFrame(rows)
    for c in ['Start date','End date','Filing date']:frame[c] = pd.to_datetime(frame[c])
    result = list(UKPaymentsAdapter().normalize(frame, None, None))
    assert next(r.value for r in result if r.metric_id == 'uk_supplier_panel' and r.period == '2024-07') == 100
    assert next(r.value for r in result if r.metric_id == 'uk_supplier_payment_days' and r.period == '2024-07') == 30
    assert not any(r.period < '2024-07' for r in result)


def m(key, value=1, change=.2, period='2026-08', **kwargs):
    return dict(id=key,label=key,value=value,change=change,period=period,unit='percent',source_id='test',
        cadence='monthly',freshness='current',quality='ok',signal_type='official',series=kwargs.pop('series',[]),**kwargs)


def test_assessment_deduplicates_prices_and_excludes_stale_power_from_growth():
    cpi=m('bis_cpi',3,-.2); hicp=m('macro_inflation',4,.5)
    power=m('power_demand',20,10); power['source_id']='ember'
    gdp=m('macro_gdp',.5,.1,'2026-Q2'); industry=m('macro_industry',-1,-.3)
    result=assess_country([cpi,hicp,power,gdp,industry],'2026-09-26')
    inflation=next(p for p in result['pillars'] if p['id']=='inflation')
    growth=next(p for p in result['pillars'] if p['id']=='growth')
    assert inflation['direction']=='down' and len(inflation['evidence'])==1
    assert growth['direction']=='mixed' and {e['metric_id'] for e in growth['evidence']}=={'macro_gdp','macro_industry'}
    assert result['families'].count('consumer_prices')==1
    industry['freshness']='stale'
    assert assess_country([gdp,industry],'2026-09-26')['pillars'][0]['direction']=='up'


def test_hiring_divergence_requires_matching_latest_dates():
    new=m('hiring_new',90,-10,'2026-09-18',series=[dict(period='2026-08-21',value=100),dict(period='2026-09-18',value=90)])
    total=m('hiring_total',110,10,'2026-09-18',series=[dict(period='2026-08-21',value=100),dict(period='2026-09-18',value=110)])
    assert 'New hiring' in assess_country([new,total],'2026-09-26')['relationships'][0]['title']
    total['period']='2026-09-17'
    assert not assess_country([new,total],'2026-09-26')['relationships']


def test_historical_rank_excludes_future_values_and_requires_sample():
    points=[dict(period=period_shift('2024-01',i),value=i) for i in range(25)]
    card=m('test',24,period='2026-01',series=points+[dict(period='2027-01',value=999)])
    rank=historical_position(card)
    assert rank['observations']==24 and rank['percentile']==100
    card['series']=points[:10]
    assert historical_position(card) is None
    assert rolling_mean({'2026-01-01':10,'2026-01-03':20},days=2)=={}


def test_publication_metadata_survives_unchanged_responses(tmp_path):
    path=tmp_path/'history.csv'
    first=CanonicalRecord('test','us','2026-08',10,'index','test',published_at='2026-09-10',fetched_at='2026-09-12T12:00:00+00:00')
    merge_records(path,[first],revisions_allowed=True)
    result=merge_records(path,[CanonicalRecord('test','us','2026-08',10,'index','test')],revisions_allowed=True)
    assert result.unchanged==1 and read_history(path)[0]['published_at']=='2026-09-10'


def test_update_ledger_does_not_call_backfilled_history_new_releases(tmp_path):
    path=tmp_path/'history/test/test.csv'
    merge_records(path,[CanonicalRecord('test','us','2025-01',1,'index','test',fetched_at='2026-09-26'),
        CanonicalRecord('test','us','2026-08',2,'index','test',fetched_at='2026-09-26')],revisions_allowed=True)
    card=m('test',2);card.update(geo_id='us',source='Test',scope='United States · national')
    country=dict(code='us',name='United States',metrics=[card],alternative=[])
    result=build_updates(SimpleNamespace(data_dir=tmp_path),[country],date(2026,9,26))
    assert len(result['events'])==1 and result['events'][0]['period']=='2026-08'
    assert result['events'][0]['published_at'] is None

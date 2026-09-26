"""Integrity checks for global scope and source-specific signal definitions."""
import json
import gzip
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from citysignal.countries import COUNTRIES, MARKETS
from citysignal.adapters.ember import EmberAdapter
from citysignal.adapters.portwatch import PortWatchAdapter, VESSELS
from citysignal.framework.adapter import AdapterFailure
from citysignal.framework.fetch import FetchPlan, RawPayload
from citysignal.framework.record import geo_level_of
from citysignal.derive.economy import EconomyBuilder


def test_major_market_scope_is_explicit_and_valid():
    assert len(COUNTRIES) == 28
    assert {'es', 'pt', 'us', 'cn', 'in', 'jp', 'de'} <= COUNTRIES.keys()
    assert all('pinned' not in meta and 'focus' not in meta for meta in MARKETS.values())
    assert all(geo_level_of(c) == 'nation' for c in COUNTRIES)


def test_portwatch_rejects_truncated_response_and_api_errors():
    for body in [{'features': [], 'exceededTransferLimit': True}, {'error': {'code': 400}}]:
        p = RawPayload(FetchPlan('https://example.test', 'json'), json.dumps(body).encode(), 'x')
        with pytest.raises(AdapterFailure):
            PortWatchAdapter().parse(p, None)


def test_portwatch_missing_category_is_not_zero_and_current_month_excluded():
    row = {'ISO3': 'USA', 'date': '2025-01-01'}
    for prefix in ('ais_import', 'ais_export', 'ais_portcalls'):
        row.update({f'{prefix}_{v}': 10 for v in VESSELS})
    row['ais_import_tanker'] = None
    current = {**row, 'date': date.today().replace(day=1).isoformat()}
    records = list(PortWatchAdapter().normalize(pd.DataFrame([row, current]), FetchPlan('https://example.test', 'json', meta={'geo': 'us'}), None))
    assert {r.metric_id: r.value for r in records} == {'shipping_exports': 50, 'shipping_calls': 50, 'shipping_container_exports': 10, 'shipping_container_imports': 10, 'shipping_tanker_exports': 10}
    assert all(r.period == '2025-01' and r.quality_flag == 'estimated' for r in records)
    with pytest.raises(AdapterFailure):
        list(PortWatchAdapter().normalize(pd.DataFrame([row]), FetchPlan('https://example.test', 'json', meta={'geo': 'cn'}), None))


def test_ember_exact_units_prevent_generation_share_becoming_demand():
    rows = [dict(zip(['ISO 3 code', 'Area type', 'Date', 'Category', 'Subcategory', 'Variable', 'Unit', 'Value'], r)) for r in [
        ('USA', 'Country or economy', '2025-01-01', 'Electricity demand', 'Demand', 'Demand', 'TWh', 100),
        ('USA', 'Country or economy', '2025-01-01', 'Electricity generation', 'Aggregate fuel', 'Fossil', '%', 60),
        ('USA', 'Country or economy', '2025-01-01', 'Electricity generation', 'Aggregate fuel', 'Fossil', 'TWh', 50),
    ]]
    records = list(EmberAdapter().normalize(pd.DataFrame(rows), None, None))
    assert {r.metric_id: r.value for r in records} == {'power_demand': 100, 'power_fossil_share': 60}
    with pytest.raises(AdapterFailure):
        list(EmberAdapter().normalize(pd.DataFrame([rows[0], rows[0]]), None, None))


def test_derived_trade_gap_joins_matching_dates(config):
    base = dict(cadence='monthly', max_age_days=100, quality='estimated', source='Test')
    cards = [
        dict(base, id='shipping_exports', series=[{'period': '2026-06', 'value': 4}, {'period': '2026-07', 'value': 9}]),
        dict(base, id='shipping_imports', series=[{'period': '2026-06', 'value': -2}]),
    ]
    recipe = EconomyBuilder(config, None, today=date(2026, 9, 26)).recipes('us', cards)[0]
    assert recipe['period'] == '2026-06' and recipe['value'] == 6
    assert recipe['signal_type'] == 'derived' and recipe['quality'] == 'estimated'
    assert recipe['inputs'] == ['shipping_exports', 'shipping_imports']


def test_collected_global_panel_has_real_non_european_signals(repo_root):
    d = json.loads(gzip.decompress((repo_root/'data/derived/economy.json.gz').read_bytes()))
    countries = {c['code']: c for c in d['countries']}
    for code in ('es', 'pt', 'us', 'cn', 'in', 'jp', 'de'):
        c = countries[code]
        assert 'wb_gdp_growth' in {m['id'] for m in c['metrics']}
        assert {'power_demand', 'shipping_exports', 'shipping_imports'} <= {m['id'] for m in c['alternative']}
        assert all(m['series'] and m['source_url'] and m['period'] for m in c['metrics'] + c['alternative'])
    assert countries['us']['activity']['available'] == 4
    assert {'macro_gdp', 'macro_industry', 'macro_retail', 'macro_unemployment'} <= {m['id'] for m in countries['us']['metrics']}
    assert not countries['pt']['cities']  # Spanish local data cannot leak across countries.
    overview = json.loads((repo_root/'data/derived/economy-overview.json').read_text())
    assert not any(m['series'] for c in overview['countries'] for m in c['alternative'])
    assert (repo_root/'data/derived/economy-overview.json').stat().st_size < 1_000_000

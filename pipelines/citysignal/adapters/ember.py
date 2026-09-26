"""Monthly physical electricity activity and energy-mix exposure from Ember."""
import io
import math

import pandas as pd

from ..countries import ISO3_TO_ISO2
from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord, period_end, utc_today

# Exact dimensions protect against silently mixing TWh, percentages and regions.
MEASURES = {
    ('Electricity demand', 'Demand', 'Demand', 'TWh'): ('power_demand', 'twh'),
    ('Electricity generation', 'Aggregate fuel', 'Fossil', '%'): ('power_fossil_share', 'percent'),
    ('Power sector emissions', 'CO2 intensity', 'CO2 intensity', 'gCO2/kWh'): ('power_carbon_intensity', 'gco2_kwh'),
    ('Electricity prices', 'Day-ahead electricity price', 'Day-ahead electricity price', 'EUR/MWh'): ('power_price', 'eur_mwh'),
}


class EmberAdapter(BaseAdapter):
    manifest = SourceManifest(
        source_id='ember', publisher='Ember', license='Ember data terms; CC BY 4.0',
        attribution='Source: Ember, Monthly Electricity Data',
        docs_url='https://ember-energy.org/data/monthly-electricity-data/',
        cadence='monthly', geo_level='nation', max_age_days=150,
        formats=('csv',), kind='research', revisions_allowed=True,
        notes='National monthly electricity demand, fossil generation share, carbon intensity and available day-ahead prices. Source lags vary by country. Demand is not weather-adjusted; energy mix is exposure, not a growth forecast.',
    )

    def discover(self, ctx):
        return [FetchPlan(url='https://files.ember-energy.org/public-downloads/monthly_full_release_long_format.csv', fmt='csv', label='monthly-electricity')]

    def parse(self, payload, ctx):
        frame = pd.read_csv(io.StringIO(payload.text()))
        required = {'ISO 3 code', 'Area type', 'Date', 'Category', 'Subcategory', 'Variable', 'Unit', 'Value'}
        if not required.issubset(frame.columns):
            raise AdapterFailure('Ember schema changed')
        return frame.loc[frame['ISO 3 code'].isin(ISO3_TO_ISO2) & frame['Area type'].eq('Country or economy')]

    def normalize(self, frame, plan, ctx):
        seen = set()
        for row in frame.to_dict('records'):
            dims = tuple(row[k] for k in ('Category', 'Subcategory', 'Variable', 'Unit'))
            if dims not in MEASURES or pd.isna(row['Value']):
                continue
            period = str(row['Date'])[:7]
            if period < '2015-01' or period_end(period) >= utc_today():
                continue
            metric, unit = MEASURES[dims]
            geo = ISO3_TO_ISO2[row['ISO 3 code']]
            value = float(row['Value'])
            key = metric, geo, period
            if key in seen or not math.isfinite(value):
                raise AdapterFailure('Duplicate or invalid Ember observation')
            seen.add(key)
            yield CanonicalRecord(metric_id=metric, geo_id=geo, period=period,
                value=value, unit=unit, source_id='ember')
        if not seen:
            raise AdapterFailure('No usable Ember observations')

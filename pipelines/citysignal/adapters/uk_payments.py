"""Rolling company-level panel from statutory supplier-payment disclosures.

Each completed month uses only reports filed by that month-end, with one latest
report per company. Medians are company-weighted, never invoice-weighted.
"""
import io

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord, utc_today

URL = 'https://check-payment-practices.service.gov.uk/export/csv/'
COLUMNS = ['Report Id', 'Company number', 'Start date', 'End date', 'Filing date',
    'Average time to pay', '% Invoices not paid within agreed terms']


class UKPaymentsAdapter(BaseAdapter):
    manifest = SourceManifest(source_id='uk_payments', publisher='UK Department for Business and Trade',
        license='Open Government Licence v3.0', attribution='Source: UK Payment Practices Reporting; calculations by CitySignal',
        docs_url='https://www.gov.uk/check-when-businesses-pay-invoices', cadence='monthly',
        geo_level='nation', max_age_days=75, formats=('csv',), revisions_allowed=True, read_timeout=120,
        notes='Month-end panel of large-company payment disclosures filed by that date. Latest report per company, report end and filing within 12 months. Company medians, not invoice-weighted estimates; reporting periods and panel membership vary.')

    def discover(self, ctx):
        return [FetchPlan(URL, 'csv', label='payment-disclosures')]

    def parse(self, payload, ctx):
        try:
            frame = pd.read_csv(io.BytesIO(payload.content), usecols=COLUMNS, dtype={'Company number':str})
        except ValueError as exc:
            raise AdapterFailure('UK payment disclosure schema changed') from exc
        for c in ['Start date', 'End date', 'Filing date']:
            frame[c] = pd.to_datetime(frame[c], errors='coerce')
        for c in ['Average time to pay', '% Invoices not paid within agreed terms']:
            frame[c] = pd.to_numeric(frame[c], errors='coerce')
        return frame.dropna(subset=COLUMNS)

    def normalize(self, frame, plan, ctx):
        valid = frame[frame['Average time to pay'].between(0, 730) &
            frame['% Invoices not paid within agreed terms'].between(0, 100) &
            (frame['End date'] >= frame['Start date']) & (frame['Filing date'] >= frame['End date'])]
        if len(valid) < .9 * len(frame):
            raise AdapterFailure('Too many invalid UK payment disclosures')
        valid = valid.sort_values(['End date', 'Filing date', 'Report Id'])
        for end in pd.date_range('2019-01-31', pd.Timestamp(utc_today()) - pd.Timedelta(days=1), freq='ME'):
            cutoff = end - pd.DateOffset(years=1)
            cohort = valid[(valid['Filing date'] <= end) & (valid['Filing date'] > cutoff) &
                (valid['End date'] > cutoff)].drop_duplicates('Company number', keep='last')
            if len(cohort) < 100:
                continue
            period = end.strftime('%Y-%m')
            for metric, value, unit in [
                ('uk_supplier_payment_days', cohort['Average time to pay'].median(), 'days'),
                ('uk_supplier_overdue', cohort['% Invoices not paid within agreed terms'].median(), 'percent'),
                ('uk_supplier_panel', len(cohort), 'companies'),
            ]:
                yield CanonicalRecord(metric, 'gb', period, float(value), unit, self.manifest.source_id)

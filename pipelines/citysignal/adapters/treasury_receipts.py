"""Daily US withheld income/FICA receipts and complete monthly cash receipts."""
import math
from urllib.parse import urlencode

import pandas as pd

from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord, period_end, utc_today

BASE = 'https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/deposits_withdrawals_operating_cash'
CATEGORY = 'Taxes - Withheld Individual/FICA'


class TreasuryReceiptsAdapter(BaseAdapter):
    manifest = SourceManifest(source_id='treasury_receipts', publisher='US Treasury, Bureau of the Fiscal Service',
        license='US government public data', attribution='Source: US Treasury, Daily Treasury Statement',
        docs_url='https://fiscal.treasury.gov/accounting/daily-treasury-statement',
        cadence='daily', geo_level='nation', max_age_days=10, formats=('json',),
        revisions_allowed=True, notes='Nominal cash receipts of withheld individual income and FICA taxes. Treasury reporting days; tax rules, bonuses and payment calendars affect the series.')

    def discover(self, ctx):
        query = urlencode({'filter':f'record_date:gte:2023-02-14,transaction_type:eq:Deposits,transaction_catg:eq:{CATEGORY}',
            'sort':'record_date', 'page[size]':10000})
        return [FetchPlan(f'{BASE}?{query}', 'json', label='withheld-taxes')]

    def parse(self, payload, ctx):
        data = payload.json()
        if data.get('meta', {}).get('total-pages', 0) != 1 or not data.get('data'):
            raise AdapterFailure('Treasury response missing or paginated')
        if data['meta'].get('dataFormats', {}).get('transaction_today_amt') != '$1,000,000':
            raise AdapterFailure('Treasury cash unit changed')
        return pd.DataFrame(data['data'])

    def normalize(self, frame, plan, ctx):
        seen = set()
        monthly = {}
        today = utc_today()
        for row in frame.to_dict('records'):
            if row['transaction_type'] != 'Deposits' or row['transaction_catg'] != CATEGORY or row['account_type'] != 'Treasury General Account (TGA)':
                raise AdapterFailure('Unexpected Treasury category or account')
            period = str(row['record_date'])
            if period in seen:
                raise AdapterFailure('Duplicate Treasury reporting day')
            seen.add(period)
            value = float(row['transaction_today_amt'])
            if not math.isfinite(value):
                raise AdapterFailure('Invalid Treasury amount')
            if period_end(period) >= today:
                continue
            yield CanonicalRecord('us_withheld_tax_daily', 'us', period, value, 'USD million', self.manifest.source_id)
            month = period[:7]
            if month not in monthly or period > monthly[month]['record_date']:
                monthly[month] = row
        for month, row in sorted(monthly.items()):
            end = period_end(month)
            # Month-to-date on the last reporting day includes holiday carryover;
            # never sum an incomplete sample of reporting days into a month.
            last_weekday = end
            while last_weekday.weekday() >= 5:
                last_weekday -= pd.Timedelta(days=1)
            if end >= today or row['record_date'] != last_weekday.isoformat():
                continue
            yield CanonicalRecord('us_withheld_tax', 'us', month, float(row['transaction_mtd_amt']),
                'USD million', self.manifest.source_id)

"""RBI monthly payments: UPI, card spending and bank-linked toll payments."""
import calendar
import io
import math
import re
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord

PAGE = 'https://www.rbi.org.in/Scripts/PSIUserView.aspx'
ROWS = {
    '2.6 UPI': ('in_upi_volume', 'in_upi_value'),
    '4.1 Credit Cards': ('in_card_volume', 'in_card_value'),
    '3.3 NETC (linked to bank account)': ('in_bank_toll_volume', 'in_bank_toll_value'),
}


class RBIPaymentsAdapter(BaseAdapter):
    manifest = SourceManifest(source_id='rbi_payments', publisher='Reserve Bank of India',
        license='RBI website copyright and reuse terms; attribution', attribution='Source: Reserve Bank of India, Payment System Indicators',
        docs_url=PAGE, cadence='monthly', geo_level='nation', max_age_days=90,
        formats=('html',), revisions_allowed=True, aggregates_across_plans=True,
        notes='UPI includes transfers and merchant payments. Credit-card spending is nominal. NETC series covers bank-linked payments, not all FASTag transactions. Platform adoption and scope changes affect growth.')

    def discover(self, ctx):
        payload = ctx.fetcher.get(FetchPlan(PAGE, 'html'))
        soup = BeautifulSoup(payload.text(), 'html.parser')
        links = list(dict.fromkeys(urljoin(PAGE, a['href']) for a in soup.select('a[href]')
            if re.search(r'PSIUserView\.aspx\?Id=\d+', a['href'], re.I)))
        if not links:
            raise AdapterFailure('RBI payment report links missing')
        # Older editions first; the latest report wins when past months recur.
        return [FetchPlan(url, 'html', label=f'payments-{i}') for i, url in enumerate(reversed(links))]

    def parse(self, payload, ctx):
        soup = BeautifulSoup(payload.text(), 'html.parser')
        tables = [t for t in soup.select('table') if not t.select('table') and '2.6 UPI' in t.get_text(' ', strip=True)]
        if len(tables) != 1:
            raise AdapterFailure('RBI payment table missing or ambiguous')
        table = pd.read_html(io.StringIO(str(tables[0])))[0]
        rows = []
        for label, metrics in ROWS.items():
            matches = table[table.iloc[:, 0].astype(str).str.replace(r'\s+[@$#*]$', '', regex=True).str.strip().eq(label)]
            if len(matches) != 1:
                raise AdapterFailure(f'RBI category missing or duplicated: {label}')
            for index, column in enumerate(table.columns[1:], 1):
                parts = [str(p).strip() for p in column]
                if any('FY ' in p for p in parts):
                    continue
                measures = [p for p in parts if p.startswith(('Volume (', 'Value ('))]
                if len(measures) != 1:
                    raise AdapterFailure('RBI measurement header changed')
                measure = measures[0]
                header = ' '.join(parts[parts.index(measure) + 1:-1])
                year = re.search(r'\b(20\d{2})\b', header)
                month = next((i for i, name in enumerate(calendar.month_name) if i and re.search(rf'\b{name}\b', header, re.I)), None)
                if not year or month is None:
                    raise AdapterFailure(f'Unrecognised RBI period: {header}')
                if measure == 'Volume (lakh)':
                    metric, unit, factor = metrics[0], 'million transactions', .1
                elif measure in ('Value (₹ crore)', 'Value (Rs. crore)'):
                    metric, unit, factor = metrics[1], 'INR billion', .01
                else:
                    raise AdapterFailure(f'Unrecognised RBI unit: {measure}')
                value = float(matches.iloc[0, index]) * factor
                if not math.isfinite(value):
                    raise AdapterFailure('Missing RBI payment observation')
                rows.append(dict(metric=metric, period=f'{year[1]}-{month:02d}', value=value, unit=unit))
        return pd.DataFrame(rows)

    def normalize(self, frame, plan, ctx):
        for row in frame.to_dict('records'):
            yield CanonicalRecord(row['metric'], 'in', row['period'], row['value'], row['unit'], self.manifest.source_id)

    def finalize(self, records, ctx):
        return list({r.key: r for r in records}.values())

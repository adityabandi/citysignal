"""German large-truck mileage, adjusted by Destatis and Bundesbank."""
import io
import math
import re
from datetime import date
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

from ..framework.adapter import AdapterFailure, BaseAdapter, SourceManifest
from ..framework.fetch import FetchPlan
from ..framework.record import CanonicalRecord

PAGE = 'https://www.destatis.de/DE/Themen/Branchen-Unternehmen/Industrie-Verarbeitendes-Gewerbe/Tabellen/Lkw-Maut-Fahrleistungsindex-Daten.html'


class DestatisFreightAdapter(BaseAdapter):
    manifest = SourceManifest(source_id='destatis_freight', publisher='Destatis / Bundesbank / BALM',
        license='Data licence Germany attribution 2.0', attribution='Source: Destatis, Deutsche Bundesbank and BALM',
        docs_url=PAGE, cadence='daily', geo_level='nation', max_age_days=21,
        formats=('xlsx',), revisions_allowed=True,
        notes='Daily mileage of trucks with at least four axles on German motorways. Calendar and seasonally adjusted; 2021=100. Updated weekly.')

    def discover(self, ctx):
        payload = ctx.fetcher.get(FetchPlan(PAGE, 'html'))
        soup = BeautifulSoup(payload.text(), 'html.parser')
        urls = [urljoin(PAGE, a['href'].replace('&amp;', '&')) for a in soup.select('a[href]')
                if 'lkw-maut-fahrleistungsindex' in a['href'].lower() and '.xlsx' in a['href'].lower()]
        if not urls:
            raise AdapterFailure('Destatis freight workbook link missing')
        return [FetchPlan(urls[0], 'xlsx', label='truck-mileage')]

    def parse(self, payload, ctx):
        book = pd.ExcelFile(io.BytesIO(payload.content))
        frame = pd.read_excel(book, sheet_name='csv-42191-b01')
        required = {'Gebiet', 'Datum', 'Saisonbereinigung', 'Indexwert'}
        if not required <= set(frame):
            raise AdapterFailure('Destatis freight schema changed')
        title = ' '.join(pd.read_excel(book, sheet_name='42191-b01', header=None, nrows=1).astype(str).values.ravel())
        if '2021=100' not in title:
            raise AdapterFailure('Destatis freight reference year changed')
        cover = ' '.join(pd.read_excel(book, sheet_name='Titel', header=None).astype(str).values.ravel())
        released = re.search(r'Erschienen am (\d{1,2})\. (\w+) (\d{4})', cover)
        months = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember']
        if released and released[2] in months:
            frame.attrs['published_at'] = date(int(released[3]), months.index(released[2]) + 1, int(released[1])).isoformat()
        return frame

    def normalize(self, frame, plan, ctx):
        rows = frame[frame['Gebiet'].eq('Deutschland insgesamt') & frame['Saisonbereinigung'].str.strip().eq('Kalender- und saisonbereinigt (KSB)')]
        seen = set()
        latest = pd.to_datetime(rows['Datum']).max().date().isoformat()
        for row in rows.to_dict('records'):
            period = pd.Timestamp(row['Datum']).date().isoformat()
            if period < '2019-01-01':
                continue
            try:
                value = float(row['Indexwert'])
            except (ValueError, TypeError):
                continue
            if period in seen or not math.isfinite(value):
                raise AdapterFailure('Invalid or duplicate truck mileage observation')
            seen.add(period)
            yield CanonicalRecord('de_truck_mileage', 'de', period, value, 'index', self.manifest.source_id,
                published_at=frame.attrs.get('published_at') if period == latest else None)
        if not seen:
            raise AdapterFailure('No adjusted German freight observations')

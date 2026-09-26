"""Observation and revision ledger, preserving source and collection dates."""
from collections import defaultdict

from ..framework.history import read_history
from ..framework.record import period_end


def build_updates(config, countries, today):
    requested = defaultdict(list)
    for country in countries:
        for m in country['metrics'] + country['alternative']:
            if m['signal_type'] != 'derived':
                requested[(m['source_id'], m.get('raw_metric_id', m['id']), m['geo_id'])].append((country, m))
    history = {}
    events = []
    for (source, raw_metric, geo), destinations in requested.items():
        path = config.data_dir / 'history' / source / f'{raw_metric}.csv'
        if path not in history:
            history[path] = read_history(path)
        rows = [r for r in history[path] if r['geo_id'] == geo and r.get('value') and period_end(r['period']) < today]
        by_period = defaultdict(list)
        for row in rows:
            by_period[row['period']].append(row)
        latest = max(by_period, default=None)
        first_capture = min((r['fetched_at'][:10] for r in rows if r.get('fetched_at')), default='')
        # Initial backfills appear once at their latest observation, not as
        # thousands of new releases. Subsequent revisions remain visible.
        for period, revisions in by_period.items():
            revisions.sort(key=lambda r:int(r['revision'] or 0))
            prior = None
            for row in revisions:
                captured = row.get('fetched_at') or ''
                if captured[:10] > today.isoformat():
                    continue
                if period != latest and ((prior is None and captured[:10] == first_capture) or captured[:10] < (today.replace(day=1)).isoformat()):
                    prior = row
                    continue
                for country, metric in destinations:
                    events.append({'country':country['code'], 'country_name':country['name'],
                        'metric_id':metric['id'], 'raw_metric_id':raw_metric, 'label':metric['label'],
                        'period':period, 'value':float(row['value']), 'unit':row['unit'],
                        'published_at':row.get('published_at') or None, 'captured_at':captured or None,
                        'revision':int(row['revision'] or 0), 'event':('Metadata' if float(row['value']) == float(prior['value']) else 'Revision') if prior else 'Observation',
                        'prior_value':float(prior['value']) if prior else None,
                        'revision_change':round(float(row['value']) - float(prior['value']), 6) if prior else None,
                        'source':metric['source'], 'source_id':source,
                        'scope':metric['scope']})
                prior = row
    events.sort(key=lambda e:(e['captured_at'] or '', e['period'], e['country_name']), reverse=True)
    return {'as_of':today.isoformat(), 'events':events,
            'method':'Raw source units. Published dates are populated only when supplied by the publisher. Collection time dates the captured version. Initial backfills appear at their latest observation; historical revisions collected this month are retained.'}

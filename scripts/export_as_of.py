"""Export only observations captured by a UTC calendar date.

This is a capture-time replay. It does not reconstruct unknown historical source
publication timestamps. No backfilled row can appear before its fetched_at date.
"""
from __future__ import annotations
import argparse
import csv
import gzip
from datetime import date
from pathlib import Path


def as_of(rows, cutoff):
    latest = {}
    for row in rows:
        captured = row.get('fetched_at', '')[:10]
        if not captured or captured > cutoff:
            continue
        if row.get('published_at') and row['published_at'][:10] > cutoff:
            continue
        key = (row['metric_id'], row['geo_id'], row['period'])
        rank = (captured, int(row.get('revision') or 0))
        if key not in latest or rank > latest[key][0]:
            latest[key] = (rank, row)
    return [latest[key][1] for key in sorted(latest)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, type=date.fromisoformat, help='UTC capture cutoff YYYY-MM-DD')
    parser.add_argument('--input', type=Path, default=Path(__file__).resolve().parents[1] / 'data/derived/economy-vintages.csv.gz')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error('Output must not overwrite the vintage archive')
    with (gzip.open(args.input, 'rt', newline='') if args.input.suffix == '.gz' else args.input.open(newline='')) as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = as_of(reader, args.as_of.isoformat())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    print(f'{len(rows):,} observations captured by {args.as_of}; publication-time history is not implied.')


if __name__ == '__main__':
    main()

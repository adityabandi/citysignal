# CitySignal

**What is changing in housing demand, economic stress, tourism and supply across eight Spanish cities — using auditable evidence, exact geographies and visible data freshness.**

Madrid · Barcelona · València · Málaga · Sevilla · Palma · Bilbao · Zaragoza

CitySignal is a public monitor with no server, no database and no paid API. A weekly
GitHub Action pulls official statistics, normalises them into small city-level time
series that are **committed to this repository**, and rebuilds a static site. Git is
the historical data store and the audit trail: every number the site shows can be
traced to a commit, a source and an observation date.

It does not predict a crash. It tells you what is happening, how fresh the evidence
is, and which of a city's signals currently disagree with each other.

## How it works

```
weekly Action → fetch (each adapter isolated) → validate → normalise
   → append to data/history/*.csv → derive indices and regimes
   → tests → commit → build site → GitHub Pages
```

If a source breaks, its history is left untouched, the site keeps serving the
last-good value with a stale badge, and one GitHub issue is opened for that adapter
and closed automatically when it recovers.

## Ground rules

- **Geography is never fudged.** Every chart states whether it shows a municipality,
  province, autonomous community, airport or functional urban area. A provincial
  series is useful context for a city; it is not that city's number.
- **Observation date and last-checked date are shown separately.** A source we
  checked this morning may still be reporting April.
- **Official, research and commercial evidence are labelled as such.** Inside Airbnb
  is a research snapshot, not a register. Search and news volumes are *attention*,
  not demand.
- **Leading claims must earn the label.** The lead-lag lab measures whether each
  candidate leading signal actually leads an observable outcome out-of-sample, and
  publicly demotes it when it stops.
- **Rules are versioned and visible.** Regime classification is a small ordered rule
  set in `config/rules/`, not a model. Every published classification records which
  rule fired and which rules version produced it.

## Local development

```bash
uv sync --extra dev
uv run citysignal all          # fetch every source, then derive
uv run pytest                  # adapters, framework, config integrity, smoke
npm install && npm run dev     # site, reads committed data, works offline
```

Useful subcommands:

```bash
uv run citysignal fetch --source ine      # one adapter
uv run citysignal fetch --all --force     # ignore content-hash skipping
uv run citysignal derive                  # rebuild indices, regimes, site JSON
uv run citysignal health                  # source health as a table
```

## Layout

| Path | What lives there |
|---|---|
| `config/` | city, metric and source registries; search baskets; versioned index and regime rules |
| `pipelines/citysignal/framework/` | fetching, payload sniffing, the history store, validation, health |
| `pipelines/citysignal/adapters/` | one module per source |
| `pipelines/citysignal/derive/` | transforms, sub-indices, regimes, signature matching, lead-lag lab |
| `data/history/` | committed canonical time series, one CSV per source and metric |
| `data/derived/` | what the site reads |
| `data/quality/` | source health, fetch state, run reports, quarantined revisions |
| `src/` | the Observable Framework site |

## Sources

Every source, its licence, attribution and current health is listed on the site's
Sources page and declared in [`config/sources.yml`](config/sources.yml). Core data
comes from INE, the Ministry of Housing, SEPE, the Social Security administration,
the Bank of Spain, Aena, Puertos del Estado, the CGPJ, the BOE and BORME, plus the
Madrid open-data portal for the district-level deep dive.

## Licence

Code is MIT. Data belongs to the publishers named in `config/sources.yml` and is
redistributed here only where their terms permit it; sources marked
`redistribute: false` appear only as derived aggregates.

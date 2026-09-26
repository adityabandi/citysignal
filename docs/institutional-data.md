# CitySignal data contract

The market monitor covers 28 economies. Markets are ordered alphabetically;
selection is controlled by the user and saved in the browser. Country views
combine official, alternative and derived series. Spain includes eight cities
and Madrid district coverage.

## Connected country datasets

| Source | Measures | Coverage | Frequency |
| --- | --- | --- | --- |
| BIS | National CPI, end-of-month policy rate | CPI: 28 countries; rates: 19 national series and euro-area series shared by seven markets | Monthly |
| OECD | Amplitude-adjusted composite leading indicator | 17 countries | Monthly |
| Caldara & Iacoviello | Country geopolitical-risk news share | 25 countries | Monthly |
| IMF PortWatch | Seaborne imports, exports and calls; container and tanker tonnes | 27 countries | Monthly |
| Indeed Hiring Lab | Total and new job postings | 10 countries | Daily observations; weekly publisher refresh |
| Ember | Electricity load, fossil generation share, carbon intensity, wholesale prices | Power: 25 countries; prices: 11 | Monthly |
| Eurostat / DG ECFIN | GDP, industry, retail, unemployment, employment, HICP, sentiment, house prices | Six countries; availability varies by measure | Monthly / quarterly |
| World Bank | GDP, CPI, unemployment, population, PPP GDP per capita, trade / GDP | 28 countries | Annual |
| Red Eléctrica | National electricity load; mainland large-user demand index | Spain, with distinct mainland scope | Daily / monthly |
| ECB | Household mortgage rates | Existing Spanish series | Monthly |

Source URLs, terms, definitions and attribution are stored in `config/sources.yml`
and each adapter's manifest. Existing city datasets include INE, BORME,
municipal sources, aviation, ports, housing and attention measures.

## Measurement

- OECD uses amplitude-adjusted CLI (LI / AA / IX / H), trend=100. Normalised
  and trend-restored variants are excluded.
- BIS CPI uses annual percentage changes (unit 771). Policy rates use monthly
  end-of-period observations and publisher-specific instrument definitions.
  Euro-area rates retain `euro_area` geography. A shared policy rate is not
  counted as a separate national observation in the raw panel.
- Geopolitical news uses `GPRC` country shares, measured as a percentage of
  articles. `GPRHC` historical variants and global normalised indices are
  separate definitions and are excluded. The source newspaper sample is US-based.
- Shipping aggregates five vessel types. Missing categories invalidate the
  aggregate without invalidating independently reported category observations.
  Container and tanker series retain vessel-type definitions. Physical tonnes
  include transshipment at covered ports; they are not customs-value measures.
- Electricity is classified as energy exposure. Aggregate consumption includes
  data centres and responds to weather, electrification, efficiency and sector
  composition. It is not used as a broad-output indicator.
- REE daily growth compares complete 28-day windows with 364 days earlier.
  The weekday mix aligns; weather and movable holidays are not adjusted.
- Hiring flow gap = 28-day growth in new postings minus 28-day growth in total
  postings. Export–import gap = export tonnage y/y growth minus import tonnage
  y/y growth, joined on matching months. Both are measured in percentage points.

The monitor displays the latest observation per cell, with period and change
window. The screener uses one selected observation period for all countries.
Absent observations remain blank. Country comparisons use user-selected peers;
chart lines break at missing scheduled periods.

## Files and vintages

`citysignal derive` produces:

- `economy-panel.csv.gz`: latest captured revision per metric, geography and period.
- `economy-vintages.csv.gz`: append-only captured revisions.
- `economy-manifest.json`: schema, definitions, counts, attribution and checksums.
- `economy-overview.json`: country metadata and latest readings.
- `countries/*.json.gz`: complete country histories, loaded on demand.
- `screener.json.gz`: recent observation cross-sections and period-specific changes.
- `research.json`: forecast experiments and derived features.

Storage uses deterministic gzip. Catalog downloads are plain CSV; published
SHA-256 checksums describe those uncompressed files. Dashboard CSV exports
contain displayed transformations; the full observation panel preserves source
units. Computed spreads retain their raw input references in the dictionary.

`observation_end` describes the measurement period. `published_at` records a
publisher release date when supplied. `fetched_at` is the first capture date
of a revision. Historical backfills become available on their capture date.
Date-only captures support daily replay:

```sh
python scripts/export_as_of.py --as-of 2026-09-26 --output /tmp/citysignal-as-of.csv
```

## Research specification

Three fixed specifications predict a Spanish official activity series two
months ahead. Expanding-window OLS uses at least 36 training observations.
Training target months end before the test input month. The augmented model
uses current target growth and the alternative signal; its matched baseline
uses current target growth alone. Persistence error is reported separately.
At least 12 evaluation months are required, and negative results are retained.

These experiments use revised observation history. Historical publication lags,
trading costs and asset returns are outside the specification. Captured-vintage
replay is available separately from the dates collection began.

## Collection and deployment

The existing pipeline refreshes weekly and retains source status and failed
requests in the data-quality report. Collection frequency and observation
frequency are distinct. Main-branch code changes run tests and a static build,
then deploy to GitHub Pages. Weekly refreshes use the same test gate before
committing snapshots and publishing. Build scripts invalidate data-loader
caches; restart a local preview after regenerating data.

## Additional dataset work

- TED procurement: award value, amendments, cancellations, buyer and regional
  matching. [API](https://docs.ted.europa.eu/api/latest/search.html).
- NASA Black Marble: industrial-site radiance with cloud, lunar and snow
  corrections. [Dataset](https://blackmarble.gsfc.nasa.gov/).
- Indeed occupations: fixed-membership sector diffusion and hiring spreads.
- BOE / BORME: national entity-event coverage, filing revisions and sector matching.

These datasets are listed under dataset development, separately from connected
coverage. Source-specific redistribution terms remain in the data catalog.

# Google Trends without an API

The official [Trends API](https://developers.google.com/search/apis/trends) is in
alpha and application-gated. The unofficial endpoints that scraping libraries use
are not a substitute for a scheduled build: `trends.google.com/trends/api/explore`
returns **HTTP 429 on a first request from a clean address**, and a shared CI
runner does worse. A public site whose search layer silently stops updating is
worse than one that admits it has no search layer.

So this is the manual path. It is allowed, free, reproducible, and it never
breaks the weekly build. Export a basket by hand, drop the CSV here, and the
`trends_manual` adapter picks it up on the next run.

## Exporting a basket

1. Open [trends.google.com/trends/explore](https://trends.google.com/trends/explore).
2. Set **region** to Spain (or the city's region), **period** to *2004–present* or
   *Past 5 years*, and enter the basket's terms — **all of them in one query**, using
   the "+ Compare" button.
3. Download the *Interest over time* CSV.
4. Rename it `<metric_id>__<geo_id>.csv` and save it in this directory.

## One export or several? Both, for different jobs

Measured against the live API, July 2026:

| | Grouped export (terms together) | Single-term export |
|---|---|---|
| Shared scale | yes — ratios are valid | no — ratios are meaningless |
| Largest term in the basket | full resolution | **identical**, byte for byte |
| Smaller terms | compressed toward zero | full resolution |

The dominant term is scaled to 100 either way, so exporting it alone gains
nothing. The compression only bites the *minor* terms: in a grouped
Madrid basket, "alquiler habitacion madrid" returned several zero months;
queried on its own it returned none.

That gives a clean division of labour. **Ratios must come from a grouped
export**, because only terms measured on one scale can be divided. **Levels of a
minor term are better from a single-term export**, because that is where the
resolution is lost.

It is not a cure for everything. Queried entirely on their own, "alquiler
habitacion palma", "alquiler habitacion zaragoza" and "alquiler habitacion
bilbao" still came back as zero in 28 to 36 months out of 60. Those terms are
genuinely not searched enough in Spain to measure — the zeros are the finding,
not an artifact, and the coverage rule below drops them rather than charting
them.

## Why terms that are divided must be in one export

Trends rescales its 0–100 index **per request**. Two separately downloaded charts
do not share a scale, so their numbers cannot be added, averaged or compared.
Terms exported together do share a scale, which is why the adapter averages the
columns of a single file and never combines across files. Each file is its own
series, compared only with itself over time.

## File naming

`<metric_id>__<geo_id>.csv`, for example:

| Filename | What to export |
|---|---|
| `search_rental_pressure__mun-28079.csv` | `alquiler piso Madrid` + `alquiler habitación Madrid` + `pisos alquiler Madrid` |
| `search_buy_momentum__mun-28079.csv` | `comprar piso Madrid` + `pisos en venta Madrid` + `hipoteca Madrid` |
| `search_relocation__mun-28079.csv` | `mudarse a Madrid` + `vivir en Madrid` + `empadronamiento Madrid` |
| `search_travel_intent__mun-29067.csv` | `vuelos Málaga` + `hoteles Málaga` + `qué ver en Málaga` |
| `search_commercial_distress__mun-28079.csv` | `traspaso local Madrid` + `se traspasa Madrid` |
| `search_hardship__es.csv` | `paro` + `subsidio desempleo` + `desahucio` + `dación en pago` |

Valid `geo_id` values are the ones the pipeline already uses: `mun-28079`
(Madrid), `mun-08019` (Barcelona), `mun-46250` (València), `mun-29067` (Málaga),
`mun-41091` (Sevilla), `mun-07040` (Palma), `mun-48020` (Bilbao), `mun-50297`
(Zaragoza), or `es` for the whole country.

## What these baskets are for

The interesting readings are **ratios between terms exported together**, because
the per-request rescaling cancels out:

- `alquiler habitación` ÷ `alquiler piso` — households compressing into rooms
- `estafa alquiler` ÷ `alquiler piso` — rental scams scale with scarcity
- `traspaso local` ÷ `alquiler local` — businesses exiting rather than expanding
- a hardship basket ÷ a discretionary basket — the cleanest boom-to-bust flip

## Honesty rules the adapter enforces

- The export date is recorded as `published_at`, and a basket nobody has
  refreshed goes stale on the Sources page like any other source.
- The source is labelled **commercial**, not official, everywhere it appears.
- Nothing here is redistributed: `redistribute: false` in `config/sources.yml`.
- Search interest is **attention**, never demand. Whether a basket leads anything
  observable is tested on the Signals page and reported either way.

## If API access arrives

Apply for the alpha with a public-interest use case. When credentials exist,
`trends.py` replaces this adapter behind the same metric ids, and the only thing
that changes is that the series become consistently scaled and update themselves.

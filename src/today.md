---
title: Today
toc: false
---

```js
import {ribbon, regimeLegend} from "./components/vitals.js";
import {el, cityColor, formatPeriod, formatValue, formatDelta, deltaClass, deltaArrow, regime} from "./components/theme.js";

const overview = await FileAttachment("data/overview.json").json();
const manifest = await FileAttachment("data/manifest.json").json();
```

<span class="cs-kicker">Eight Spanish cities · rebuilt weekly from official statistics</span>

# What is changing

<div class="cs-lede">

Housing demand, economic stress, tourism and supply across Madrid, Barcelona,
València, Málaga, Sevilla, Palma, Bilbao and Zaragoza. Every figure carries the
geography it was actually published at, the period it describes, and the date we
last asked its publisher for it. Nothing here forecasts a crash — it shows what
the evidence currently says, including where the evidence disagrees with itself.

</div>

```js
const headline = overview.headline;

display(
  headline
    ? el("div", {class: "cs-headline"}, [
        el("div", {class: "cs-kicker", text: "Largest verified change this build"}),
        el("div", {class: "cs-headline-text"}, [
          document.createTextNode(headline.lead + " "),
          el("span", {class: "cs-headline-figure cs-num", text: headline.figure}),
          document.createTextNode(" " + headline.tail)
        ]),
        el("div", {class: "cs-meta"}, [
          el("span", {class: `cs-scope${headline.geo_level === "municipality" ? "" : " cs-scope-wider"}`, text: headline.scope_label}),
          el("span", {text: `${formatPeriod(headline.period)} · ${headline.label} · ${formatValue(headline.value, headline.unit)}`}),
          el("a", {href: `./cities/${headline.city}`, text: `${headline.city_name} →`})
        ])
      ])
    : null
);
```

## Vital signs

<div class="cs-note" style="margin-bottom:.4rem">

One row per city. The band is the regime the published rules assigned at that
moment; the line is demand momentum measured against that city's own history.
Hover any row for the value at a point in time.

</div>

```js
display(regimeLegend());
```

```js
// `width` is Observable's reactive measurement of the content column, so the
// ribbons re-fit when the window changes instead of guessing once at load.
const ribbonWidth = Math.max(240, width - 300);

display(
  el("div", {class: "cs-panel"}, [
    el("div", {class: "cs-vitals"},
      overview.cities.flatMap((city) => [
        el("div", {class: "cs-vitals-city"}, [
          el("a", {href: `./cities/${city.slug}`, text: city.name})
        ]),
        ribbon(city, {width: ribbonWidth, height: 56}),
        el("div", {}, [
          el("span", {class: `cs-regime regime-${city.regime.rule_id}`}, [
            el("span", {class: "cs-glyph", text: regime(city.regime.rule_id).glyph}),
            document.createTextNode(city.regime.label)
          ])
        ])
      ])
    )
  ])
);
```

```js
const unclassified = overview.cities.filter((c) => c.regime.rule_id === "neutral" || !c.regime.confident);
display(
  unclassified.length
    ? el("div", {
        class: "cs-note",
        style: "margin-top:.8rem",
        text:
          unclassified.length === overview.cities.length
            ? "No city is classified yet: the regime rules need at least three of the four sub-indices, and not enough sources are reporting. This is the honest state of the data, not a rendering failure — the Sources page shows which adapters are still to land."
            : `${unclassified.map((c) => c.name).join(", ")} ${unclassified.length === 1 ? "is" : "are"} shown without a confident regime because fewer than three sub-indices currently have data.`
      })
    : null
);
```

## Largest moves

<div class="cs-note" style="margin-bottom:.6rem">

Biggest year-over-year changes across every city and measure. Deliberately not
coloured good-or-bad — whether a rise is welcome depends on whether you are the
landlord or the tenant, and that is not this site's call to make. Releases that
look provisional are excluded.

</div>

```js
const movers = overview.movers ?? [];

display(
  movers.length
    ? el("table", {class: "cs-table"}, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", {text: "City"}),
            el("th", {text: "Measure"}),
            el("th", {text: "Latest"}),
            el("th", {text: "Year on year"}),
            el("th", {text: "Period"}),
            el("th", {text: "Scope"})
          ])
        ]),
        el("tbody", {},
          movers.map((m) =>
            el("tr", {}, [
              el("td", {}, [
                el("span", {
                  style: `display:inline-block;width:8px;height:8px;border-radius:1px;background:${cityColor(m.city)};margin-right:.45rem`
                }),
                el("a", {href: `./cities/${m.city}`, text: m.city_name})
              ]),
              el("td", {text: m.label}),
              el("td", {class: "cs-num", text: formatValue(m.value, m.unit)}),
              el("td", {class: `cs-num ${deltaClass()}`, text: `${deltaArrow(m.yoy)} ${formatDelta(m.yoy)}`}),
              el("td", {class: "cs-num", text: formatPeriod(m.period)}),
              el("td", {}, [el("span", {class: `cs-scope${m.geo_level === "municipality" ? "" : " cs-scope-wider"}`, text: m.scope_label})])
            ])
          )
        )
      ])
    : el("div", {class: "cs-empty", text: "No year-over-year comparisons available yet — a measure needs at least two years of history before it can move."})
);
```

## Needs attention

```js
const attention = overview.attention ?? [];

display(
  attention.length
    ? el("div", {class: "cs-grid"},
        attention.map((source) =>
          el("div", {class: "cs-panel"}, [
            el("div", {class: "cs-card-head"}, [
              el("span", {class: "cs-card-label", text: source.source_id}),
              el("span", {class: `cs-freshness ${source.status === "failed" ? "cs-failing" : "cs-stale"}`, text: source.status === "failed" ? "failing" : "stale"})
            ]),
            el("div", {class: "cs-note", style: "margin-top:.5rem", text:
              source.last_error
                ? source.last_error
                : `Latest observation ${formatPeriod(source.latest_observation) ?? "unknown"}, ${source.staleness_days ?? "?"} days old.`})
          ])
        )
      )
    : el("div", {class: "cs-empty", text: "Every source reported on schedule at the last run."})
);
```

<div class="cs-note" style="margin-top:2.5rem">

Built <span class="cs-num">${manifest.generated_at?.slice(0, 10)}</span> from
<span class="cs-num">${manifest.counts?.observations?.toLocaleString("en-GB")}</span> observations
across <span class="cs-num">${manifest.counts?.series}</span> series.
Rules in force: ${Object.values(manifest.rules ?? {}).filter(Boolean).join(" · ")}.

</div>

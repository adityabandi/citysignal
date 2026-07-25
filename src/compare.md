---
title: Compare
toc: false
---

```js
import {indexTrace, regimeLegend} from "./components/vitals.js";
import {el, cityColor, formatPeriod, formatValue, formatDelta, deltaClass, regime} from "./components/theme.js";

const overview = await FileAttachment("data/overview.json").json();
const cities = await FileAttachment("data/cities.json").json();
```

<span class="cs-kicker">Eight cities, one scale</span>

# Compare

<div class="cs-lede">

Every measure below is standardised against each city's own history, so a value
of +1 means "high for this city" rather than "high in absolute terms". That is
the only way Palma and Madrid belong on the same axis: their levels are not
comparable, but their departures from their own normal are.

</div>

```js
const indexIds = Object.keys(Object.values(cities)[0]?.indices ?? {});
const chosen = view(
  Inputs.select(indexIds, {
    label: "Index",
    value: indexIds[0],
    format: (id) => Object.values(cities)[0].indices[id].label
  })
);
```

```js
const spec = Object.values(cities)[0].indices[chosen];
display(el("div", {class: "cs-note", style: "margin:.2rem 0 1rem", text: spec.question}));
display(el("div", {class: "cs-grid"}, indexTrace(overview.cities, chosen)));
```

## Current standing

```js
const rows = overview.cities
  .map((c) => ({...c, value: c.indices[chosen]}))
  .sort((a, b) => (b.value ?? -Infinity) - (a.value ?? -Infinity));

display(
  el("table", {class: "cs-table"}, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", {text: "City"}),
        el("th", {text: spec.label}),
        el("th", {text: "Regime"}),
        el("th", {text: "Closest archetype"})
      ])
    ]),
    el("tbody", {},
      rows.map((row) =>
        el("tr", {}, [
          el("td", {}, [
            el("span", {style: `display:inline-block;width:8px;height:8px;border-radius:1px;background:${cityColor(row.slug)};margin-right:.45rem`}),
            el("a", {href: `./cities/${row.slug}`, text: row.name})
          ]),
          el("td", {class: "cs-num", text: row.value == null ? "insufficient data" : `${row.value > 0 ? "+" : ""}${row.value.toFixed(2)} σ`}),
          el("td", {}, [
            el("span", {class: `cs-regime regime-${row.regime.rule_id}`, style: "font-size:.62rem;padding:.18rem .4rem"}, [
              el("span", {class: "cs-glyph", text: regime(row.regime.rule_id).glyph}),
              document.createTextNode(row.regime.label)
            ])
          ]),
          el("td", {class: "cs-note", text:
            row.top_signature && !row.top_signature.insufficient_coverage
              ? `${row.top_signature.label} — ${row.top_signature.firing} of ${row.top_signature.available} signals`
              : "insufficient coverage"})
        ])
      )
    )
  ])
);
```

## Regime history

```js
display(regimeLegend());
```

```js
import {ribbon} from "./components/vitals.js";
const ribbonWidth = Math.max(240, width - 300);

display(
  el("div", {class: "cs-panel"}, [
    el("div", {class: "cs-vitals"},
      overview.cities.flatMap((city) => [
        el("div", {class: "cs-vitals-city"}, [el("a", {href: `./cities/${city.slug}`, text: city.name})]),
        ribbon(city, {width: ribbonWidth, height: 56, index: chosen}),
        el("div", {class: "cs-num", style: "font-size:.72rem;color:var(--cs-ink-3)", text:
          city.timeline?.length ? formatPeriod(city.timeline.at(-1).period) : "—"})
      ])
    )
  ])
);
```

<div class="cs-note" style="margin-top:1.4rem">

Standardisation needs history: a measure is only scored once it has at least 24
prior observations, and the current value is held out of its own mean and
standard deviation so a single extreme reading cannot flatten the score meant to
reveal it. Cities missing a component are not given a substitute from a
neighbouring geography.

</div>

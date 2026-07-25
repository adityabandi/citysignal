---
title: Madrid districts
toc: false
---

```js
import {madridMap, mapLegend} from "./components/madridMap.js";
import {el, formatPeriod, formatValue, deltaArrow, formatDelta} from "./components/theme.js";

const cities = await FileAttachment("data/cities.json").json();
const madrid = cities.madrid;
const districts = madrid.districts ?? {metrics: {}, codes: []};
const available = Object.entries(districts.metrics ?? {});
```

<span class="cs-kicker">Madrid · 21 districts</span>

# Inside the city

<div class="cs-lede">

Madrid is the only one of the eight cities that publishes enough at district
level to map. The others get a city-level page and nothing invented to fill the
gap — a coarse map is honest, an interpolated one is not.

</div>

```js
// `display(null)` prints a literal null, so only render the notice when it applies.
if (!available.length) {
  display(el("div", {class: "cs-empty", text:
    "No district-level series are reporting yet. This page fills itself in as the " +
    "Madrid adapters land — monthly padrón by district, the business-premises " +
    "census, and licensed tourist dwellings."}));
}
```

```js
const chosen = available.length
  ? view(Inputs.select(available.map(([id]) => id), {
      label: "Measure",
      value: available[0][0],
      format: (id) => districts.metrics[id].label
    }))
  : null;
```

```js
const geo = available.length ? await FileAttachment("data/madrid-districts.geojson").json() : null;
```

```js
if (available.length) {
  const metric = districts.metrics[chosen];
  const values = Object.values(metric.districts).map((d) => d.latest.value);

  display(el("div", {}, [
    metric.plain ? el("div", {class: "cs-plain", style: "margin-bottom:.7rem", text: metric.plain}) : null,
    madridMap(geo, metric),
    mapLegend(metric, values)
  ]));
}
```

```js
if (available.length) {
  const metric = districts.metrics[chosen];
  const rows = Object.entries(metric.districts)
    .map(([code, d]) => ({
      code,
      name: geo.features.find((f) => f.properties.district_code === code)?.properties.name ?? code,
      ...d
    }))
    .sort((a, b) => b.latest.value - a.latest.value);

  display(el("table", {class: "cs-table", style: "margin-top:1.6rem"}, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", {text: "District"}),
        el("th", {text: metric.label}),
        el("th", {text: "Year on year"}),
        el("th", {text: "Period"})
      ])
    ]),
    el("tbody", {},
      rows.map((row) =>
        el("tr", {}, [
          el("td", {text: row.name}),
          el("td", {class: "cs-num", text: formatValue(row.latest.value, metric.unit)}),
          el("td", {class: "cs-num cs-delta-neutral", text:
            row.yoy == null ? "—" : `${deltaArrow(row.yoy)} ${formatDelta(row.yoy)}`}),
          el("td", {class: "cs-num", text: formatPeriod(row.latest.period)})
        ])
      )
    )
  ]));
}
```

## Why only Madrid

<div class="cs-note">

Sub-city evidence exists only where a municipality chooses to publish it.
Madrid's open-data portal carries monthly population by district and barrio, a
census of every business premises with its activity code, and the register of
licensed tourist dwellings. Barcelona publishes a comparable business census and
could follow; most of the others publish nothing at this resolution.

The business-premises census is a live snapshot with no history — it tells you
today's state and nothing about last month. History for it is therefore built
here rather than downloaded: a base snapshot, then a small monthly diff, with a
hash chain so any past month can be reconstructed and checked. That is also why
openings and closures only appear from the second month onward. There is no
honest way to produce a change from a single observation.

</div>

---
title: CitySignal
toc: false
---

```js
import {tapeStrip, signalCard, compositeHeadline, describeComposite, DESK} from "./components/desk.js";
import {el, formatPeriod, formatValue, cityColor} from "./components/theme.js";

const national = await FileAttachment("data/national.json").json();
```

<span class="cs-kicker">Spain · the market, and the eight cities inside it</span>

# The market, and then the street

<div class="cs-lede">

Rates, credit and national hiring set the weather for every Spanish city at once,
so they come first. Underneath, eight cities scored against their own histories —
and the side-door measures that were never built for property analysis: shop mix
reconstructed from OpenStreetMap's edit history, attention by Wikipedia language
edition, what people search before they move or before they lose a home.

</div>

## The national tape

<div class="cs-note" style="margin-bottom:.7rem">

Real units. These are euro-area and Spain-wide — nobody's local market escapes them.

</div>

```js
display(national.tape.length
  ? tapeStrip(national.tape)
  : el("div", {class: "cs-empty", text: "No national tape series reporting."}));
```

```js
display(compositeHeadline(
  {...national, name: "Spain"},
  describeComposite({...national, name: "Spain"})
));
```

## National signals

```js
display(national.signals.length
  ? el("div", {class: "cs-signal-grid"}, national.signals.map((s) => signalCard(s)))
  : el("div", {class: "cs-empty", text: "No national signals with enough history yet."}));
```

## The eight cities

<div class="cs-note" style="margin-bottom:.9rem">

Each city's composite is scored against <em>its own</em> history, never against
the others — Palma and Madrid share no scale, but each can be unusual for itself.
A higher number here does not mean a hotter city than its neighbour; it means a
city further from its own normal. Click through for the full desk.

</div>

```js
function cityTile(c) {
  const value = c.composite;
  const hue = cityColor(c.slug);
  const tone = value == null ? DESK.flat : value >= 60 ? DESK.up : value <= 40 ? DESK.down : DESK.flat;
  return el("a", {
    class: "cs-city-tile",
    href: `./cities/${c.slug}`,
    style: `--signal:${tone};--city:${hue}`
  }, [
    el("div", {class: "cs-city-tile-head"}, [
      el("span", {class: "cs-city-dot"}),
      el("span", {class: "cs-city-tile-name", text: c.name})
    ]),
    el("div", {class: "cs-city-tile-figure"}, [
      el("span", {class: "cs-num cs-city-tile-index", text: String(value ?? "—")}),
      el("span", {class: "cs-signal-outof", text: "/100"})
    ]),
    el("div", {class: "cs-gauge"}, [
      el("div", {class: "cs-gauge-fill", style: `width:${Math.max(1, value ?? 0)}%;background:${tone}`}),
      el("div", {class: "cs-gauge-mid"})
    ]),
    el("div", {class: "cs-city-tile-top"},
      (c.top ?? []).map((s) =>
        el("span", {
          class: "cs-city-tile-sig",
          title: s.plain ?? "",
          text: `${s.ticker} ${s.index}`
        })
      )
    ),
    el("div", {class: "cs-city-tile-n", text: `${c.signals} signals`})
  ]);
}

display(el("div", {class: "cs-city-grid"}, national.cities.map(cityTile)));
```

<div class="cs-note" style="margin-top:1.6rem">

**What the scores mean.** Each signal is a percentile against that city's own
record: 63 means higher than 63% of everything it has ever posted for that
measure, and 50 is exactly typical. Scores are oriented so higher always reads as
more expansionary — a rising eviction-search count pushes a city *down*, not up.
Higher means busier, not better; whether that is welcome depends on whether you
are buying or selling.

Madrid carries far more signals than the others because it is the only one of the
eight publishing enough at district level to support them. That is a fact about
Spanish municipal open data, not about Madrid.

</div>

```js
display(
  el("div", {class: "cs-meta", style: "border-top:none;margin-top:1.2rem"}, [
    el("a", {href: "./today", text: "what changed this week →"}),
    el("a", {href: "./forecast", text: "what we think happens next →"}),
    el("a", {href: "./track-record", text: "how those calls scored →"}),
    el("a", {href: "./methodology", text: "method →"})
  ])
);
```

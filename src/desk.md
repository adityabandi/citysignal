---
title: Desk
toc: false
---

```js
import {tapeStrip, signalCard, compositeHeadline, describeComposite} from "./components/desk.js";
import {el, formatPeriod, formatValue, cityColor} from "./components/theme.js";

const cities = await FileAttachment("data/cities.json").json();
```

```js
const citySlug = view(Inputs.select(Object.keys(cities), {
  label: "City",
  value: "madrid",
  format: (slug) => cities[slug].name
}));
```

```js
const city = cities[citySlug];
const desk = city.desk;
```

<span class="cs-kicker">The desk · official tape and side-door signals</span>

# What the city is doing when nobody is measuring it

<div class="cs-lede">

Everyone watches rents, transactions and unemployment. They arrive late, revised,
and after the fact. This page keeps those on the tape in their real units, and
puts beside them the measures that were never built for property analysis —
restaurant density reconstructed from OpenStreetMap's edit history, attention by
Wikipedia language edition, what people search when they are about to move or
about to lose a home.

</div>

## The official tape

<div class="cs-note" style="margin-bottom:.7rem">

Real units, because a mortgage rate means something as 2.85% and nothing as a
score. Each carries the geography it was actually published at.

</div>

```js
display(desk.tape.length ? tapeStrip(desk.tape) : el("div", {class: "cs-empty", text: "No tape series reporting for this city yet."}));
```

## The composite

```js
display(compositeHeadline(desk, describeComposite(desk)));
```

## Unconventional signals

<div class="cs-note" style="margin-bottom:.9rem">

Each is a <strong>percentile against this city's own history</strong>: 63 means
higher than 63% of everything this city has ever recorded for that measure, and
50 is exactly typical. That is the same underlying number as the sigma values
elsewhere on the site, in a form you can act on. The colour reads the signal in
the direction it actually means — a rising vacancy rate is contractionary even
though the number went up.

</div>

```js
display(
  desk.signals.length
    ? el("div", {class: "cs-signal-grid"}, desk.signals.map((s) => signalCard(s)))
    : el("div", {class: "cs-empty", text:
        "No unconventional signals have enough history for this city yet. They need at least two years before a percentile means anything."})
);
```

## Where these come from, and what they cannot tell you

<div class="cs-note">

**OpenStreetMap food density** counts restaurants, cafés and bars inside each
district boundary from OSM's full edit history. It is published as a *share* of
all mapped points, because the raw count also measures how busy volunteer mappers
were — Madrid Centro's restaurant count nearly quadrupled between 2014 and 2018
without 800 restaurants opening.

**Attention by language edition** reads the same city article across German,
French, Italian, Portuguese and English Wikipedia, normalised against each
edition's own total traffic. Without that normalisation the numbers mostly track
Wikipedia shrinking rather than interest moving.

**Search spreads** are ratios between terms queried together, never raw levels.
Google rescales every request, so a level can fall because Spain searched less
overall; a ratio between two terms on one scale cannot.

None of these are demand. They are attention, physical presence, and paperwork —
things that correlate with demand and sometimes lead it. Whether any of them
actually leads anything is tested on the <a href="./signals">Signals</a> page and
reported either way, including when the answer is no.

</div>

```js
display(
  el("div", {class: "cs-meta", style: "border-top:none;margin-top:1.4rem"}, [
    el("span", {class: "cs-kind", text: `${desk.signals.length} signals · ${desk.tape.length} tape series`}),
    el("a", {href: `./cities/${citySlug}`, text: `${desk.name} in full →`}),
    el("a", {href: "./methodology", text: "how the scoring works →"})
  ])
);
```

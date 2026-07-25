---
toc: true
---

```js
import {cardGrid} from "../components/metricCard.js";
import {fingerprint, signatureSummary} from "../components/fingerprint.js";
import {el, cityColor, formatPeriod, regime} from "../components/theme.js";

const all = await FileAttachment("../data/cities.json").json();
const city = all[observable.params.city];
const hue = cityColor(city.slug);
```

<span class="cs-kicker">${city.province} · ${city.ccaa}</span>

# ${city.name}

```js
display(
  el("div", {style: "display:flex;flex-wrap:wrap;gap:.7rem;align-items:center;margin:.6rem 0 1.2rem"}, [
    el("span", {class: `cs-regime regime-${city.regime.rule_id}`}, [
      el("span", {class: "cs-glyph", text: regime(city.regime.rule_id).glyph}),
      document.createTextNode(city.regime.label)
    ]),
    el("span", {class: "cs-kind", text: `rule ${city.regime.rule_id} · ${city.regime.rules_version}`}),
    city.regime.period ? el("span", {class: "cs-kind", text: `as of ${formatPeriod(city.regime.period)}`}) : null
  ])
);
```

<div class="cs-lede">${city.regime.reading}</div>

```js
display(
  el("div", {class: "cs-note", style: "margin-top:.8rem"}, [
    document.createTextNode(
      city.regime.confident
        ? `This classification fired because \`${city.regime.expression}\` held. `
        : `Fewer than three of the four sub-indices have data for ${city.name}, so this classification is provisional. `
    ),
    document.createTextNode(
      "Every rule and threshold is published on the Method page, and the same rules are replayed over history to draw the timeline below."
    )
  ])
);
```

## The four sub-indices

<div class="cs-note" style="margin-bottom:.6rem">

Each index is a weighted mean of measures standardised against this city's own
record, oriented so positive always means hotter. An index with too few
components available reports insufficient data instead of guessing.

</div>

```js
display(
  el("div", {class: "cs-grid"},
    Object.values(city.indices).map((index) =>
      el("div", {class: "cs-panel"}, [
        el("div", {class: "cs-card-head"}, [
          el("span", {class: "cs-card-label", text: index.label}),
          el("span", {class: "cs-kind", text: `${index.components.length}/${index.components.length + index.missing.length} inputs`})
        ]),
        el("div", {class: "cs-card-value cs-num", text: index.value == null ? "—" : `${index.value > 0 ? "+" : ""}${index.value.toFixed(2)} σ`}),
        el("div", {class: "cs-note", style: "margin-top:.3rem", text: index.insufficient ? "Not enough components have data to compute this index." : index.question}),
        index.components.length
          ? el("div", {class: "cs-meta"},
              index.components
                .slice()
                .sort((a, b) => Math.abs(b.oriented) - Math.abs(a.oriented))
                .slice(0, 4)
                .map((c) =>
                  el("span", {
                    title: `${c.label}: ${c.oriented > 0 ? "+" : ""}${c.oriented.toFixed(2)} σ (${c.transform}, ${c.geo_level}, ${formatPeriod(c.period)})`,
                    text: `${c.label} ${c.oriented > 0 ? "+" : ""}${c.oriented.toFixed(1)}`
                  })
                )
            )
          : null
      ])
    )
  )
);
```

## Signature

<div class="cs-note" style="margin-bottom:.8rem">

Each archetype is a list of signals declared in advance, with the direction each
should move if that pattern is what is happening. The solid shape is this city
now; the dashed outline is what the archetype expects. Where they diverge is the
interesting part — a city can look like a boom on tourism and like distress on
employment at the same time, and this is where you would see that.

</div>

```js
const shown = city.signatures.filter((s) => s.available >= 3).slice(0, 3);

display(
  shown.length
    ? el("div", {class: "cs-grid", style: "grid-template-columns:repeat(auto-fill,minmax(330px,1fr))"},
        shown.map((signature) =>
          el("div", {class: "cs-panel"}, [
            signatureSummary(signature),
            el("div", {style: "display:flex;justify-content:center;margin-top:.6rem"}, [
              fingerprint(signature, {color: hue})
            ])
          ])
        )
      )
    : el("div", {class: "cs-empty", text: "Too few signals have data for this city to draw an archetype shape yet."})
);
```

## Evidence

```js
const SECTION_TITLES = {
  housing: "Housing",
  people: "People",
  work: "Work",
  tourism: "Tourism",
  str: "Short-term rentals",
  supply: "Supply",
  credit: "Credit",
  distress: "Distress",
  attention: "Attention",
  other: "Other"
};

for (const [key, title] of Object.entries(SECTION_TITLES)) {
  const cards = city.sections[key];
  if (!cards?.length) continue;
  display(el("h3", {text: title, style: "margin-top:2rem"}));
  display(cardGrid(cards, {color: hue}));
}
```

```js
const empty = Object.keys(SECTION_TITLES).filter((k) => !city.sections[k]?.length);
display(
  empty.length
    ? el("div", {class: "cs-note", style: "margin-top:1.4rem", text:
        `No data yet for: ${empty.map((k) => SECTION_TITLES[k].toLowerCase()).join(", ")}. ` +
        `These sections appear on their own once the relevant adapter reports.`})
    : null
);
```

## Geography on this page

<div class="cs-note">

Madrid the municipality, Madrid the province and the Community of Madrid are
three different places, and mixing them is the most common way a city dashboard
misleads. Every card above states the level its number was published at. For
${city.name} those are: municipality <span class="cs-num">${city.geo.municipality}</span>,
province <span class="cs-num">${city.geo.province}</span>,
autonomous community <span class="cs-num">${city.geo.ccaa}</span>${city.geo.airports.length ? `, airport <span class="cs-num">${city.geo.airports.join(", ")}</span>` : ""}${city.geo.ports.length ? `, port <span class="cs-num">${city.geo.ports.join(", ")}</span>` : ""}.

</div>

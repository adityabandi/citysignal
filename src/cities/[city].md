---
toc: true
---

```js
import {cardGrid, sparkline} from "../components/metricCard.js";
import {fingerprint, signatureSummary} from "../components/fingerprint.js";
import {el, cityColor, formatPeriod, regime, describeIndex, shortZ, describeZ} from "../components/theme.js";

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

```js
display(
  el("div", {class: `cs-situation regime-${city.regime.rule_id}`}, [
    el("div", {class: "cs-situation-text", text: city.regime.reading})
  ])
);
```

```js
display(
  el("div", {class: "cs-note"}, [
    document.createTextNode(
      city.regime.confident
        ? `This label was produced by a published rule, not a judgement call: it fired because \`${city.regime.expression}\` held. `
        : `Fewer than three of the four indices have data for ${city.name}, so this label is provisional. `
    ),
    document.createTextNode(
      "Every rule and threshold is on the Method page, and the same rules are replayed over history to draw the timeline."
    )
  ])
);
```

## The four questions

<div class="cs-note" style="margin-bottom:.9rem">

Every measure below is compared against <em>this city's own past</em>, not against
other cities — Palma and Madrid share no scale, but each can be unusual for
itself. "Clearly higher" means roughly one to two standard deviations above what
this city normally does.

</div>

```js
display(
  el("div", {class: "cs-grid"},
    Object.values(city.indices).map((index) =>
      el("div", {class: "cs-panel"}, [
        el("div", {class: "cs-card-head"}, [
          el("span", {class: "cs-card-label", text: index.label}),
          el("span", {class: "cs-kind", text: `${index.components.length} of ${index.components.length + index.missing.length} inputs`})
        ]),
        el("div", {class: "cs-index-verdict", text: describeIndex(index.index_id, index.value)}),
        el("div", {class: "cs-index-figure"}, [
          el("span", {class: "cs-num", text: index.value == null ? "—" : `${index.value > 0 ? "+" : ""}${index.value.toFixed(2)}`}),
          el("span", {class: "cs-card-unit", text: index.value == null ? "no reading" : "standard deviations from this city's normal"})
        ]),
        index.components.length
          ? el("div", {class: "cs-meta"},
              index.components
                .slice()
                .sort((a, b) => Math.abs(b.oriented) - Math.abs(a.oriented))
                .slice(0, 4)
                .map((c) =>
                  el("span", {
                    title: `${c.label} — ${describeZ(c.oriented, {noun: "It"})} (${c.transform}, ${c.geo_level}, ${formatPeriod(c.period)})`,
                    text: `${c.label}: ${shortZ(c.oriented)}`
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

## What people are searching

<div class="cs-note" style="margin-bottom:.9rem">

Search interest is <em>attention</em>, not demand — and its raw level is not
comparable across years, because Google rescales it for every query. What <em>is</em>
comparable is the ratio between terms that were searched for together, which is
what these two spreads are. Whether they lead anything real is tested on the
<a href="../signals">Signals</a> page, and reported either way.

</div>

```js
const SPREADS = {
  search_room_share: {
    title: "Are people looking for rooms instead of flats?",
    up: "More people are searching for a room rather than a whole flat than a year ago — households compressing.",
    down: "Fewer people are searching for a room rather than a whole flat than a year ago."
  },
  search_tenure_switch: {
    title: "Are people looking to buy instead of rent?",
    up: "Attention has shifted further toward buying and away from renting over the past year.",
    down: "Attention has shifted back toward renting and away from buying over the past year."
  },
  search_rental_pressure: {
    title: "How hard are people looking for somewhere to rent?",
    up: "More rental searching than a year ago.",
    down: "Less rental searching than a year ago."
  },
  search_buy_momentum: {
    title: "How hard are people looking to buy?",
    up: "More buyer searching than a year ago.",
    down: "Less buyer searching than a year ago."
  }
};

const searchCards = (city.sections.attention ?? []).filter((c) => c.metric_id in SPREADS);

display(
  searchCards.length
    ? el("div", {class: "cs-grid"},
        searchCards.map((card) => {
          const copy = SPREADS[card.metric_id];
          const change = card.latest.yoy;
          return el("div", {class: "cs-panel"}, [
            el("div", {class: "cs-kicker", text: copy.title}),
            el("div", {class: "cs-index-verdict", text: change == null ? "Not enough history yet to compare." : (change > 0 ? copy.up : copy.down)}),
            sparkline(card.series, {color: hue, width: 250, height: 44}),
            el("div", {class: "cs-meta"}, [
              el("span", {class: "cs-num", text: `${change > 0 ? "+" : ""}${change?.toFixed(1) ?? "—"}% year on year`}),
              el("span", {class: "cs-kind cs-kind-commercial", text: "search attention"}),
              el("span", {text: formatPeriod(card.latest.period)})
            ])
          ]);
        })
      )
    : el("div", {class: "cs-empty", text:
        "No search baskets for this city yet. Google reports low-volume terms as zero rather than as missing, and a series that is mostly zeros is dropped rather than published — which is why the smaller cities carry fewer of these."})
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

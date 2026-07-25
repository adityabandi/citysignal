---
title: Method
toc: true
---

```js
import {el} from "./components/theme.js";
const manifest = await FileAttachment("data/manifest.json").json();
const sources = await FileAttachment("data/sources.json").json();
```

<span class="cs-kicker">Rules in force: ${Object.values(manifest.rules ?? {}).filter(Boolean).join(" · ")}</span>

# Method

<div class="cs-lede">

CitySignal is a rule set, not a model. Nothing here is fitted, trained or
tuned against an outcome. Every classification on the site can be reproduced
from the published rules plus the committed data, and every rule file is frozen
once data has been published against it.

</div>

## What the site will not do

<div class="cs-note">

**It will not give you a crash probability.** A single percentage would look
authoritative and be indefensible. Instead there are four sub-indices you can
inspect and a regime label whose triggering rule is printed next to it.

**It will not silently change geography.** A provincial series shown on a city
page says "province" on the card. Where only provincial evidence exists, that is
what you get, labelled as such — never rebadged as the city's own number.

**It will not present attention as demand.** Pageviews and news volume are
labelled as attention and kept out of the housing and labour measures. Whether
they lead anything is tested on the Signals page and reported either way.

**It will not hide a broken source.** A failed fetch leaves the last-good value
in place with a stale badge and opens a tracked issue. The charts do not quietly
freeze.

</div>

## How a number becomes a chart

<div class="cs-note">

A weekly job fetches each source in isolation. Payloads are checked structurally
before parsing — Spanish government portals routinely return a cookie wall or
error page under a `text/csv` content type, and that must fail loudly rather than
produce an empty series. Records are validated against the metric registry
(unit, cadence, geographic level, plausible range) and appended to a
committed CSV per source and metric.

History is append-only. When a publisher restates a past value, the new value is
appended with an incremented revision and the old row stays; when a source that
is not supposed to revise does so anyway, the change is quarantined and flagged
rather than absorbed. The result is that `git log` is a genuine audit trail: any
figure the site ever showed can be recovered from the commit that produced it.

</div>

## Standardisation

<div class="cs-note">

Cities are not comparable in levels — Palma and Madrid share no scale. Each
measure is therefore scored against **its own history**: at least 24 prior
observations, with the current value excluded from its own mean and standard
deviation so one extreme reading cannot flatten the score meant to reveal it.
Scores are clipped at four standard deviations, because an outlier should read
as extreme rather than as infinite.

Scores are then oriented by the metric's declared direction so that an index has
a consistent sense: positive means more pressure, more activity, more heat.
"Hotter" here is a description of the market's temperature, not a verdict on it —
rising rents and rising unemployment both push their indices in the direction
that matches what they measure, and whether either is welcome depends entirely on
who is reading.

</div>

## The four sub-indices

<div class="cs-note">

**Demand momentum** — is underlying demand strengthening? **Housing pressure** —
is demand outrunning available housing? **Supply response** — is supply catching
up? **Distress** — is an orderly slowdown becoming financial stress?

Each is a weighted mean of the components that are actually available. Below the
declared minimum number of components an index reports insufficient data instead
of guessing, and the site shows that state rather than an empty chart.

</div>

## Regimes

<div class="cs-note">

An ordered rule list, first match wins, with a fallback that always matches.
Leaving Stress or Dislocation requires the exit condition to hold for two
consecutive periods — a false calm costs more than a false alarm — while
entering one does not.

Rule expressions are parsed, not evaluated: only comparisons, boolean operators,
numbers, named indices and two functions are permitted. Each published
classification records the rule that fired and the rules version, so replaying
history against the same file reproduces it exactly.

</div>

## Signatures

<div class="cs-note">

Each archetype — boom, overheating, orderly cooling, distress, tourism shock,
regulatory shock — is a list of signals and the direction each should move if
that pattern is what is happening, written down before the data is looked at.
A city's reading is simply how many of the **available** signals agree, with the
disagreeing ones named. Signals with no data are excluded from both sides and the
coverage is shown, because "3 of 4 firing" out of nineteen declared signals is a
much weaker statement than "14 of 19".

</div>

## The lead-lag lab

<div class="cs-note">

Candidate leading signals are declared in advance in a versioned config. The lag
is chosen on the early part of the series and scored on a held-out tail the
search never saw, then required to beat a seasonal-naive baseline. A pair that
correlates but cannot beat the baseline is reported as no evidence. Pairs are
never deleted for scoring badly.

</div>

## Sources in this build

```js
display(
  el("table", {class: "cs-table"}, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", {text: "Source"}),
        el("th", {text: "Kind"}),
        el("th", {text: "Level"}),
        el("th", {text: "Cadence"}),
        el("th", {text: "Licence"})
      ])
    ]),
    el("tbody", {},
      (sources.sources ?? []).map((s) =>
        el("tr", {}, [
          el("td", {text: s.publisher ?? s.source_id}),
          el("td", {}, [el("span", {class: `cs-kind cs-kind-${s.kind ?? "official"}`, text: s.kind ?? "official"})]),
          el("td", {text: s.geo_level ?? "—"}),
          el("td", {text: s.cadence ?? "—"}),
          el("td", {class: "cs-note", text: s.license ?? "—"})
        ])
      )
    )
  ])
);
```

## Known limitations

<div class="cs-note">

Sub-city evidence exists only where a municipality publishes it; most of these
cities do not, and a coarse map is more honest than an invented one.
Judicial distress measures are provincial and slow — they are the confirmation,
not the warning.

Google Trends is present, but by hand rather than by API. The official API is
alpha and application-gated, and the unofficial endpoints rate-limit on a first
request, which makes them unfit for a scheduled build. Baskets are therefore
exported manually and committed, and they go stale on the Sources page like any
other source when nobody refreshes them. Two properties of that data shape how it
is used: Trends rescales 0-100 for every query, so only terms exported *together*
share a scale and only ratios between them are comparable across years; and a
term with too little volume is reported as zero rather than as missing, so a
series that is mostly zeros is dropped rather than published. That is why the
smaller cities carry fewer search metrics than Madrid, and why they should.

</div>

## The site takes no view on whether prices should rise

<div class="cs-note">

A rise in rents is good news for a landlord and bad news for a tenant. This
project has no standing to decide which of those readers it is written for, so
it tries not to decide by accident either.

Three places where it previously did, and what changed:

**Year-on-year changes were coloured good-or-bad.** A change that moved the way
the metric registry calls "hotter" was rendered green — which told a renter that
rising rents were good news. Deltas are now shown in neutral ink with an arrow
for direction. Status colour is reserved for regime states and source health,
where it marks a defined condition rather than an opinion.

**The regime rules were asymmetric.** The first rule set could not classify any
city, so states were added — `Hot` and `Pressure without supply` — that let the
rules name what the first full dataset happened to show. Nothing equivalent was
added for the opposite condition. A rule set extended only in the direction the
data already points will keep finding what it was extended to find. Version 3
mirrors every heat state with its opposite at the same thresholds: `Easing`,
`Supply catching up`, `Cheap but tightening`. If the rules still say "hot" more
often than "easing", that is now a fact about the data rather than about which
rules happen to exist.

**Wording carried judgement.** "Supply is not answering" implies it ought to.
The index descriptions now state what the measures did, not what they should
have done.

What remains, unavoidably, is that someone chose which measures to collect and
how to group them into four indices. Those choices are in
`config/metrics.yml` and `config/rules/`, in the open, and the regime label on
every page prints the rule that produced it.

</div>

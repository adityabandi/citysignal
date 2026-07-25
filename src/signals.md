---
title: Signals
toc: false
---

```js
import {el, cityColor, formatPeriod} from "./components/theme.js";
const signals = await FileAttachment("data/signals.json").json();
```

<span class="cs-kicker">The lead-lag lab · ${signals.version ?? "unversioned"}</span>

# Does this signal actually lead anything?

<div class="cs-lede">

Search interest, news volume and page views are easy to publish and easy to
over-claim. With enough candidate series, something will correlate by accident.
So every "leading indicator" on this site has to earn the label here, and can
lose it.

</div>

<div class="cs-note" style="margin-top:1rem">

${signals.method}
The pairs tested are declared in <span class="cs-num">config/rules/</span> before
the correlations are computed, and a pair is never removed for scoring badly — a
signal that fails is a published result, not a mistake to hide.

</div>

## Verdicts

```js
const VERDICTS = {
  leading: {label: "Leads", className: "cs-fresh", glyph: "▲"},
  coincident: {label: "Coincident", className: "cs-unknown", glyph: "="},
  no_evidence: {label: "No evidence", className: "cs-stale", glyph: "✕"},
  insufficient_data: {label: "Not yet testable", className: "cs-unknown", glyph: "·"}
};

const pairs = signals.pairs ?? [];
const testable = pairs.filter((p) => p.verdict !== "insufficient_data");

display(
  pairs.length
    ? el("table", {class: "cs-table"}, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", {text: "Candidate signal"}),
            el("th", {text: "Outcome tested against"}),
            el("th", {text: "Verdict"}),
            el("th", {text: "Median lead"}),
            el("th", {text: "Cities leading"}),
            el("th", {text: "Cities testable"})
          ])
        ]),
        el("tbody", {},
          pairs.map((pair) => {
            const verdict = VERDICTS[pair.verdict] ?? VERDICTS.insufficient_data;
            return el("tr", {}, [
              el("td", {text: pair.signal_label}),
              el("td", {text: pair.outcome_label}),
              el("td", {}, [
                el("span", {class: `cs-freshness ${verdict.className}`, text: verdict.label})
              ]),
              el("td", {class: "cs-num", text: pair.median_lag == null ? "—" : `${pair.median_lag} periods`}),
              el("td", {class: "cs-num", text: String(pair.cities_leading)}),
              el("td", {class: "cs-num", text: String(pair.cities_tested)})
            ]);
          })
        )
      ])
    : el("div", {class: "cs-empty", text: "No pairs declared."})
);
```

```js
display(
  !testable.length
    ? el("div", {class: "cs-note", style: "margin-top:1rem", text:
        "Nothing is testable yet. A pair needs at least 30 overlapping year-over-year observations on both sides plus a held-out tail, which most sources will only reach once they have several years of history committed. Until then the honest answer is that we do not know, and that is what this page says."})
    : null
);
```

## How a signal earns and loses its label

<div class="cs-note">

The lag is chosen on the early part of each series, then scored on a held-out
tail the search never saw. That alone is not enough, because most of what a city
series does is season: the winning lag must also beat a seasonal-naive baseline
— "this month looks like the same month last year". A pair that correlates but
cannot beat the baseline is reported as no evidence, however good the raw
correlation looks.

Both sides are compared as year-over-year change rather than as levels, because
two trending series will correlate for reasons that have nothing to do with one
leading the other. A signal that stops beating the baseline is demoted here in
public, and its history stays on the page.

</div>

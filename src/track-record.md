---
title: Track record
toc: false
---

```js
import {el, formatPeriod, formatValue} from "./components/theme.js";

const record = await FileAttachment("data/track-record.json").json();
```

<span class="cs-kicker">Every forecast · every score · including the bad ones</span>

# The track record

<div class="cs-lede">

This page exists because anyone can publish a forecast and quietly forget it.
Every prediction CitySignal makes is written to a file, committed, and never
edited. When its outcome period arrives it is scored here automatically — whether
it was right or wrong — against the simple baseline it was supposed to beat.

</div>

<div class="cs-note" style="margin-bottom:1.8rem">

**How to check this yourself, without trusting us.** Each forecast below links to
the commit that created it. That commit predates the outcome it describes, and it
contains both the prediction and a hash of exactly the data the model was allowed
to see. Recompute the hash from the repository at that commit and it must match.
Rewriting a bad call after the fact would mean rewriting public git history, which
is visible to anyone who has ever cloned the repo.

</div>

```js
const scores = record.scores ?? [];
const matured = scores.length;
```

```js
display(
  matured === 0
    ? el("div", {class: "cs-panel"}, [
        el("div", {class: "cs-kicker", text: "Nothing has matured yet"}),
        el("div", {class: "cs-situation-text", style: "max-width:52ch", text:
          `${record.pending ?? 0} forecasts are frozen and waiting for their outcome periods to arrive.`}),
        el("div", {class: "cs-plain", style: "margin-top:.7rem", text:
          "This is the honest state of a track record that has just started: predictions exist and " +
          "are committed, but none can be scored until the months they describe have happened and " +
          "the statistics agency has published them. The first scores appear here automatically. " +
          "A forecasting product that showed impressive accuracy on day one would be showing you a " +
          "backtest, not a record."})
      ])
    : null
);
```

```js
// Once scores exist, lead with accuracy against the baseline rather than with a
// flattering aggregate.
display(
  matured > 0
    ? el("div", {class: "cs-grid"}, [
        ["Forecasts scored", matured],
        ["Inside their stated range", `${Math.round((record.summary?.coverage ?? 0) * 100)}%`],
        ["Direction called right", `${Math.round((record.summary?.direction_accuracy ?? 0) * 100)}%`],
        ["Median error", `${(record.summary?.median_abs_pct ?? 0).toFixed(1)}%`]
      ].map(([label, value]) =>
        el("div", {class: "cs-panel"}, [
          el("div", {class: "cs-card-label", text: label}),
          el("div", {class: "cs-card-value cs-num", text: String(value)})
        ])
      ))
    : null
);
```

## Forecasts on the record

```js
const frozen = record.frozen ?? [];

display(
  frozen.length
    ? el("table", {class: "cs-table"}, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", {text: "Issued"}),
            el("th", {text: "Question"}),
            el("th", {text: "For"}),
            el("th", {text: "Predicted"}),
            el("th", {text: "Actual"}),
            el("th", {text: "Error"}),
            el("th", {text: "In range"}),
            el("th", {text: "Model"})
          ])
        ]),
        el("tbody", {},
          frozen.map((f) =>
            el("tr", {}, [
              el("td", {class: "cs-num", text: (f.issued_at ?? "").slice(0, 10)}),
              el("td", {}, [
                el("span", {title: f.question ?? "", text: f.target_id.replace(/_/g, " ")})
              ]),
              el("td", {class: "cs-num", text: formatPeriod(f.for_period)}),
              el("td", {class: "cs-num", text: f.predicted_p50 == null ? "—" : formatValue(f.predicted_p50, f.unit)}),
              el("td", {class: "cs-num", text: f.actual == null ? "pending" : formatValue(f.actual, f.unit)}),
              el("td", {class: "cs-num cs-delta-neutral", text:
                f.pct_error == null ? "—" : `${f.pct_error > 0 ? "+" : ""}${f.pct_error.toFixed(1)}%`}),
              el("td", {}, [
                f.inside_80 == null
                  ? el("span", {class: "cs-kind", text: "—"})
                  : el("span", {
                      class: `cs-freshness ${f.inside_80 ? "cs-fresh" : "cs-failing"}`,
                      text: f.inside_80 ? "yes" : "no"
                    })
              ]),
              el("td", {class: "cs-kind", text: (f.model ?? "").replace(/_/g, " ")})
            ])
          )
        )
      ])
    : el("div", {class: "cs-empty", text: "No forecasts frozen yet."})
);
```

## What would make this page worth believing

<div class="cs-note">

A track record earns trust slowly and by a specific route, so it is worth being
explicit about what is not yet proven here.

**Enough scored forecasts to distinguish skill from luck.** With a handful of
outcomes, a good record and a lucky one look identical. Direction calls need
roughly thirty scored forecasts before the accuracy figure means much, and
interval coverage needs more.

**Calibration, not just accuracy.** If the 10–90 range is honest, the outcome
should fall inside it about 80% of the time — not 100%, which would mean the
intervals are uselessly wide, and not 50%, which would mean they are dishonestly
narrow. Both failures are visible in the coverage column above.

**Beating the baseline.** Predicting that next month resembles this month is
free. Any model here that cannot beat that is reported as not beating it, and
until one does, the baseline is what gets published.

Until those three hold, this page is a record of a system that is being honest
about not having proven itself yet. That is a better starting point than a
confident number.

</div>

```js
display(
  el("div", {class: "cs-meta", style: "border-top:none;margin-top:1.4rem"}, [
    el("span", {class: "cs-kind", text: record.targets_version ?? ""}),
    el("span", {text: `generated ${(record.generated_at ?? "").slice(0, 10)}`}),
    el("a", {href: "https://github.com/adityabandi/citysignal/tree/main/data/forecasts", text: "the frozen forecast files →"})
  ])
);
```

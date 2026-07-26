---
title: Forecast
toc: false
---

```js
import {el, formatPeriod, formatValue, cityColor, describeZ} from "./components/theme.js";
import {sparkline} from "./components/metricCard.js";

const fc = await FileAttachment("data/forecasts.json").json();
const cities = await FileAttachment("data/cities.json").json();
const madrid = cities.madrid;
```

<span class="cs-kicker">Madrid · frozen at issue · scored in public</span>

# What we think happens next

<div class="cs-lede">

Each forecast below was written before its outcome existed, committed to the
repository, and left alone. When the period matures it is scored against what
actually happened — wins and losses both — on the <a href="./track-record">track
record</a>. Nothing here is a crash call: these are named statistics with
intervals, which is the only kind of prediction that can be checked.

</div>

```js
const issued = fc.forecasts.filter((f) => f.status === "issued");
const blocked = fc.forecasts.filter((f) => f.status !== "issued");

// A forecast whose chosen model is a baseline is a real forecast — it is just
// one that says "nothing we track beats the obvious guess here". Saying so is
// the point.
const skilled = issued.filter((f) => f.model_verdict === "beats_baseline");
```

```js
// The honest headline gets the headline: if nothing beats a naive baseline yet,
// that is the most important fact on the page.
display(
  el("div", {class: "cs-headline"}, [
    el("div", {class: "cs-kicker", text: "Where the models stand"}),
    el("div", {class: "cs-headline-text"},
      skilled.length === 0
        ? [
            document.createTextNode(`Of ${issued.length} forecasts, `),
            el("span", {class: "cs-headline-figure cs-num", text: "none"}),
            document.createTextNode(" yet beat a simple baseline.")
          ]
        : [
            el("span", {class: "cs-headline-figure cs-num", text: String(skilled.length)}),
            document.createTextNode(` of ${issued.length} forecasts beat a simple baseline.`)
          ]
    ),
    el("div", {class: "cs-note", style: "max-width:60ch;margin-top:.9rem", text:
      'A baseline here means something like "same as last month" or "same month last year" — ' +
      "free, explanation-free, and in this market genuinely hard to beat. Until a model clears " +
      "one by the margin declared in targets-v1.yml, the baseline is what gets published, " +
      "labelled as such."})
  ])
);
```

## The forecasts

```js
function verdictChip(verdict) {
  const map = {
    beats_baseline: ["cs-fresh", "beats the baseline"],
    baseline: ["cs-unknown", "baseline is best"],
    no_better_than_baseline: ["cs-stale", "no model beats baseline"],
    insufficient_data: ["cs-unknown", "not enough history"]
  };
  const [cls, label] = map[verdict] ?? ["cs-unknown", verdict];
  return el("span", {class: `cs-freshness ${cls}`, text: label});
}

display(
  el("div", {class: "cs-grid", style: "grid-template-columns:repeat(auto-fill,minmax(340px,1fr))"},
    issued.map((f) => {
      const q = f.quantiles;
      const change = q ? ((q.p50 - f.last_observed) / Math.abs(f.last_observed)) * 100 : null;
      const chosen = f.skill?.[f.model] ?? {};
      return el("div", {class: "cs-panel"}, [
        el("div", {class: "cs-kicker", text: f.question}),
        q
          ? el("div", {}, [
              el("div", {class: "cs-card-value"}, [
                document.createTextNode(formatValue(q.p50, f.unit)),
                el("span", {class: "cs-card-unit", text: `by ${formatPeriod(f.for_period)}`})
              ]),
              el("div", {class: "cs-plain", style: "margin-top:.1rem"},
                `Range ${formatValue(q.p10, f.unit)} to ${formatValue(q.p90, f.unit)}. ` +
                `Last observed ${formatValue(f.last_observed, f.unit)} in ${formatPeriod(f.from_period)}` +
                (change === null ? "." : `, so a change of ${change > 0 ? "+" : ""}${change.toFixed(1)}%.`))
            ])
          : el("div", {class: "cs-plain", text: "Direction only — too little history for an interval."}),
        f.p_direction_up != null
          ? el("div", {class: "cs-index-figure", style: "margin-top:.6rem"}, [
              el("span", {class: "cs-num", text: `${Math.round(f.p_direction_up * 100)}%`}),
              el("span", {class: "cs-card-unit", text: "chance it ends up higher than today"})
            ])
          : null,
        el("div", {class: "cs-meta"}, [
          verdictChip(f.model_verdict),
          el("span", {class: `cs-scope${f.geo_level === "municipality" ? "" : " cs-scope-wider"}`, text: f.geo_level}),
          el("span", {class: "cs-kind", text: f.model.replace(/_/g, " ")}),
          chosen.folds ? el("span", {text: `tested on ${chosen.folds} past windows`}) : null
        ])
      ]);
    })
  )
);
```

## Why each of these, and not a price prediction

```js
display(
  el("div", {class: "cs-grid", style: "grid-template-columns:repeat(auto-fill,minmax(300px,1fr))"},
    issued.map((f) =>
      el("div", {class: "cs-panel"}, [
        el("div", {class: "cs-card-label", text: f.question}),
        el("div", {class: "cs-plain", text: f.why})
      ])
    )
  )
);
```

## Not forecast, and why

```js
display(
  blocked.length
    ? el("table", {class: "cs-table"}, [
        el("thead", {}, [
          el("tr", {}, [
            el("th", {text: "Question"}),
            el("th", {text: "Status"}),
            el("th", {text: "Reason"})
          ])
        ]),
        el("tbody", {},
          blocked.map((f) =>
            el("tr", {}, [
              el("td", {text: f.question || f.target_id}),
              el("td", {}, [verdictChip(f.status === "not_implemented" ? "insufficient_data" : f.status)]),
              el("td", {class: "cs-note", text:
                f.status === "insufficient_data"
                  ? `${f.observations} observations available, ${f.needed} needed before a backtest means anything.`
                  : "Declared as a target; its scoring machinery is not built yet, so nothing is published."})
            ])
          )
        )
      ])
    : el("div", {class: "cs-empty", text: "Every declared target is being forecast."})
);
```

## How the models were compared

<div class="cs-note">

Every model is tested by walking forward through history: train on everything up
to a date, skip a gap, predict, compare against what happened, step on, repeat.
A random train/test split would let a model learn from next year to predict last
year, which inflates every score and means nothing for a series that runs
forwards.

Skill is judged on **pinball loss**, not on error at the midpoint. That choice
changed a published answer here: on Madrid unemployment, "same month last year"
had the lowest midpoint error of any model, but its interval spanned 85,000 to
176,000 people — wide enough to be right almost always and useless to anyone.
Pinball loss charges for that width, and a tighter model won instead.

</div>

```js
display(
  el("table", {class: "cs-table"}, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", {text: "Question"}),
        el("th", {text: "Model"}),
        el("th", {text: "Loss vs best baseline"}),
        el("th", {text: "Inside its own range"}),
        el("th", {text: "Windows"}),
        el("th", {text: "Verdict"})
      ])
    ]),
    el("tbody", {},
      issued.flatMap((f) =>
        Object.values(f.skill ?? {})
          .sort((a, b) => a.pinball - b.pinball)
          .map((s) =>
            el("tr", {style: s.model === f.model ? "background:rgba(255,255,255,.02)" : ""}, [
              el("td", {class: "cs-note", text: s.model === f.model ? f.target_id : ""}),
              el("td", {text: s.model.replace(/_/g, " ")}),
              el("td", {class: "cs-num", text: s.pinball_ratio == null ? "—" : s.pinball_ratio.toFixed(3)}),
              el("td", {class: "cs-num", text: `${Math.round(s.coverage_80 * 100)}%`}),
              el("td", {class: "cs-num", text: s.folds}),
              el("td", {class: "cs-note", text: s.verdict.replace(/_/g, " ")})
            ])
          )
      )
    )
  ])
);
```

<div class="cs-note" style="margin-top:.6rem">

"Inside its own range" should sit near 80% for a well-calibrated 10–90 interval.
Above that means the interval is too wide, below means too narrow. Several of
these are too wide, which is visible here rather than hidden.

</div>

## What the models were allowed to see

<div class="cs-note">

A backtest that reads today's data is not a backtest. Every fold above was fitted
on a reconstruction of the history **as it stood at the time**, so a model
forecasting March 2015 could not see figures published in April.

That reconstruction needs to know when each number became public, and only one of
our seventeen sources actually records a publication date. For the rest,
availability is modelled as period-end plus a declared per-source lag — INE at 45
days, SEPE at 3, the judicial statistics at 95 — and the lags err long, because
assuming data arrived later than it did makes a model look worse rather than
better. Every forecast records which basis it used.

</div>

```js
display(
  el("div", {class: "cs-meta", style: "border-top:none"}, [
    el("span", {class: "cs-num", text: fc.data_vintage}),
    el("span", {text: `${fc.vintage_basis.published_at.toLocaleString("en-GB")} rows with a real publication date`}),
    el("span", {text: `${fc.vintage_basis.declared_lag.toLocaleString("en-GB")} rows on a declared lag`}),
    el("span", {text: `${fc.revisions_in_history} restatements in history`}),
    el("span", {class: "cs-kind", text: fc.targets_version})
  ])
);
```

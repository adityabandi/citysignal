---
title: Sources
toc: false
---

```js
import {el, formatPeriod} from "./components/theme.js";
const sources = await FileAttachment("data/sources.json").json();
const rows = sources.sources ?? [];
```

<span class="cs-kicker">Data health · public catalogue</span>

# Where every number comes from

<div class="cs-lede">

This page is part of the product, not an engineering afterthought. If a source
breaks, its last-good values stay on the site with a stale badge and the failure
appears here and as a GitHub issue — the charts never quietly freeze.

</div>

```js
const STATUS = {
  ok: {label: "reporting", className: "cs-fresh"},
  skipped: {label: "unchanged", className: "cs-fresh"},
  partial: {label: "partial", className: "cs-stale"},
  failed: {label: "failing", className: "cs-failing"}
};

display(
  rows.length
    ? el("div", {class: "cs-grid", style: "grid-template-columns:repeat(auto-fill,minmax(320px,1fr))"},
        rows.map((source) => {
          const status = STATUS[source.status] ?? {label: source.status ?? "unknown", className: "cs-unknown"};
          return el("div", {class: "cs-panel"}, [
            el("div", {class: "cs-card-head"}, [
              el("span", {class: "cs-card-label", text: source.publisher ?? source.source_id}),
              el("span", {class: `cs-freshness ${status.className}`, text: status.label})
            ]),
            el("div", {class: "cs-note", style: "margin-top:.35rem", text: source.declared?.notes?.trim() || source.attribution || ""}),
            el("div", {class: "cs-meta"}, [
              el("span", {class: `cs-kind cs-kind-${source.kind ?? "official"}`, text: source.kind ?? "official"}),
              el("span", {class: "cs-scope", text: source.geo_level ?? "—"}),
              el("span", {text: source.cadence ?? ""}),
              source.latest_observation
                ? el("span", {text: `latest ${formatPeriod(source.latest_observation)}`})
                : null,
              source.staleness_days != null
                ? el("span", {text: `${source.staleness_days}d old · limit ${source.max_age_days}d`})
                : null
            ]),
            el("div", {class: "cs-meta", style: "border-top:none;padding-top:0"}, [
              el("span", {text: `last checked ${source.last_checked?.slice(0, 10) ?? "never"}`}),
              source.docs_url ? el("a", {href: source.docs_url, text: "documentation"}) : null
            ]),
            source.last_error
              ? el("div", {class: "cs-note", style: "margin-top:.5rem;color:var(--cs-dislocation)", text: source.last_error})
              : null,
            el("div", {class: "cs-note", style: "margin-top:.5rem;font-size:.68rem;color:var(--cs-ink-4)", text: source.license ?? ""})
          ]);
        })
      )
    : el("div", {class: "cs-empty", text: "No source has reported yet. Run the pipeline to populate this catalogue."})
);
```

## Reading the labels

<div class="cs-note">

**Official** means a national or municipal statistics producer. **Research**
means an independent dataset — Inside Airbnb is scraped snapshots of listings,
not a register of dwellings, and Wikipedia pageviews are attention rather than
demand. **Commercial** means a licensed product. The distinction is on every
card on the site, because a research snapshot and a national statistic should
never be read with the same confidence.

**Latest** is the period the data describes. **Last checked** is when we asked
the publisher. A source checked this morning may still be reporting April — the
two dates are never merged into one "updated" timestamp, because that is how a
stale number comes to look current.

Sources whose terms do not permit redistribution appear here only as derived
aggregates; their rows are never committed to this repository.

</div>

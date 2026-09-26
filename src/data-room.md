---
title: Data catalog
toc: false
---

```js
import { el, nav } from "./components/desk.js";
display(el("div", { class: "ec-app" }, [nav("data")]));
const manifest = await FileAttachment("data/economy-manifest.json").json();
const panel = FileAttachment("data/economy-panel.csv");
const vintages = FileAttachment("data/economy-vintages.csv");
const dictionary = FileAttachment("data/economy-manifest.json");
```

<div class="ec-app">
<span class="ec-eyebrow">CITYSIGNAL / DATA</span>
<h1>Data catalog</h1>
<p class="ec-muted">Country observations, captured revisions and series definitions.</p>
</div>

```js
display(
  el("div", { class: "ec-app" }, [
    el("div", { class: "ec-structure-grid" }, [
      el("div", {}, [
        el("span", { text: "Current observations" }),
        el("strong", {
          text: manifest.current_observations.toLocaleString("en-GB"),
        }),
      ]),
      el("div", {}, [
        el("span", { text: "Country markets" }),
        el("strong", { text: String(Object.keys(manifest.countries).length) }),
      ]),
      el("div", {}, [
        el("span", { text: "Snapshot date" }),
        el("strong", { text: manifest.as_of }),
      ]),
    ]),
    el("div", { class: "ec-downloads" }, [
      el("a", {
        href: await panel.url(),
        download: "citysignal-economy-panel.csv",
        class: "ec-button",
        text: "↓ Current observations · CSV",
      }),
      el("a", {
        href: await vintages.url(),
        download: "citysignal-economy-vintages.csv",
        class: "ec-button",
        text: "↓ Captured revisions · CSV",
      }),
      el("a", {
        href: await dictionary.url(),
        download: "citysignal-data-dictionary.json",
        class: "ec-button",
        text: "↓ Dictionary & checksums · JSON",
      }),
    ]),
    el("details", { class: "ec-method" }, [
      el("summary", { text: "Release and capture dates" }),
      el("p", { text: manifest.availability_policy }),
    ]),
  ]),
);
```

<div class="ec-app">
<div class="ec-section-heading"><h2>Observation schema</h2></div>
<div class="ec-table-wrap"><table class="ec-table"><thead><tr><th>Field</th><th>Meaning</th></tr></thead><tbody>
<tr><td>metric_id / geo_id / period</td><td>Series and observation identity. </td></tr>
<tr><td>value / unit</td><td>Raw source measurement. Dashboard transformations are documented separately.</td></tr>
<tr><td>observation_end</td><td>Last date described by the observation—not its release date.</td></tr>
<tr><td>published_at</td><td>Source publication date where known. Blank means unknown.</td></tr>
<tr><td>fetched_at</td><td>First capture date of this revision. A backfill does not establish earlier availability.</td></tr>
<tr><td>quality_flag / revision</td><td>Estimated or suspect observations, plus version number for captured restatements.</td></tr>
<tr><td>source_id</td><td>Join to publisher, license and definition in the dictionary.</td></tr>
</tbody></table></div>
<div class="ec-section-heading"><h2>Coverage and freshness</h2><a class="ec-link" href="./sources">Source status ↗</a></div>
</div>

```js
display(
  el("div", { class: "ec-app" }, [
    el("div", { class: "ec-table-wrap" }, [
      el("table", { class: "ec-table" }, [
        el("thead", {}, [
          el(
            "tr",
            {},
            ["Measure", "Cadence", "Scope", "Source"].map((text) =>
              el("th", { text }),
            ),
          ),
        ]),
        el(
          "tbody",
          {},
          Object.entries(manifest.metrics).map(([id, m]) =>
            el("tr", {}, [
              el("td", {}, [
                el("span", { text: m.label }),
                el("small", {
                  style: "display:block",
                  class: "ec-muted",
                  text: id,
                }),
              ]),
              el("td", { text: m.cadence }),
              el("td", { text: m.geo_level.replaceAll("_", " ") }),
              el("td", { text: m.source_id }),
            ]),
          ),
        ),
      ]),
    ]),
    el("details", { class: "ec-method" }, [
      el("summary", { text: "File checksums · SHA-256" }),
      ...manifest.files.map((f) =>
        el("p", {}, [
          el("strong", { text: f.file + " · " }),
          el("code", { style: "overflow-wrap:anywhere", text: f.sha256 }),
        ]),
      ),
    ]),
  ]),
);
```

<div class="ec-app">
<div class="ec-section-heading"><h2>Coverage</h2></div>
<p>28 country markets. Monthly cycle, inflation, policy, shipping, energy and geopolitical series; daily hiring and annual macroeconomic benchmarks. Coverage by market and source is available in the <a href="./">market monitor</a>. Spain includes regional and city-level datasets.</p>
<p>Dashboard exports contain displayed transformations. The full observation panel preserves source units. Derived hiring and trade spreads are documented in the dictionary.</p>
</div>

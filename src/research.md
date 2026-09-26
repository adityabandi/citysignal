---
title: Research
toc: false
---

```js
import { el, txt, nav, table, link } from "./components/desk.js";
const research = await FileAttachment("data/research.json").json();
const economy = await FileAttachment("data/economy-overview.json").json();
display(el("div", { class: "ec-app" }, [nav("research")]));
```

<div class="ec-app">
<header class="desk-heading"><div><span class="ec-eyebrow">CITYSIGNAL / RESEARCH</span><h1>Signal research</h1><p class="ec-muted">Definitions, coverage and model results.</p></div></header>
</div>

```js
const families = [
  [
    "Labour demand",
    "Indeed Hiring Lab",
    "hiring_total",
    "Total and new job postings; 28-day hiring-flow spread. Seasonally adjusted, seven-day averages.",
    "daily",
  ],
  [
    "Trade & cargo",
    "IMF PortWatch",
    "shipping_exports",
    "Seaborne imports, exports and port calls; container and tanker breakdowns; export–import growth spread.",
    "monthly",
  ],
  [
    "Geopolitics",
    "Caldara & Iacoviello",
    "geopolitical_risk",
    "Country-specific share of geopolitical-risk newspaper articles. US newspaper sample; country and major-city mentions.",
    "monthly",
  ],
  [
    "Energy exposure",
    "Ember",
    "power_demand",
    "Electricity load, fossil generation share, carbon intensity and wholesale prices.",
    "monthly",
  ],
  [
    "Business electricity",
    "Red Eléctrica",
    "electricity_large_users",
    "Mainland Spanish large-user demand index. National daily load is available separately.",
    "monthly / daily",
  ],
  [
    "Business cycle",
    "OECD",
    "oecd_cli",
    "Amplitude-adjusted composite leading indicator. Long-run trend=100.",
    "monthly",
  ],
  [
    "Inflation & rates",
    "BIS",
    "bis_cpi",
    "National CPI inflation and end-of-month policy rates. Euro-area policy is retained as a shared series.",
    "monthly",
  ],
];
display(
  el("div", { class: "ec-app" }, [
    table(
      ["Family", "Source", "Measures", "Frequency", "Markets"],
      families.map(([name, source, id, definition, cadence]) => {
        const markets = economy.countries.filter((c) =>
          [...c.metrics, ...c.alternative].some((m) => m.id === id),
        );
        return el("tr", {}, [
          txt("td", name),
          txt("td", source),
          txt("td", definition),
          txt("td", cadence),
          el(
            "td",
            {},
            markets.map((c) =>
              link(
                c.code.toUpperCase(),
                `./?country=${c.code}&metric=${id}`,
                "desk-market-code",
              ),
            ),
          ),
        ]);
      }),
    ),
  ]),
);
```

<div class="ec-app">
<div class="ec-section-heading"><h2>Forecast experiments</h2><span class="ec-muted">Spain · monthly targets · revised history</span></div>
<p class="ec-muted">Expanding-window evaluation. Error reduction is measured against the autoregressive baseline on matching test months.</p>
</div>

```js
display(
  el("div", { class: "ec-app" }, [
    table(
      [
        "Specification",
        "Test period",
        "Months",
        "Model RMSE",
        "AR RMSE",
        "Persistence RMSE",
        "Error reduction",
      ],
      research.tests.map((t) =>
        el("tr", {}, [
          txt("td", t.label),
          txt("td", t.test_start ? `${t.test_start} – ${t.test_end}` : "—"),
          txt("td", String(t.test_observations)),
          txt("td", t.rmse?.toFixed(2) ?? "—"),
          txt("td", t.baseline_rmse?.toFixed(2) ?? "—"),
          txt("td", t.persistence_rmse?.toFixed(2) ?? "—"),
          txt("td", t.skill == null ? "—" : `${t.skill.toFixed(1)}%`),
        ]),
      ),
    ),
    el("details", { class: "ec-method" }, [
      txt("summary", "Model specification & sample"),
      txt("p", research.method),
      txt("p", research.limitation),
    ]),
  ]),
);
```

<div class="ec-app">
<div class="ec-section-heading"><h2>Dataset development</h2><span class="ec-muted">Additional coverage</span></div>
<div class="ec-table-wrap"><table class="ec-table"><thead><tr><th>Dataset</th><th>Measure</th><th>Resolution</th><th>Source</th></tr></thead><tbody>
<tr><td>Public procurement</td><td>Awarded value, cancellations, amendments and contract duration. Entity and notice-version matching.</td><td>EU / region / buyer / sector</td><td><a href="https://docs.ted.europa.eu/api/latest/search.html">TED Search API</a></td></tr>
<tr><td>Industrial night lights</td><td>Radiance over fixed industrial sites, with cloud and lunar corrections.</td><td>Global / site</td><td><a href="https://blackmarble.gsfc.nasa.gov/">NASA Black Marble</a></td></tr>
<tr><td>Occupational hiring</td><td>Sector diffusion, new-posting momentum and construction-to-services hiring spread.</td><td>Country / occupation</td><td><a href="https://github.com/hiring-lab/job_postings_tracker">Indeed Hiring Lab</a></td></tr>
<tr><td>Business formation & exits</td><td>Incorporations, dissolutions and insolvency filings; entity matching and filing-date history.</td><td>Spain / province / sector</td><td><a href="https://www.boe.es/datosabiertos/">BOE / BORME</a></td></tr>
</tbody></table></div>
</div>

import {
  el,
  txt,
  num,
  signed,
  button,
  select,
  field,
  nav,
  seriesOf,
  metricOf,
  table,
  download,
  formatPeriod,
} from "./desk.js";

const LENSES = {
  "Activity & prices": [
    "oecd_cli",
    "bis_cpi",
    "bis_policy_rate",
    "shipping_exports",
    "hiring_total",
    "geopolitical_risk",
  ],
  Trade: [
    "shipping_exports",
    "shipping_imports",
    "shipping_container_exports",
    "shipping_tanker_imports",
    "shipping_calls",
    "trade_flow_gap",
  ],
  Labour: [
    "hiring_total",
    "hiring_new",
    "hiring_flow_gap",
    "macro_unemployment",
    "macro_employment",
    "wb_unemployment",
  ],
  Energy: [
    "power_demand",
    "power_price",
    "power_fossil_share",
    "power_carbon_intensity",
    "electricity_large_users",
  ],
  Risk: [
    "geopolitical_risk",
    "bis_policy_rate",
    "bis_cpi",
    "power_price",
    "trade_flow_gap",
  ],
  "Annual macro": [
    "wb_gdp_growth",
    "wb_inflation",
    "wb_unemployment",
    "wb_gdp_per_capita",
    "wb_trade",
  ],
};
const SHORT = {
  oecd_cli: "Leading indicator",
  bis_cpi: "CPI",
  bis_policy_rate: "Policy rate",
  shipping_exports: "Seaborne exports",
  hiring_total: "Job postings",
  geopolitical_risk: "Geopolitical news",
  shipping_imports: "Seaborne imports",
  shipping_container_exports: "Container exports",
  shipping_tanker_imports: "Tanker imports",
  shipping_calls: "Port calls",
  trade_flow_gap: "Trade growth gap",
  hiring_new: "New postings",
  hiring_flow_gap: "Hiring flow gap",
  macro_unemployment: "Unemployment",
  macro_employment: "Employment",
  wb_unemployment: "Unemployment · annual",
  power_demand: "Electricity load",
  power_price: "Power price",
  power_fossil_share: "Fossil share",
  power_carbon_intensity: "Carbon intensity",
  electricity_large_users: "Business electricity",
  wb_gdp_growth: "GDP growth",
  wb_inflation: "Inflation",
  wb_gdp_per_capita: "GDP per capita · PPP",
  wb_trade: "Trade / GDP",
};

export function globalOverview(data, openCountry, loadScreener, state = {}) {
  state.lens ||= "Activity & prices";
  state.mode ||= "Monitor";
  if (!state.watch) {
    try {
      state.watch = new Set(
        JSON.parse(localStorage.getItem("citysignal-markets") || "[]"),
      );
    } catch {
      state.watch = new Set();
    }
  }
  const allMetrics = new Map(
    data.countries.flatMap((c) => seriesOf(c).map((m) => [m.id, m])),
  );
  const root = el("div", { class: "desk-global" });
  const count = data.countries.reduce((n, c) => n + seriesOf(c).length, 0);
  root.append(
    nav(),
    el("header", { class: "desk-heading" }, [
      el("div", {}, [
        txt("span", "CITYSIGNAL / GLOBAL MACRO", "ec-eyebrow"),
        txt("h1", "Market monitor"),
      ]),
      txt(
        "p",
        `${data.countries.length} markets · ${count} series · Snapshot ${data.as_of}`,
        "ec-muted",
      ),
    ]),
  );
  const modes = el("div", {
    class: "desk-tabs",
    "aria-label": "Workspace view",
  });
  const search = el("input", {
    type: "search",
    class: "ec-search",
    placeholder: "Country / ISO code",
    "aria-label": "Find a country",
    value: state.search || "",
  });
  const region = select(
    "Region",
    [
      ["", "All regions"],
      ...[...new Set(data.countries.map((c) => c.region))]
        .sort()
        .map((r) => [r, r]),
    ],
    state.region || "",
  );
  const universe = select(
    "Universe",
    [
      ["all", "All markets"],
      ["selected", "Selected markets"],
    ],
    state.universe || "all",
  );
  const exportButton = button("Export view ↓", () =>
    download(
      `citysignal-${state.mode.toLowerCase()}-${state.metric || state.lens.replaceAll(" ", "-")}.csv`,
      exportRows,
    ),
  );
  let exportRows = [];
  const controls = el("div", { class: "desk-toolbar" }, [
    field("Market", search),
    field("Region", region),
    field("Universe", universe),
    exportButton,
  ]);
  const lensTabs = el("div", {
    class: "desk-tabs desk-lenses",
    "aria-label": "Signal family",
  });
  const screenControls = el("div", { class: "desk-toolbar" });
  const status = txt("p", "", "desk-status");
  status.setAttribute("aria-live", "polite");
  const body = el("div", { class: "desk-matrix" });
  root.append(modes, controls, lensTabs, screenControls, status, body);
  const filtered = () =>
    data.countries
      .filter(
        (c) =>
          (!region.value || c.region === region.value) &&
          (universe.value !== "selected" || state.watch.has(c.code)) &&
          `${c.name} ${c.code} ${c.iso3}`
            .toLowerCase()
            .includes(search.value.toLowerCase().trim()),
      )
      .sort((a, b) => a.name.localeCompare(b.name));
  const rowsBase = (c) => {
    const cb = el("input", {
      type: "checkbox",
      "aria-label": `Select ${c.name}`,
    });
    cb.checked = state.watch.has(c.code);
    cb.onchange = () => {
      cb.checked ? state.watch.add(c.code) : state.watch.delete(c.code);
      try {
        localStorage.setItem(
          "citysignal-markets",
          JSON.stringify([...state.watch]),
        );
      } catch {}
      update();
    };
    return el("td", { class: "desk-market" }, [
      el("div", {}, [
        cb,
        button(c.name, () => openCountry(c.code), "ec-country-link"),
      ]),
      txt("small", `${c.iso3} · ${c.region}`),
    ]);
  };
  function metricCell(c, id) {
    const m = metricOf(c, id);
    if (!m) return txt("td", "—", "desk-missing");
    const b = button("", () => openCountry(c.code, m.id), "desk-reading");
    b.title = `${m.source} · ${m.scope}\n${m.description}\n${m.change_window}: ${signed(m.change)} ${m.change_unit}`;
    b.setAttribute(
      "aria-label",
      `${c.name}: ${m.label}, ${num(m.value)} ${m.unit}, ${m.period}`,
    );
    b.append(
      txt("strong", num(m.value)),
      txt(
        "small",
        `${formatPeriod(m.period)}${m.freshness === "stale" ? " · lagged" : ""}`,
      ),
    );
    if (m.change != null)
      b.append(
        txt(
          "span",
          `${signed(m.change)} ${m.change_unit} / ${m.change_window}`,
          "desk-delta",
        ),
      );
    return el("td", {}, [b]);
  }
  async function update() {
    state.search = search.value;
    state.region = region.value;
    state.universe = universe.value;
    const rows = filtered();
    exportRows = [];
    exportButton.disabled = false;
    modes.replaceChildren(
      ...["Monitor", "Screener", "Coverage"].map((mode) => {
        const b = button(
          mode,
          () => {
            state.mode = mode;
            update();
          },
          `ec-range ${state.mode === mode ? "active" : ""}`,
        );
        b.setAttribute("aria-pressed", String(state.mode === mode));
        return b;
      }),
    );
    lensTabs.replaceChildren();
    screenControls.replaceChildren();
    if (state.mode === "Monitor") {
      lensTabs.append(
        ...Object.keys(LENSES).map((lens) => {
          const b = button(
            lens,
            () => {
              state.lens = lens;
              update();
            },
            `ec-range ${lens === state.lens ? "active" : ""}`,
          );
          b.setAttribute("aria-pressed", String(lens === state.lens));
          return b;
        }),
      );
      const ids = LENSES[state.lens];
      status.textContent = `${rows.length} markets · Latest observations · Δ versus indicated prior period`;
      body.replaceChildren(
        table(
          [
            "Market",
            ...ids.map((id) =>
              el("div", {}, [
                txt("span", SHORT[id]),
                txt("small", allMetrics.get(id)?.unit || "%"),
              ]),
            ),
          ],
          rows.map((c) =>
            el("tr", {}, [rowsBase(c), ...ids.map((id) => metricCell(c, id))]),
          ),
        ),
      );
      exportRows = rows.flatMap((c) =>
        ids.map((id) => {
          const m = metricOf(c, id);
          return {
            country: c.name,
            iso3: c.iso3,
            metric_id: m?.id || id,
            period: m?.period,
            value: m?.value,
            unit: m?.unit,
            change: m?.change,
            change_unit: m?.change_unit,
            change_window: m?.change_window,
            source: m?.source,
            scope: m?.scope,
            quality: m?.quality,
            captured_at: m?.fetched_at,
          };
        }),
      );
    } else if (state.mode === "Coverage") {
      const providers = [
        ...new Set(
          data.countries.flatMap((c) => seriesOf(c).map((m) => m.source_id)),
        ),
      ].sort();
      const labels = {
        bis_macro: "BIS",
        oecd_cli: "OECD",
        gpr: "GPR",
        ember: "Ember",
        portwatch: "PortWatch",
        indeed_daily: "Indeed",
        worldbank: "World Bank",
        eurostat_macro: "Eurostat",
        ree: "REE",
        ecb: "ECB",
      };
      status.textContent = `${rows.length} markets · ${providers.length} sources · Series count / latest observation`;
      body.replaceChildren(
        table(
          ["Market", ...providers.map((p) => labels[p] || p)],
          rows.map((c) =>
            el("tr", {}, [
              rowsBase(c),
              ...providers.map((source) => {
                const ms = seriesOf(c).filter(
                  (m) => m.source_id === source && m.signal_type !== "derived",
                );
                const latest = ms
                  .map((m) => m.period)
                  .sort()
                  .at(-1);
                exportRows.push({
                  country: c.name,
                  iso3: c.iso3,
                  source,
                  series: ms.length,
                  latest_observation: latest,
                });
                return el(
                  "td",
                  {},
                  ms.length
                    ? [
                        txt("strong", String(ms.length)),
                        txt("small", formatPeriod(latest)),
                      ]
                    : [txt("span", "—", "desk-missing")],
                );
              }),
            ]),
          ),
        ),
      );
    } else {
      if (!state.screener) {
        status.textContent = "Loading period history…";
        body.replaceChildren();
        exportButton.disabled = true;
        try {
          state.screener = await (state.loading ||= loadScreener());
          if (state.mode === "Screener") update();
        } catch {
          state.loading = null;
          status.textContent = "Period history unavailable.";
          body.replaceChildren(button("Retry", update));
        }
        return;
      }
      const index = state.screener;
      const options = Object.values(index).sort(
        (a, b) =>
          a.group.localeCompare(b.group) || a.label.localeCompare(b.label),
      );
      if (!index[state.metric])
        state.metric = "oecd_cli" in index ? "oecd_cli" : options[0].id;
      const metric = index[state.metric];
      const historyFor = (c) =>
        metric.countries[c.code] ||
        (state.metric === "bis_policy_rate"
          ? index.bis_euro_policy_rate?.countries[c.code]
          : null);
      const choose = select(
        "Indicator",
        options.map((m) => [m.id, `${m.group} / ${m.label} · ${m.cadence}`]),
        state.metric,
      );
      const periods = [
        ...new Set(
          rows.flatMap((c) => historyFor(c)?.map((p) => p.period) || []),
        ),
      ]
        .sort()
        .reverse();
      if (!periods.includes(state.period)) state.period = periods[0];
      const period = select(
        "Observation period",
        periods.map((p) => [
          p,
          `${formatPeriod(p)} (${rows.filter((c) => historyFor(c)?.some((d) => d.period === p)).length}/${rows.length})`,
        ]),
        state.period,
      );
      const sort = select(
        "Sort",
        [
          ["name", "Country A–Z"],
          ["desc", "Value: high to low"],
          ["asc", "Value: low to high"],
          ["change", "Change: high to low"],
        ],
        state.sort || "name",
      );
      choose.onchange = () => {
        state.metric = choose.value;
        state.period = null;
        update();
      };
      period.onchange = () => {
        state.period = period.value;
        update();
      };
      sort.onchange = () => {
        state.sort = sort.value;
        update();
      };
      screenControls.append(
        field("Indicator", choose),
        field("Observation period", period),
        field("Sort", sort),
      );
      const reading = (c) =>
        historyFor(c)?.find((p) => p.period === state.period);
      const sorted = rows.slice();
      if (sort.value !== "name")
        sorted.sort((a, b) => {
          const x = reading(a)?.[sort.value === "change" ? "change" : "value"],
            y = reading(b)?.[sort.value === "change" ? "change" : "value"];
          return x == null
            ? y == null
              ? 0
              : 1
            : y == null
              ? -1
              : sort.value === "asc"
                ? x - y
                : y - x;
        });
      status.textContent = `${rows.filter((c) => reading(c)).length}/${rows.length} markets · ${formatPeriod(state.period)} · ${metric.unit}`;
      body.replaceChildren(
        table(
          [
            "Market",
            `Value · ${metric.unit}`,
            `Δ ${metric.change_window} · ${metric.change_unit}`,
            "Source",
            "Scope",
          ],
          sorted.map((c) => {
            const p = reading(c),
              m = metricOf(c, state.metric);
            exportRows.push({
              country: c.name,
              iso3: c.iso3,
              metric_id: m?.id || state.metric,
              period: state.period,
              value: p?.value,
              unit: metric.unit,
              change: p?.change,
              change_unit: metric.change_unit,
              source: m?.source,
              scope: m?.scope,
              quality: p?.quality,
              snapshot: data.as_of,
            });
            return el("tr", {}, [
              rowsBase(c),
              el("td", {}, [
                p
                  ? button(
                      num(p.value),
                      () => openCountry(c.code, m?.id || state.metric),
                      "desk-reading",
                    )
                  : txt("span", "—", "desk-missing"),
              ]),
              txt("td", signed(p?.change)),
              txt("td", m?.source || "—"),
              txt("td", m?.scope || "—", "desk-scope"),
            ]);
          }),
        ),
      );
    }
    exportButton.disabled = !exportRows.length;
    if (!rows.length)
      body.append(
        txt(
          "p",
          universe.value === "selected"
            ? "No selected markets. Add markets using row checkboxes."
            : "No matching markets.",
          "ec-empty",
        ),
      );
  }
  search.oninput = update;
  region.onchange = update;
  universe.onchange = update;
  update();
  return root;
}

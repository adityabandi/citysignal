import * as d3 from "npm:d3";
import { globalOverview } from "./global.js";
import { countryAssessment } from "./assessment.js";
import {
  el,
  txt as text,
  num as fmt,
  signed,
  button,
  select,
  field,
  nav,
  seriesOf,
  metricOf,
  link,
  table,
  download,
  formatPeriod,
  masthead,
} from "./desk.js";
const COLORS = [
  "#a4e6ce",
  "#e6ad78",
  "#b3a2e4",
  "#83c6ba",
  "#dd8faa",
  "#bfc37b",
  "#84c9d8",
];
const dateOf = (p) =>
  new Date(
    p.includes("-Q")
      ? `${p.slice(0, 4)}-${String(Number(p.at(-1)) * 3).padStart(2, "0")}-01T00:00:00Z`
      : `${p.length === 4 ? p + "-01-01" : p.length === 7 ? p + "-01" : p}T00:00:00Z`,
  );

export function countryDashboard(
  data,
  cities,
  loadCountry,
  loadScreener,
  loadUpdates,
) {
  const root = el("div", { class: "ec-app desk-app" }),
    loaded = new Set(),
    globalState = {};
  let renderVersion = 0;
  const params = new URLSearchParams(location.search);
  const state = {
    country: params.get("country") || "world",
    metric: params.get("metric") || "oecd_cli",
    years: 3,
    peers: new Set(),
  };
  function openCountry(code, metric = "oecd_cli") {
    state.country = code;
    state.metric = metric;
    state.peers.delete(code);
    const u = new URL(location.href);
    if (code === "world") {
      u.searchParams.delete("country");
      u.searchParams.delete("metric");
    } else {
      u.searchParams.set("country", code);
      u.searchParams.set("metric", metric);
    }
    history.pushState(null, "", u);
    render();
    window.scrollTo({ top: 0 });
  }
  window.addEventListener("popstate", () => {
    const p = new URLSearchParams(location.search);
    state.country = p.get("country") || "world";
    state.metric = p.get("metric") || "oecd_cli";
    render();
  });
  async function ensure(codes) {
    const details = await Promise.all(
      codes.filter((c) => !loaded.has(c)).map(loadCountry),
    );
    for (const c of details) {
      data.countries[data.countries.findIndex((old) => old.code === c.code)] =
        c;
      loaded.add(c.code);
    }
  }
  async function render() {
    const version = ++renderVersion;
    if (!data.countries.some((c) => c.code === state.country)) {
      root.replaceChildren(
        globalOverview(
          data,
          openCountry,
          loadScreener,
          globalState,
          loadUpdates,
        ),
      );
      return;
    }
    if (!loaded.has(state.country)) {
      root.replaceChildren(nav(), text("p", "Loading series…", "ec-empty"));
      try {
        await ensure([state.country]);
      } catch {
        if (version === renderVersion)
          root.replaceChildren(
            nav(),
            text("p", "Country history unavailable.", "ec-empty"),
            button("Retry", render),
          );
        return;
      }
      if (version !== renderVersion) return;
    }
    const country = data.countries.find((c) => c.code === state.country);
    const all = seriesOf(country).sort(
      (a, b) =>
        [
          "Cycle",
          "Growth",
          "Prices",
          "Monetary policy",
          "Markets",
          "Hiring",
          "Labour",
          "Trade",
          "Logistics",
          "Geopolitics",
          "Production",
          "Consumption",
          "Investment",
          "Payments",
          "Corporate payments",
          "Tax receipts",
          "Energy exposure",
          "Energy costs",
          "Business electricity",
          "Housing",
          "Financing",
          "Expectations",
          "Annual macro",
          "Structure",
        ].indexOf(a.group) -
          [
            "Cycle",
            "Growth",
            "Prices",
            "Monetary policy",
            "Markets",
            "Hiring",
            "Labour",
            "Trade",
            "Logistics",
            "Geopolitics",
            "Production",
            "Consumption",
            "Investment",
            "Payments",
            "Corporate payments",
            "Tax receipts",
            "Energy exposure",
            "Energy costs",
            "Business electricity",
            "Housing",
            "Financing",
            "Expectations",
            "Annual macro",
            "Structure",
          ].indexOf(b.group) || a.label.localeCompare(b.label),
    );
    if (!all.some((m) => m.id === state.metric))
      state.metric = all.find((m) => m.id === "bis_cpi")?.id || all[0]?.id;
    const countrySelect = select(
      "Select country",
      data.countries.map((c) => [c.code, c.name]),
      country.code,
    );
    countrySelect.onchange = () => openCountry(countrySelect.value);
    root.replaceChildren(
      masthead(data.as_of),
      nav(),
      el("header", { class: "desk-heading country-heading" }, [
        el("div", {}, [
          button("← All markets", () => openCountry("world"), "ec-link"),
          text("h1", country.name),
          text(
            "p",
            `${country.iso3} / ${country.region} · ${all.length} series`,
            "ec-muted",
          ),
        ]),
        field("Market", countrySelect),
      ]),
    );
    const search = el("input", {
      type: "search",
      class: "ec-search",
      placeholder: "Find series",
      "aria-label": "Find series",
    });
    const group = select("Series group", [
      ["", "All groups"],
      ...[...new Set(all.map((m) => m.group))].map((g) => [g, g]),
    ]);
    const kind = select("Series type", [
      ["", "All series"],
      ["official", "Official"],
      ["alternative", "Alternative"],
      ["derived", "Derived"],
    ]);
    const availability = select(
      "Observation coverage",
      [
        ["current", "Current observations"],
        ["all", "Include historical coverage"],
      ],
      all.find((m) => m.id === state.metric)?.historical_only
        ? "all"
        : "current",
    );
    const list = el("div", { class: "desk-series" }),
      detail = el("section", {
        class: "desk-detail",
        "aria-label": "Selected series",
      });
    const layout = el("div", { class: "desk-country-layout card-layout" }, [
      el("section", { class: "desk-series-panel" }, [
        el("div", { class: "ec-section-heading" }, [
          text("h2", "Indicators"),
          text("span", `${all.length} series`, "ec-muted"),
        ]),
        el("div", { class: "desk-series-controls" }, [
          search,
          group,
          kind,
          availability,
        ]),
        list,
      ]),
      detail,
    ]);
    root.append(
      countryAssessment(country, (id) => {
        state.metric = id;
        state.peers.clear();
        updateList();
        updateDetail();
        detail.scrollIntoView({ behavior: "instant", block: "start" });
      }),
      layout,
    );
    function updateList() {
      const shown = all.filter(
        (m) =>
          (!group.value || m.group === group.value) &&
          (!kind.value || m.signal_type === kind.value) &&
          (availability.value === "all" || !m.historical_only) &&
          `${m.label} ${m.group} ${m.source}`
            .toLowerCase()
            .includes(search.value.toLowerCase()),
      );
      list.replaceChildren(
        el(
          "div",
          { class: "ec-metrics" },
          shown.map((m) => {
            const alternative = m.signal_type !== "official";
            const card = button(
              "",
              () => {
                state.metric = m.id;
                state.peers.clear();
                updateList();
                updateDetail();
                detail.scrollIntoView({
                  behavior: window.matchMedia(
                    "(prefers-reduced-motion: reduce)",
                  ).matches
                    ? "instant"
                    : "smooth",
                  block: "start",
                });
              },
              `ec-metric ${alternative ? "is-alternative" : ""} ${state.metric === m.id ? "selected" : ""}`,
            );
            card.setAttribute("aria-label", m.label);
            card.setAttribute("aria-pressed", String(state.metric === m.id));
            card.append(
              el("div", { class: "ec-metric-top" }, [
                text("span", m.group),
                text("span", m.signal_type, "ec-tag"),
              ]),
              text("h3", m.label),
              text("strong", fmt(m.value, 1), "ec-metric-value"),
              text("span", m.unit, "ec-metric-unit"),
              spark(m.series, alternative ? COLORS[1] : COLORS[0]),
              el("div", { class: "ec-metric-foot" }, [
                text("span", `${formatPeriod(m.period)} · ${m.cadence}`),
                text("span", m.source),
              ]),
            );
            return card;
          }),
        ),
      );
      if (!shown.length)
        list.append(text("p", "No matching series.", "ec-empty"));
    }
    async function updateDetail() {
      const m = all.find((m) => m.id === state.metric);
      if (!m) return;
      const u = new URL(location.href);
      u.searchParams.set("metric", m.id);
      history.replaceState(null, "", u);
      const chart = el("div", { class: "desk-chart" });
      const header = el("div", { class: "desk-detail-heading" }, [
        el("div", {}, [
          text("span", `${m.group} / ${m.cadence}`, "ec-eyebrow"),
          text("h2", m.label),
        ]),
        button("Export series ↓", () =>
          download(
            `${country.code}-${m.id}.csv`,
            m.series.map((p) => ({
              country: country.name,
              metric_id: m.id,
              period: p.period,
              value: p.value,
              unit: m.unit,
              source: m.source,
              scope: m.scope,
              transform: m.transform,
              snapshot: data.as_of,
            })),
          ),
        ),
      ]);
      const ranges = el(
        "div",
        { class: "desk-tabs", "aria-label": "History range" },
        [
          [1, "1Y"],
          [3, "3Y"],
          [5, "5Y"],
          [100, "All"],
        ].map(([years, label]) => {
          const b = button(
            label,
            () => {
              state.years = years;
              updateDetail();
            },
            `ec-range ${state.years === years ? "active" : ""}`,
          );
          b.setAttribute("aria-pressed", String(state.years === years));
          return b;
        }),
      );
      const comparisonId =
        m.id === "oecd_euro_fx"
          ? "oecd_fx"
          : m.id === "bis_euro_policy_rate"
            ? "bis_policy_rate"
            : m.id;
      const peerOptions = data.countries.filter(
        (c) => c.code !== country.code && metricOf(c, comparisonId),
      );
      const peers = el("details", { class: "desk-comparison" }, [
        text(
          "summary",
          `Compare markets${state.peers.size ? ` (${state.peers.size})` : ""}`,
        ),
      ]);
      const peerGrid = el("div", { class: "desk-peer-grid" });
      for (const peer of peerOptions) {
        const cb = el("input", {
          type: "checkbox",
          "aria-label": `Compare ${peer.name}`,
        });
        cb.checked = state.peers.has(peer.code);
        cb.disabled = state.peers.size >= 6 && !cb.checked;
        cb.onchange = async () => {
          cb.checked
            ? state.peers.add(peer.code)
            : state.peers.delete(peer.code);
          peers.querySelector("summary").textContent =
            `Compare markets (${state.peers.size})`;
          for (const input of peerGrid.querySelectorAll("input"))
            input.disabled = state.peers.size >= 6 && !input.checked;
          await draw();
        };
        peerGrid.append(el("label", {}, [cb, text("span", peer.name)]));
      }
      peers.append(peerGrid);
      const observations = el("details", { class: "ec-method" }, [
        text("summary", `Observations (${m.series.length})`),
        table(
          ["Period", m.unit],
          m.series
            .slice()
            .reverse()
            .map((p) =>
              el("tr", {}, [
                text("td", formatPeriod(p.period)),
                text("td", fmt(p.value, 4)),
              ]),
            ),
        ),
      ]);
      const metadata = el("details", { class: "ec-method" }, [
        text("summary", "Definition & source"),
        text("p", m.description),
        table(
          ["Field", "Value"],
          Object.entries({
            Source: m.source,
            Scope: m.scope,
            Transformation: m.transform,
            "Source unit": m.raw_unit,
            "Observation period": m.period,
            "Capture date": m.fetched_at,
            "Publisher release": m.published_at,
            "Raw metric ID": m.raw_metric_id,
            "Signal family": m.family,
            "Historical percentile": m.historical_position
              ? `${m.historical_position.percentile} / 100 · ${m.historical_position.window} · ${m.historical_position.observations} preceding observations · ${m.historical_position.basis}`
              : null,
            "Source check": m.last_checked,
            "Observation status": m.quality,
            Freshness: m.freshness,
            License: m.license,
          }).map(([k, v]) =>
            el("tr", {}, [text("td", k), text("td", v || "—")]),
          ),
        ),
        link("Source documentation ↗", m.source_url),
      ]);
      detail.replaceChildren(
        header,
        el("div", { class: "desk-latest" }, [
          text("strong", fmt(m.value)),
          text("span", m.unit),
          text("span", formatPeriod(m.period), "ec-muted"),
          text(
            "span",
            m.change == null
              ? ""
              : `${signed(m.change)} ${m.change_unit} / ${m.change_window}`,
            "desk-delta",
          ),
        ]),
        text("p", `${m.source} · ${m.scope}`, "desk-source"),
        ranges,
        chart,
        peers,
        metadata,
        observations,
      );
      let drawVersion = 0;
      async function draw() {
        const dv = ++drawVersion;
        const codes = [...state.peers];
        try {
          await ensure(codes);
        } catch {
          chart.replaceChildren(
            text("p", "Comparison history unavailable."),
            button("Retry", draw),
          );
          return;
        }
        if (dv !== drawVersion || !detail.contains(chart)) return;
        const lines = [
          { name: country.name, points: m.series },
          ...codes.map((code) => {
            const c = data.countries.find((c) => c.code === code);
            return {
              name: c.name,
              points: metricOf(c, comparisonId)?.series || [],
            };
          }),
        ];
        chart.replaceChildren(
          historyChart(lines, m.unit, state.years, chart.clientWidth || 680),
        );
      }
      await draw();
    }
    search.oninput = updateList;
    group.onchange = updateList;
    kind.onchange = updateList;
    availability.onchange = updateList;
    updateList();
    updateDetail();
    if (country.cities.length) {
      root.append(
        el("details", { class: "desk-regional" }, [
          text(
            "summary",
            `Regional coverage · ${country.cities.length} cities`,
          ),
          el(
            "div",
            { class: "desk-region-links" },
            country.cities.map((c) => link(c.name, `./cities/${c.slug}`)),
          ),
        ]),
      );
    }
  }
  render();
  return root;
}

function spark(points, color) {
  const values = points.slice(-36);
  const svg = d3
    .create("svg")
    .attr("viewBox", "0 0 180 38")
    .attr("class", "ec-spark")
    .attr("aria-hidden", "true");
  if (values.length < 2) return svg.node();
  const x = d3.scaleUtc(
    d3.extent(values, (d) => dateOf(d.period)),
    [1, 179],
  );
  const extent = d3.extent(values, (d) => d.value);
  if (extent[0] === extent[1]) {
    extent[0] -= 1;
    extent[1] += 1;
  }
  const y = d3.scaleLinear(extent, [34, 4]);
  svg
    .append("path")
    .attr(
      "d",
      d3
        .line()
        .x((d) => x(dateOf(d.period)))
        .y((d) => y(d.value))(values),
    )
    .attr("fill", "none")
    .attr("stroke", color)
    .attr("stroke-width", 1.6);
  return svg.node();
}

export function historyChart(series, unit, years = 3, width = 680) {
  const end = d3.max(
    series.flatMap((s) => s.points),
    (d) => dateOf(d.period),
  );
  if (!end) return text("p", "No history available.", "ec-empty");
  const start = new Date(end);
  start.setUTCFullYear(start.getUTCFullYear() - years);
  const shown = series.map((s) => ({
    ...s,
    points: s.points.filter((p) => dateOf(p.period) >= start),
  }));
  const points = shown.flatMap((s) => s.points),
    w = Math.max(300, Math.min(1000, width)),
    h = 280,
    margin = { left: 55, right: 22, top: 24, bottom: 35 };
  const x = d3.scaleUtc(
    d3.extent(points, (d) => dateOf(d.period)),
    [margin.left, w - margin.right],
  );
  const extent = d3.extent(points, (d) => d.value);
  if (extent[0] === extent[1]) {
    extent[0] -= 1;
    extent[1] += 1;
  }
  const y = d3.scaleLinear(extent, [h - margin.bottom, margin.top]).nice();
  const svg = d3
    .create("svg")
    .attr("viewBox", `0 0 ${w} ${h}`)
    .attr("class", "ec-history-chart")
    .attr("role", "img")
    .attr(
      "aria-label",
      `${unit}. ${shown.map((s) => s.name).join(", ")} time series.`,
    );
  svg
    .append("title")
    .text(
      `${unit}; latest ${series[0].points.at(-1)?.period}. Observation table available.`,
    );
  svg
    .append("g")
    .attr("transform", `translate(${margin.left},0)`)
    .call(
      d3
        .axisLeft(y)
        .ticks(5)
        .tickSize(-(w - margin.left - margin.right))
        .tickFormat(d3.format("~g")),
    )
    .call((g) => g.select(".domain").remove());
  svg
    .append("g")
    .attr("transform", `translate(0,${h - margin.bottom})`)
    .call(
      d3
        .axisBottom(x)
        .ticks(w < 500 ? 3 : 6)
        .tickSize(0),
    )
    .call((g) => g.select(".domain").remove());
  if (y.domain()[0] < 0 && y.domain()[1] > 0)
    svg
      .append("line")
      .attr("x1", margin.left)
      .attr("x2", w - margin.right)
      .attr("y1", y(0))
      .attr("y2", y(0))
      .attr("stroke", "#7c8986")
      .attr("stroke-dasharray", "3 5");
  shown.forEach((s, i) => {
    // Separate paths at missing scheduled observations rather than joining gaps.
    const segments = [];
    let segment = [];
    for (const p of s.points) {
      const prev = segment.at(-1);
      const days = prev
        ? (dateOf(p.period) - dateOf(prev.period)) / 86400000
        : 0;
      const maxGap = p.period.includes("Q")
        ? 100
        : p.period.length === 7
          ? 35
          : p.period.length === 4
            ? 370
            : 2;
      if (prev && days > maxGap) {
        segments.push(segment);
        segment = [];
      }
      segment.push(p);
    }
    segments.push(segment);
    for (const part of segments)
      svg
        .append("path")
        .attr(
          "d",
          d3
            .line()
            .x((d) => x(dateOf(d.period)))
            .y((d) => y(d.value))(part),
        )
        .attr("fill", "none")
        .attr("stroke", COLORS[i % COLORS.length])
        .attr("stroke-width", i ? 1.6 : 2.8);
    if (s.points.length) {
      const last = s.points.at(-1);
      svg
        .append("circle")
        .attr("cx", x(dateOf(last.period)))
        .attr("cy", y(last.value))
        .attr("r", 3)
        .attr("fill", COLORS[i % COLORS.length]);
    }
  });
  const wrap = el("div", { class: "ec-chart-wrap" });
  const readout = text("div", `${unit}`, "ec-chart-readout");
  svg.on("pointermove", function (event) {
    const [px] = d3.pointer(event, this);
    const at = x.invert(px);
    const primary = shown[0].points;
    if (!primary.length) return;
    const p = d3.least(primary, (p) => Math.abs(dateOf(p.period) - at));
    readout.textContent = `${shown[0].name} · ${formatPeriod(p.period)} · ${fmt(p.value, 2)} ${unit}`;
  });
  wrap.append(
    readout,
    svg.node(),
    el(
      "div",
      { class: "ec-legend" },
      shown.map((s, i) =>
        el("span", {}, [
          el("i", { style: `background:${COLORS[i % COLORS.length]}` }),
          document.createTextNode(s.name),
        ]),
      ),
    ),
  );
  return wrap;
}

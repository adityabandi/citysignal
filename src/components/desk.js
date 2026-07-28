// The desk: an official tape in real units, and unconventional signals as a
// 0-100 diffusion index.
//
// The split is the whole idea. A mortgage rate means something as 2.85% and
// nothing as a score, so the tape keeps its units. A food-POI share of 0.38
// means nothing to anybody, so it becomes a percentile against the city's own
// history — 63 reads as "higher than 63% of everything this city has recorded".
//
// Ticker codes are a display convention, not data, but a stable one: somebody
// who learns OSM-FOOD should find it in the same place next month.

import * as Plot from "npm:@observablehq/plot";
import {el, formatPeriod, formatValue, formatDelta, deltaArrow, periodToDate} from "./theme.js";

// Terminal green, used only on this page and only for state. Sits on the same
// dark surface as the rest of the site so the desk reads as a room in the same
// building rather than a different product.
export const DESK = {
  up: "#3ddc84",
  down: "#e8674f",
  flat: "#8b8f96",
  glow: "rgba(61, 220, 132, 0.10)"
};

const READ_COLOR = {
  expansionary: DESK.up,
  contractionary: DESK.down,
  neutral: DESK.flat,
  unknown: DESK.flat
};

export function tapeStrip(tape) {
  return el(
    "div",
    {class: "cs-tape"},
    tape.map((item) =>
      el("div", {class: "cs-tape-item", title: item.plain ?? ""}, [
        el("span", {class: "cs-tape-ticker", text: item.ticker}),
        el("span", {class: "cs-tape-value cs-num", text: formatValue(item.value, item.unit)}),
        item.yoy == null
          ? null
          : el("span", {
              class: "cs-tape-delta cs-num",
              text: `${deltaArrow(item.yoy)} ${formatDelta(item.yoy)}`
            }),
        el("span", {class: "cs-tape-period", text: formatPeriod(item.period)})
      ])
    )
  );
}

function gauge(index, color) {
  // A 0-100 bar with the neutral mark at 50, so "unusual" is visible as
  // distance from the middle rather than as a number to be interpreted.
  return el("div", {class: "cs-gauge"}, [
    el("div", {
      class: "cs-gauge-fill",
      style: `width:${Math.max(1, index)}%;background:${color}`
    }),
    el("div", {class: "cs-gauge-mid"})
  ]);
}

export function signalCard(signal, {onSelect, selected} = {}) {
  const color = READ_COLOR[signal.read] ?? DESK.flat;
  const points = (signal.series ?? [])
    .filter((d) => d.value != null)
    .map((d) => ({date: periodToDate(d.period), value: d.value}));

  const spark = points.length > 1
    ? Plot.plot({
        width: 260,
        height: 38,
        margin: 0,
        marginTop: 3,
        marginBottom: 3,
        axis: null,
        style: {background: "transparent", overflow: "visible"},
        marks: [
          Plot.areaY(points, {x: "date", y: "value", fill: color, fillOpacity: 0.14, curve: "monotone-x"}),
          Plot.lineY(points, {x: "date", y: "value", stroke: color, strokeWidth: 1.6, curve: "monotone-x"})
        ]
      })
    : el("div", {style: "height:38px"});

  const delta = signal.index_prev == null ? null : signal.index - signal.index_prev;

  return el(
    "div",
    {
      class: `cs-signal${selected ? " cs-signal-on" : ""}`,
      style: `--signal:${color}`,
      onclick: onSelect
    },
    [
      el("div", {class: "cs-signal-head"}, [
        el("span", {class: "cs-signal-ticker", text: signal.ticker}),
        el("span", {class: "cs-signal-scope", text: signal.geo_level})
      ]),
      el("div", {class: "cs-signal-name", text: signal.label}),
      el("div", {class: "cs-signal-figure"}, [
        el("span", {class: "cs-num cs-signal-index", text: String(signal.index ?? "—")}),
        el("span", {class: "cs-signal-outof", text: "/100"}),
        delta == null
          ? null
          : el("span", {
              class: "cs-num cs-signal-delta",
              text: `${delta > 0 ? "+" : ""}${delta} m/m`
            })
      ]),
      gauge(signal.index ?? 0, color),
      spark,
      el("div", {class: "cs-signal-read"}, [
        document.createTextNode(signal.read),
        signal.inverted
          ? el("span", {
              class: "cs-signal-inv",
              title:
                `This measure is inverted: a rising raw value is bad news, so the score is flipped ` +
                `to keep high meaning expansionary. Raw percentile ${signal.percentile_raw}.`,
              text: " · inverted"
            })
          : null
      ]),
      signal.plain ? el("div", {class: "cs-signal-why", text: signal.plain}) : null
    ]
  );
}

export function compositeHeadline(desk, sentence) {
  const value = desk.composite;
  const color = value == null ? DESK.flat : value >= 60 ? DESK.up : value <= 40 ? DESK.down : DESK.flat;
  return el("div", {class: "cs-desk-hero", style: `--signal:${color}`}, [
    el("div", {class: "cs-kicker", text: `${desk.name} · unconventional composite`}),
    el("div", {class: "cs-desk-hero-text", text: sentence}),
    el("div", {class: "cs-desk-hero-meta"}, [
      el("span", {class: "cs-num cs-desk-hero-figure", text: String(value ?? "—")}),
      el("span", {text: `/ 100 · ${desk.composite_n} signals, equal weight · 50 is typical for this city`})
    ])
  ]);
}

// Written from the data rather than chosen, so the sentence cannot flatter the
// numbers. Deliberately says "busier", not "better": whether a hotter city is
// good news depends entirely on whether you are buying or selling.
export function describeComposite(desk) {
  const value = desk.composite;
  if (value == null) return "Not enough unconventional signals are reporting to compose a reading.";

  const hot = desk.signals.filter((s) => s.read === "expansionary").map((s) => s.label);
  const cold = desk.signals.filter((s) => s.read === "contractionary").map((s) => s.label);
  const lead = hot.length >= cold.length ? hot : cold;
  const naming = lead.slice(0, 2).join(" and ").toLowerCase();

  const band =
    value >= 70 ? "running hot against its own record"
    : value >= 60 ? "busier than usual for itself"
    : value >= 40 ? "close to its own normal"
    : value >= 30 ? "quieter than usual for itself"
    : "well below its own normal";

  return `${desk.name} is ${band}. ${
    lead.length ? `The strongest readings come from ${naming}.` : ""
  } These are side-door measures — restaurant density, search behaviour, attention by language — and they are scored against this city's own past, not against other cities.`;
}

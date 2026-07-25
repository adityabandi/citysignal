// Shared tokens and formatters. Colour lives here so a city's hue and a
// regime's status colour are decided in exactly one place and stay stable
// across every page — a reader who learns "Barcelona is orange" is never
// re-taught it by a filter or a different chart.

export const SURFACE = "#0c0d10";
export const GRID = "#1c1e24";
export const RULE = "#24272e";
export const INK = "#ffffff";
export const INK_2 = "#c3c2b7";
export const INK_3 = "#898781";
export const INK_4 = "#5f5e59";

// Validated categorical order (dark surface #0c0d10): all eight slots clear the
// lightness band, chroma floor, adjacent CVD separation (worst ΔE 8.4), the
// normal-vision floor (worst ΔE 19.3) and 3:1 contrast.
export const CITY_COLORS = {
  madrid: "#3987e5",
  barcelona: "#d95926",
  valencia: "#199e70",
  malaga: "#c98500",
  sevilla: "#d55181",
  palma: "#008300",
  bilbao: "#9085e9",
  zaragoza: "#e66767"
};

export const cityColor = (slug) => CITY_COLORS[slug] ?? INK_3;

// Regime is a state, not a series: it uses the reserved status palette and is
// always drawn with a glyph and a word beside it, never colour alone.
// Ordered by severity, not alphabetically, so the legend reads as a scale.
// Two states may share a hue where they are the same family (heat, or crisis) —
// the glyph and the word always travel with the colour, so nothing is carried by
// hue alone. Any regime the rules can emit must appear here, or it falls through
// to neutral and a real state is rendered as "nothing happening".
export const REGIMES = {
  expansion: {label: "Expansion", color: "#0ca30c", glyph: "▲", rank: 0},
  orderly_cooling: {label: "Orderly cooling", color: "#898781", glyph: "▽", rank: 0.5},
  affordability_improving: {label: "Easing", color: "#0ca30c", glyph: "▼", rank: 1},
  supply_catching_up: {label: "Supply catching up", color: "#0ca30c", glyph: "▼", rank: 2},
  cooling_reversing: {label: "Cheap but tightening", color: "#898781", glyph: "△", rank: 3},
  hot: {label: "Hot", color: "#fab219", glyph: "◆", rank: 4},
  hot_decelerating: {label: "Hot but decelerating", color: "#fab219", glyph: "◇", rank: 5},
  supply_squeeze: {label: "Pressure without supply", color: "#ec835a", glyph: "◉", rank: 6},
  stress: {label: "Stress", color: "#d03b3b", glyph: "✖", rank: 7},
  dislocation: {label: "Dislocation", color: "#d03b3b", glyph: "‼", rank: 8},
  neutral: {label: "No clear regime", color: "#6e6e6b", glyph: "·", rank: 9}
};

export const REGIME_ORDER = Object.entries(REGIMES)
  .sort((a, b) => a[1].rank - b[1].rank)
  .map(([id]) => id);

export const regime = (id) => REGIMES[id] ?? REGIMES.neutral;

export const FRESHNESS = {
  fresh: {label: "fresh", className: "cs-fresh"},
  stale: {label: "stale", className: "cs-stale"},
  failing: {label: "not updating", className: "cs-failing"},
  unknown: {label: "unknown", className: "cs-unknown"}
};

const UNIT_SUFFIX = {
  percent: "%",
  per_million: " per million",
  eur: " €",
  eur_m2: " €/m²",
  eur_m2_month: " €/m²/mo",
  index: "",
  tone: ""
};

export function formatValue(value, unit) {
  if (value == null || Number.isNaN(value)) return "—";
  const suffix = UNIT_SUFFIX[unit] ?? "";
  const magnitude = Math.abs(value);
  let text;
  if (magnitude >= 1_000_000) text = (value / 1_000_000).toFixed(2) + "M";
  else if (magnitude >= 10_000) text = Math.round(value).toLocaleString("en-GB");
  else if (magnitude >= 100) text = Math.round(value).toLocaleString("en-GB");
  else if (magnitude >= 10) text = value.toFixed(1);
  else text = value.toFixed(2);
  return text + suffix;
}

export function formatDelta(value) {
  if (value == null || Number.isNaN(value)) return null;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

// Deliberately NOT coloured good-or-bad.
//
// An earlier version coloured a change green when it moved the way the metric's
// `direction` called "hotter". That renders rising rents green, which tells a
// renter that their problem is good news. Whether a rise is welcome depends
// entirely on who is reading — owner or tenant, seller or buyer — and that is
// not a question this project has any standing to answer.
//
// So the delta shows magnitude and an arrow for sense, in neutral ink. Status
// colour is reserved for the regime chips and for source health, where it means
// a defined state rather than an opinion.
export function deltaClass() {
  return "cs-delta-neutral";
}

export function deltaArrow(value) {
  if (value == null || Math.abs(value) < 0.05) return "→";
  return value > 0 ? "↑" : "↓";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatPeriod(period) {
  if (!period) return "—";
  if (/^\d{4}$/.test(period)) return period;
  if (/^\d{4}-Q[1-4]$/.test(period)) return period.replace("-", " ");
  const [year, month] = period.split("-");
  if (month && MONTHS[+month - 1]) return `${MONTHS[+month - 1]} ${year}`;
  return period;
}

// Periods are strings by design (2026-Q2, 2026-03, 2026); charts need a date.
export function periodToDate(period) {
  if (/^\d{4}$/.test(period)) return new Date(Date.UTC(+period, 6, 1));
  const quarter = period.match(/^(\d{4})-Q([1-4])$/);
  if (quarter) return new Date(Date.UTC(+quarter[1], (+quarter[2] - 1) * 3 + 1, 1));
  const [year, month] = period.split("-");
  return new Date(Date.UTC(+year, +month - 1, 15));
}

// "+1.25 σ" is not a fact about a city, it is a fact about a distribution. These
// turn the statistics back into the sentence a person would actually say.

export function describeZ(z, {noun = "This"} = {}) {
  if (z == null) return "Not enough history yet to judge what is normal here.";
  const magnitude = Math.abs(z);
  const side = z > 0 ? "higher" : "lower";
  if (magnitude < 0.5) return `${noun} is about normal for this city.`;
  if (magnitude < 1) return `${noun} is a little ${side} than normal for this city.`;
  if (magnitude < 2) return `${noun} is clearly ${side} than this city's own record.`;
  if (magnitude < 3) return `${noun} is far ${side} than anything usual for this city.`;
  return `${noun} is at an extreme against this city's own history.`;
}

// The same scale, compressed to a chip.
export function shortZ(z) {
  if (z == null) return "no reading";
  const magnitude = Math.abs(z);
  const side = z > 0 ? "high" : "low";
  if (magnitude < 0.5) return "normal";
  if (magnitude < 1) return `slightly ${side}`;
  if (magnitude < 2) return `clearly ${side}`;
  if (magnitude < 3) return `very ${side}`;
  return `extreme ${side}`;
}

export const INDEX_PLAIN = {
  demand_momentum: {
    high: "More people are arriving, working and buying than usual here.",
    low: "Fewer people are arriving, working and buying than usual here.",
    flat: "Arrivals, jobs and purchases are running at about the usual pace."
  },
  housing_pressure: {
    high: "Housing costs more, and is fought over harder, than is normal here.",
    low: "The squeeze on housing has eased below what is normal here.",
    flat: "Housing is about as tight as it usually is here."
  },
  supply_response: {
    high: "Building is running ahead of its usual pace here.",
    low: "Building has slowed below its usual pace here.",
    flat: "Building is running at about its usual pace."
  },
  distress: {
    high: "More people are losing jobs, homes or businesses than is normal here.",
    low: "Fewer people are losing jobs, homes or businesses than is normal here.",
    flat: "Job, home and business losses are at about their usual level."
  }
};

export function describeIndex(indexId, value) {
  if (value == null) return "Not enough of this index's inputs are reporting yet.";
  const copy = INDEX_PLAIN[indexId];
  if (!copy) return describeZ(value);
  if (value > 0.5) return copy.high;
  if (value < -0.5) return copy.low;
  return copy.flat;
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child == null) continue;
    node.append(child);
  }
  return node;
}

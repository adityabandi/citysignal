// The metric card is where the product's honesty rules become visible.
// Every card states four things a normal dashboard hides: the geography the
// number was actually published at, when the observation is from, when we last
// asked the publisher, and whether the source is official, research or
// commercial. If any of those is missing, the card says so rather than
// implying freshness it cannot support.

import * as Plot from "npm:@observablehq/plot";
import {
  FRESHNESS,
  GRID,
  INK_3,
  cityColor,
  deltaClass,
  el,
  formatDelta,
  formatPeriod,
  formatValue,
  periodToDate
} from "./theme.js";

export function sparkline(series, {color = "#3987e5", width = 236, height = 34, direction = 1} = {}) {
  const points = (series ?? [])
    .filter((d) => d.value != null)
    .map((d) => ({date: periodToDate(d.period), value: d.value, period: d.period}));
  if (points.length < 2) return el("div", {style: `height:${height}px`});

  const last = points.at(-1);
  return Plot.plot({
    width,
    height,
    margin: 0,
    marginTop: 4,
    marginBottom: 4,
    axis: null,
    style: {background: "transparent", overflow: "visible"},
    marks: [
      Plot.areaY(points, {
        x: "date",
        y: "value",
        fill: color,
        fillOpacity: 0.12,
        curve: "monotone-x"
      }),
      Plot.lineY(points, {
        x: "date",
        y: "value",
        stroke: color,
        strokeWidth: 2,
        curve: "monotone-x"
      }),
      // One direct label only: the latest point, which is what the card is about.
      Plot.dot([last], {x: "date", y: "value", fill: color, r: 3, stroke: "#0c0d10", strokeWidth: 2})
    ]
  });
}

function scopePill(card) {
  // A wider-than-city geography is flagged, because that is the substitution a
  // reader is most likely to make without noticing.
  const wider = !card.is_city_level;
  return el("span", {
    class: `cs-scope${wider ? " cs-scope-wider" : ""}`,
    text: card.scope_label,
    title: wider
      ? `Published at ${card.scope_label} level (${card.geo_id}) — the closest available evidence, not a municipal measure.`
      : `Published for the municipality itself (${card.geo_id}).`
  });
}

function freshnessTag(card) {
  const state = FRESHNESS[card.fresh] ?? FRESHNESS.unknown;
  const checked = card.last_checked ? card.last_checked.slice(0, 10) : "never";
  return el("span", {
    class: `cs-freshness ${state.className}`,
    text: state.label,
    title:
      `Observation covers ${formatPeriod(card.latest.period)} ` +
      `(${card.staleness_days} days old). Publisher last checked ${checked}.`
  });
}

export function metricCard(card, {color} = {}) {
  const hue = color ?? "#3987e5";
  const delta = formatDelta(card.latest.yoy);

  const head = el("div", {class: "cs-card-head"}, [
    el("span", {class: "cs-card-label", text: card.label, title: card.plain ?? ""}),
    delta
      ? el("span", {
          class: `cs-delta ${deltaClass(card.latest.yoy, card.direction)}`,
          text: delta,
          title: "Change against the same period a year earlier"
        })
      : null
  ]);

  const value = el("div", {class: "cs-card-value"}, [
    document.createTextNode(formatValue(card.latest.value, card.unit)),
    ["percent", "index", "tone", "per_million"].includes(card.unit)
      ? null
      : el("span", {class: "cs-card-unit", text: card.unit.replaceAll("_", " ")})
  ]);

  const meta = el("div", {class: "cs-meta"}, [
    scopePill(card),
    freshnessTag(card),
    card.suspect
      ? el("span", {
          class: "cs-scope cs-scope-wider",
          text: "provisional",
          title: card.suspect_note ?? "Latest release treated as provisional."
        })
      : null,
    el("span", {
      class: `cs-kind cs-kind-${card.source.kind ?? "official"}`,
      text: card.source.kind ?? "official"
    }),
    el("span", {
      text: `${formatPeriod(card.latest.period)} · ${card.source_id}`,
      title: card.source.attribution ?? card.source_id
    })
  ]);

  return el("div", {class: "cs-panel"}, [
    head,
    // What the measure actually is, in plain words, before any number is read.
    card.plain ? el("div", {class: "cs-plain", text: card.plain}) : null,
    value,
    sparkline(card.series, {color: hue, direction: card.direction}),
    meta,
    card.suspect_note
      ? el("div", {class: "cs-note", style: "margin-top:.5rem;color:var(--cs-hot)", text: card.suspect_note})
      : null,
    card.note ? el("div", {class: "cs-note", style: "margin-top:.5rem", text: card.note}) : null
  ]);
}

export function cardGrid(cards, {color} = {}) {
  if (!cards?.length) {
    return el("div", {
      class: "cs-empty",
      text: "No series available for this section yet. When an adapter for it starts reporting, cards appear here automatically."
    });
  }
  return el("div", {class: "cs-grid"}, cards.map((card) => metricCard(card, {color})));
}

export function regimeBadge(regimeInfo) {
  const {regime} = {regime: regimeInfo};
  return el("span", {class: `cs-regime regime-${regime.rule_id}`}, [
    el("span", {class: "cs-glyph", text: regimeGlyph(regime.rule_id)}),
    document.createTextNode(regime.label)
  ]);
}

function regimeGlyph(id) {
  return {
    expansion: "▲",
    hot_decelerating: "◆",
    orderly_cooling: "▽",
    stress: "◉",
    dislocation: "✖",
    neutral: "·"
  }[id] ?? "·";
}

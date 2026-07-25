// Vital-signs ribbons: eight cities, one line each, read top to bottom.
//
// The band behind each trace is the regime the rules assigned at that moment, so
// a run of amber turning to orange is visible before you read a single number.
// The trace itself is the demand-momentum index. Regime is a state and uses the
// reserved status palette; the trace is identity and uses the city's own hue —
// the two never borrow each other's colours.

import * as Plot from "npm:@observablehq/plot";
import {GRID, INK_3, INK_4, RULE, SURFACE, cityColor, el, formatPeriod, periodToDate, regime} from "./theme.js";

export function ribbon(city, {width = 520, height = 46, index = "demand_momentum"} = {}) {
  const timeline = (city.timeline ?? []).filter((d) => d.period);
  if (timeline.length < 2) {
    return el("div", {
      class: "cs-note",
      style: `height:${height}px;display:flex;align-items:center`,
      text: "Not enough history yet to classify."
    });
  }

  const bands = timeline.map((d, i) => ({
    start: periodToDate(d.period),
    end: periodToDate(timeline[i + 1]?.period ?? d.period),
    rule_id: d.rule_id,
    label: regime(d.rule_id).label,
    period: d.period
  }));

  const trace = timeline
    .filter((d) => d[`index_${index}`] != null)
    .map((d) => ({date: periodToDate(d.period), value: d[`index_${index}`], period: d.period}));

  const hue = cityColor(city.slug);

  return Plot.plot({
    width,
    height,
    marginLeft: 0,
    marginRight: 0,
    marginTop: 3,
    marginBottom: 3,
    style: {background: "transparent", overflow: "visible"},
    x: {type: "utc", axis: null},
    y: {domain: [-2.5, 2.5], axis: null},
    marks: [
      Plot.rect(bands, {
        x1: "start",
        x2: "end",
        y1: -2.5,
        y2: 2.5,
        fill: (d) => regime(d.rule_id).color,
        fillOpacity: 0.22,
        title: (d) => `${formatPeriod(d.period)} — ${d.label}`
      }),
      Plot.ruleY([0], {stroke: INK_4, strokeWidth: 1, strokeOpacity: 0.6}),
      trace.length > 1
        ? Plot.lineY(trace, {
            x: "date",
            y: "value",
            stroke: hue,
            strokeWidth: 2,
            curve: "monotone-x",
            tip: false
          })
        : null,
      trace.length
        ? Plot.dot([trace.at(-1)], {
            x: "date",
            y: "value",
            fill: hue,
            r: 3.5,
            stroke: SURFACE,
            strokeWidth: 2
          })
        : null,
      Plot.tip(
        trace,
        Plot.pointerX({
          x: "date",
          y: "value",
          fill: SURFACE,
          stroke: RULE,
          fontSize: 11,
          title: (d) => `${formatPeriod(d.period)}\nDemand momentum ${d.value.toFixed(2)} σ`
        })
      )
    ]
  });
}

export function regimeLegend() {
  const items = [
    "expansion",
    "hot_decelerating",
    "orderly_cooling",
    "stress",
    "dislocation",
    "neutral"
  ].map((id) => {
    const meta = regime(id);
    return el("span", {
      style:
        "display:inline-flex;align-items:center;gap:.35rem;font-family:var(--cs-mono);" +
        "font-size:.62rem;letter-spacing:.09em;text-transform:uppercase;color:var(--cs-ink-3)"
    }, [
      el("span", {
        style: `width:9px;height:9px;border-radius:1px;background:${meta.color};opacity:.45;flex:none`
      }),
      el("span", {text: `${meta.glyph} ${meta.label}`})
    ]);
  });

  return el("div", {style: "display:flex;flex-wrap:wrap;gap:.5rem 1rem;margin:.6rem 0 1rem"}, items);
}

// A compact multi-city comparison of one index, drawn as small multiples rather
// than eight lines in one frame: with eight series a single frame becomes a
// spaghetti plot no reader can follow.
export function indexTrace(cities, indexId, {width = 240, height = 90} = {}) {
  return cities.map((city) => {
    const points = (city.timeline ?? [])
      .filter((d) => d[`index_${indexId}`] != null)
      .map((d) => ({date: periodToDate(d.period), value: d[`index_${indexId}`], period: d.period}));

    const chart =
      points.length > 1
        ? Plot.plot({
            width,
            height,
            marginLeft: 26,
            marginRight: 8,
            marginTop: 8,
            marginBottom: 18,
            style: {background: "transparent"},
            x: {type: "utc", ticks: 3, tickSize: 0, label: null},
            y: {domain: [-2.5, 2.5], ticks: [-2, 0, 2], tickSize: 0, label: null, grid: true},
            color: {legend: false},
            marks: [
              Plot.ruleY([0], {stroke: INK_4}),
              Plot.areaY(points, {
                x: "date",
                y: "value",
                fill: cityColor(city.slug),
                fillOpacity: 0.14,
                curve: "monotone-x"
              }),
              Plot.lineY(points, {
                x: "date",
                y: "value",
                stroke: cityColor(city.slug),
                strokeWidth: 2,
                curve: "monotone-x"
              }),
              Plot.tip(
                points,
                Plot.pointerX({
                  x: "date",
                  y: "value",
                  fill: SURFACE,
                  stroke: RULE,
                  fontSize: 11,
                  title: (d) => `${formatPeriod(d.period)}\n${d.value.toFixed(2)} σ`
                })
              )
            ]
          })
        : el("div", {class: "cs-note", style: `height:${height}px`, text: "No history yet."});

    return el("div", {class: "cs-panel"}, [
      el("div", {
        style: "display:flex;align-items:center;gap:.4rem;margin-bottom:.3rem"
      }, [
        el("span", {
          style: `width:8px;height:8px;border-radius:1px;background:${cityColor(city.slug)};flex:none`
        }),
        el("span", {class: "cs-card-label", text: city.name})
      ]),
      chart
    ]);
  });
}

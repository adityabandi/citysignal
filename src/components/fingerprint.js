// The signature fingerprint.
//
// Each spoke is one declared signal of an archetype. The dashed ring is where
// "average for this city" sits; a point outside it means the measure is running
// hot against its own history, inside means cold. The faint dashed polygon is
// the archetype's *expected shape* — what a city in that state should look like
// — so the reader is comparing a shape to a shape rather than reading a score.
//
// The point is to make disagreement visible. A city that matches an archetype on
// fourteen signals and contradicts it on five looks like exactly that here, which
// a single percentage could never show.

import * as d3 from "npm:d3";
import {INK_3, INK_4, RULE, SURFACE, el, formatPeriod, regime} from "./theme.js";

const Z_MAX = 3;

export function fingerprint(signature, {size = 330, color = "#3987e5", threshold = 0.5} = {}) {
  const signals = [...(signature.signals_firing ?? []), ...(signature.signals_against ?? [])];
  if (signals.length < 3) {
    return el("div", {
      class: "cs-empty",
      text: `Not enough of the ${signature.label.toLowerCase()} signals are available yet to draw a shape.`
    });
  }

  const firingIds = new Set((signature.signals_firing ?? []).map((s) => s.metric_id));
  // Stable ordering so the same city keeps the same shape between builds.
  const ordered = [...signals].sort((a, b) => a.metric_id.localeCompare(b.metric_id));

  const outer = size / 2 - 16;
  const inner = 26;
  const rScale = d3.scaleLinear().domain([-Z_MAX, Z_MAX]).range([inner, outer]).clamp(true);
  const zeroR = rScale(0);
  const angle = (i) => (i / ordered.length) * 2 * Math.PI - Math.PI / 2;
  const point = (i, z) => [Math.cos(angle(i)) * rScale(z), Math.sin(angle(i)) * rScale(z)];

  const svg = d3
    .create("svg")
    .attr("viewBox", [-size / 2, -size / 2, size, size])
    .attr("width", size)
    .attr("height", size)
    .attr("role", "img")
    .attr(
      "aria-label",
      `${signature.label} signature: ${signature.firing} of ${signature.available} available signals firing`
    )
    .style("max-width", "100%")
    .style("height", "auto")
    .style("overflow", "visible");

  // Rings: the mean, and ±1 standard deviation for scale.
  for (const z of [-2, -1, 1, 2]) {
    svg
      .append("circle")
      .attr("r", rScale(z))
      .attr("fill", "none")
      .attr("stroke", RULE)
      .attr("stroke-width", 1);
  }
  svg
    .append("circle")
    .attr("r", zeroR)
    .attr("fill", "none")
    .attr("stroke", INK_4)
    .attr("stroke-width", 1)
    .attr("stroke-dasharray", "2 3");

  for (const [i] of ordered.entries()) {
    const [x, y] = point(i, Z_MAX);
    svg
      .append("line")
      .attr("x1", 0)
      .attr("y1", 0)
      .attr("x2", x)
      .attr("y2", y)
      .attr("stroke", RULE)
      .attr("stroke-width", 1);
  }

  const line = d3
    .lineRadial()
    .angle((_, i) => angle(i) + Math.PI / 2)
    .radius((d) => rScale(d))
    .curve(d3.curveLinearClosed);

  // The archetype's expected shape, at the firing threshold in each signal's
  // stated direction.
  const ghost = ordered.map((s) => (s.expect === "up" ? threshold : -threshold));
  svg
    .append("path")
    .attr("d", line(ghost))
    .attr("fill", "none")
    .attr("stroke", INK_3)
    .attr("stroke-width", 1.5)
    .attr("stroke-dasharray", "4 4")
    .attr("opacity", 0.75);

  const actual = ordered.map((s) => Math.max(-Z_MAX, Math.min(Z_MAX, s.z)));
  svg
    .append("path")
    .attr("d", line(actual))
    .attr("fill", color)
    .attr("fill-opacity", 0.16)
    .attr("stroke", color)
    .attr("stroke-width", 2)
    .attr("stroke-linejoin", "round");

  for (const [i, signal] of ordered.entries()) {
    const isFiring = firingIds.has(signal.metric_id);
    const [x, y] = point(i, actual[i]);
    const marker = svg
      .append("circle")
      .attr("cx", x)
      .attr("cy", y)
      .attr("r", isFiring ? 4.5 : 3)
      .attr("fill", isFiring ? color : SURFACE)
      .attr("stroke", isFiring ? SURFACE : INK_3)
      .attr("stroke-width", 2);

    marker.append("title").text(
      `${signal.label}\n` +
        `${isFiring ? "matches" : "does not match"} — expected ${signal.expect}, ` +
        `standing at ${signal.z > 0 ? "+" : ""}${signal.z.toFixed(2)} σ\n` +
        `${formatPeriod(signal.period)} · ${signal.geo_level} · ${signal.source_id}`
    );

    // Selective labels only: the signals that actually fire get named, the rest
    // stay available on hover. Labelling all nineteen would be unreadable.
    if (isFiring) {
      const [lx, ly] = point(i, Z_MAX);
      const anchor = Math.abs(lx) < 12 ? "middle" : lx > 0 ? "start" : "end";
      svg
        .append("text")
        .attr("x", lx * 1.06)
        .attr("y", ly * 1.06)
        .attr("text-anchor", anchor)
        .attr("dominant-baseline", ly > 0 ? "hanging" : "auto")
        .attr("fill", INK_3)
        .attr("font-size", 9)
        .attr("font-family", "ui-monospace, monospace")
        .text(shortLabel(signal.label));
    }
  }

  return svg.node();
}

function shortLabel(label) {
  return label.length > 22 ? label.slice(0, 21) + "…" : label;
}

export function signatureSummary(signature) {
  const insufficient = signature.insufficient_coverage;
  const score =
    signature.score == null
      ? "—"
      : `${signature.firing} of ${signature.available} firing`;

  return el("div", {}, [
    el("div", {class: "cs-kicker", text: `${signature.label} signature`}),
    el("div", {class: "cs-card-value cs-num", text: score}),
    el("div", {
      class: "cs-note",
      style: "margin-top:.45rem",
      text: insufficient
        ? `Only ${Math.round(signature.coverage * 100)}% of this archetype's signals have data for this city, which is too little to read a shape from. The chart shows what exists.`
        : signature.summary
    }),
    signature.signals_against?.length
      ? el("div", {
          class: "cs-note",
          style: "margin-top:.5rem;color:var(--cs-ink-4)",
          text:
            "Contradicting: " +
            signature.signals_against.map((s) => s.label).slice(0, 6).join(", ") +
            (signature.signals_against.length > 6
              ? ` and ${signature.signals_against.length - 6} more`
              : "")
        })
      : null
  ]);
}

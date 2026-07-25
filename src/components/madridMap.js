// A district choropleth as plain SVG.
//
// This started on MapLibre and did not need it. There is no basemap and there
// are no tiles — deliberately, so the page never calls out to a tile provider
// while somebody reads it — which left a WebGL renderer and a web worker doing
// nothing but drawing 21 static polygons. Under Observable's module rewriting
// that worker never finished initialising: the map reported `loaded: false` and
// rendered an empty box, with the source and layers correctly in place.
//
// Projected SVG paths have none of that machinery: no WebGL, no worker, no
// runtime dependency, and the shapes stay directly styleable and inspectable.
// Districts are recognisable by their own outlines.

import * as d3 from "npm:d3";
import {INK, RULE, SURFACE, el, formatPeriod, formatValue} from "./theme.js";

// One hue, light to dark. This is magnitude, so a sequential ramp — never a
// rainbow. The lightest step may recede toward the surface because low really
// does mean the bottom of the range.
const RAMP = ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#0d366b"];

export function madridMap(geojson, metric, {width = 720, height = 470} = {}) {
  const entries = Object.entries(metric.districts);
  if (!entries.length) {
    return el("div", {class: "cs-empty", text: "No district values for this measure yet."});
  }

  const byCode = new Map(entries);
  const values = entries.map(([, d]) => d.latest.value);
  const colour = d3
    .scaleSequential(d3.interpolateRgbBasis(RAMP))
    .domain([d3.min(values), d3.max(values)]);

  const projection = d3.geoMercator().fitExtent([[8, 8], [width - 8, height - 8]], geojson);
  const path = d3.geoPath(projection);

  const svg = d3
    .create("svg")
    .attr("viewBox", [0, 0, width, height])
    .attr("width", width)
    .attr("height", height)
    .attr("role", "img")
    .attr("aria-label", `${metric.label} by district in Madrid`)
    .style("max-width", "100%")
    .style("height", "auto")
    .style("background", SURFACE)
    .style("border", `1px solid ${RULE}`)
    .style("border-radius", "3px");

  const tooltip = el("div", {class: "cs-map-tip"});

  svg
    .append("g")
    .selectAll("path")
    .data(geojson.features)
    .join("path")
    .attr("d", path)
    .attr("fill", (feature) => {
      const record = byCode.get(feature.properties.district_code);
      return record ? colour(record.latest.value) : "#1a1d23";
    })
    // A surface-coloured seam separates the shapes without adding a border mark
    // that competes with the data.
    .attr("stroke", SURFACE)
    .attr("stroke-width", 1.25)
    .style("cursor", "pointer")
    .on("pointerenter pointermove", function (event, feature) {
      const record = byCode.get(feature.properties.district_code);
      d3.select(this).attr("stroke", INK).attr("stroke-width", 1.5).raise();
      tooltip.innerHTML =
        `<strong>${feature.properties.name}</strong><br>` +
        (record
          ? `${formatValue(record.latest.value, metric.unit)} · ${formatPeriod(record.latest.period)}`
          : "no data");
      tooltip.style.opacity = "1";
      const box = svg.node().getBoundingClientRect();
      tooltip.style.left = `${event.clientX - box.left + 14}px`;
      tooltip.style.top = `${event.clientY - box.top + 14}px`;
    })
    .on("pointerleave", function () {
      d3.select(this).attr("stroke", SURFACE).attr("stroke-width", 1.25);
      tooltip.style.opacity = "0";
    })
    .append("title")
    .text((feature) => {
      const record = byCode.get(feature.properties.district_code);
      return record
        ? `${feature.properties.name}: ${formatValue(record.latest.value, metric.unit)}`
        : `${feature.properties.name}: no data`;
    });

  // Direct-label the extremes only. Twenty-one labels would be unreadable, and
  // the hover layer carries the rest.
  const ranked = entries.slice().sort((a, b) => b[1].latest.value - a[1].latest.value);
  const labelled = new Set([ranked[0][0], ranked.at(-1)[0]]);

  svg
    .append("g")
    .selectAll("text")
    .data(geojson.features.filter((f) => labelled.has(f.properties.district_code)))
    .join("text")
    .attr("x", (f) => path.centroid(f)[0])
    .attr("y", (f) => path.centroid(f)[1])
    .attr("text-anchor", "middle")
    .attr("fill", INK)
    .attr("font-size", 10)
    .attr("font-weight", 600)
    .attr("paint-order", "stroke")
    .attr("stroke", SURFACE)
    .attr("stroke-width", 3)
    .text((f) => f.properties.name);

  return el("div", {style: "position:relative"}, [svg.node(), tooltip]);
}

export function mapLegend(metric, values) {
  const [lo, hi] = [Math.min(...values), Math.max(...values)];
  return el(
    "div",
    {
      style:
        "display:flex;align-items:center;gap:.6rem;margin-top:.7rem;" +
        "font-size:.68rem;color:var(--cs-ink-3);flex-wrap:wrap"
    },
    [
      el("span", {class: "cs-num", text: formatValue(lo, metric.unit)}),
      el("span", {
        style: `flex:0 0 170px;height:8px;border-radius:2px;background:linear-gradient(90deg,${RAMP.join(",")})`
      }),
      el("span", {class: "cs-num", text: formatValue(hi, metric.unit)}),
      el("span", {text: metric.label})
    ]
  );
}

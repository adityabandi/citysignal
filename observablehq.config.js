import {readFileSync} from "node:fs";

// City routes come from the same registry the pipeline uses, so a city added in
// config/cities.yml appears on the site without touching this file.
const cityBlock = readFileSync("./config/cities.yml", "utf-8");
const cities = [...cityBlock.matchAll(/^\s*-\s*slug:\s*(\S+)\s*$/gm)].map((m) => m[1]);

export default {
  title: "CitySignal",
  root: "src",
  cleanUrls: true,
  // GitHub Pages serves a project site under /<repo>/, so links must be built
  // against that prefix. Local preview stays at the root.
  base: process.env.CI ? "/citysignal/" : "/",
  dynamicPaths: cities.map((slug) => `/cities/${slug}`),
  head: `<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="What is changing in housing demand, economic stress, tourism and supply across eight Spanish cities — with auditable sources, exact geographies and visible data freshness.">
<meta name="color-scheme" content="dark light">`,
  style: "styles.css",
  pages: [
    {name: "The desk", path: "/"},
    {name: "Today", path: "/today"},
    {name: "Compare", path: "/compare"},
    {
      name: "Cities",
      pages: cities.map((slug) => ({
        name: slug[0].toUpperCase() + slug.slice(1),
        path: `/cities/${slug}`
      }))
    },
    {name: "Forecast", path: "/forecast"},
    {name: "Track record", path: "/track-record"},
    {name: "Madrid districts", path: "/madrid-map"},
    {name: "Signals", path: "/signals"},
    {name: "Sources", path: "/sources"},
    {name: "Method", path: "/methodology"}
  ],
  footer: () =>
    `Built from official statistics. Every figure carries its source, its geographic scope and its observation date. ` +
    `<a href="https://github.com/adityabandi/citysignal">Source and data on GitHub</a>.`,
  search: true
};

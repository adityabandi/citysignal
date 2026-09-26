import { readFileSync } from "node:fs";

// City routes come from the same registry the pipeline uses, so a city added in
// config/cities.yml appears on the site without touching this file.
const cityBlock = readFileSync("./config/cities.yml", "utf-8");
const cities = [...cityBlock.matchAll(/^\s*-\s*slug:\s*(\S+)\s*$/gm)].map(
  (m) => m[1],
);

export default {
  title: "CitySignal",
  root: "src",
  cleanUrls: true,
  // GitHub Pages serves a project site under /<repo>/, so links must be built
  // against that prefix. Local preview stays at the root.
  base: process.env.CI ? "/citysignal/" : "/",
  dynamicPaths: cities.map((slug) => `/cities/${slug}`),
  head: `<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Country economic intelligence and alternative signals across major global markets. Explore official benchmarks, hiring, shipping, electricity demand and energy exposure, and Spanish local markets.">
<meta name="color-scheme" content="dark light">`,
  style: "styles.css",
  pages: [
    { name: "Market monitor", path: "/" },
    { name: "Research", path: "/research" },
    { name: "Data catalog", path: "/data-room" },
    { name: "Data quality", path: "/sources" },
    {
      name: "Regional research",
      open: false,
      pages: [
        { name: "Spain overview", path: "/today" },
        { name: "City comparison", path: "/compare" },
        ...cities.map((slug) => ({
          name: slug[0].toUpperCase() + slug.slice(1),
          path: `/cities/${slug}`,
        })),
        { name: "Forecasts", path: "/forecast" },
        { name: "Track record", path: "/track-record" },
        { name: "Madrid districts", path: "/madrid-map" },
        { name: "Local signals", path: "/signals" },
        { name: "Methodology", path: "/methodology" },
      ],
    },
  ],
  footer: () =>
    `<a href="https://github.com/adityabandi/citysignal">CitySignal · source & documentation</a>`,
  search: true,
};

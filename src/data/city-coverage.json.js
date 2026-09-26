import { readFileSync, readdirSync } from "node:fs";
const directory = "data/derived/cities";
const coverage = Object.fromEntries(
  readdirSync(directory)
    .filter((name) => name.endsWith(".json"))
    .map((name) => {
      const city = JSON.parse(readFileSync(`${directory}/${name}`, "utf-8"));
      return [
        city.slug,
        { metrics: Object.values(city.sections ?? {}).flat().length },
      ];
    }),
);
process.stdout.write(JSON.stringify(coverage));

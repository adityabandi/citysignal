// Every city payload in one file, keyed by slug. The city page picks its own
// entry; bundling keeps the loader count at one and the build deterministic.
import {readFileSync, readdirSync} from "node:fs";

const dir = "data/derived/cities";
const payloads = Object.fromEntries(
  readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => [name.replace(/\.json$/, ""), JSON.parse(readFileSync(`${dir}/${name}`, "utf-8"))])
);

process.stdout.write(JSON.stringify(payloads));

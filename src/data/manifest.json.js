import {readFileSync} from "node:fs";

process.stdout.write(readFileSync("data/derived/manifest.json", "utf-8"));

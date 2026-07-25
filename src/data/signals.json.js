import {readFileSync} from "node:fs";

process.stdout.write(readFileSync("data/derived/signals.json", "utf-8"));

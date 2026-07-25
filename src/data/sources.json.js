import {readFileSync} from "node:fs";

process.stdout.write(readFileSync("data/derived/sources.json", "utf-8"));

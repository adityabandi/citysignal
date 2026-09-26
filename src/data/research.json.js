import {readFileSync} from "node:fs";
process.stdout.write(readFileSync("data/derived/research.json", "utf-8"));

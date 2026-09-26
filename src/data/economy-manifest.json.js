import {readFileSync} from "node:fs";
process.stdout.write(readFileSync("data/derived/economy-manifest.json", "utf-8"));

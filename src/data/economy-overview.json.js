import {readFileSync} from "node:fs";
process.stdout.write(readFileSync("data/derived/economy-overview.json"));

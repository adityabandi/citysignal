// The Spain-wide desk plus each city's composite, written by `citysignal derive`.
import {readFileSync} from "node:fs";

process.stdout.write(readFileSync("data/derived/national.json", "utf-8"));

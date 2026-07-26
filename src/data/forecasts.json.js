// The current forecast round, written by `citysignal forecast`. Read straight
// through: the page must never recompute a prediction, because the number on the
// site has to be the number that was frozen and committed.
import {readFileSync} from "node:fs";

process.stdout.write(readFileSync("data/derived/forecasts.json", "utf-8"));

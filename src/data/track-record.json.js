// The track record, assembled from the frozen forecast files plus whatever the
// scorer has been able to settle.
//
// Built by reading the immutable files rather than a summary, so the page cannot
// drift from what was actually committed: if a forecast file exists, it appears
// here, scored or pending. There is deliberately no way to omit one.
import {existsSync, readFileSync, readdirSync} from "node:fs";

const dir = "data/derived/track-record.json";

if (existsSync(dir)) {
  process.stdout.write(readFileSync(dir, "utf-8"));
} else {
  process.stdout.write(JSON.stringify({frozen: [], scores: [], pending: 0}));
}

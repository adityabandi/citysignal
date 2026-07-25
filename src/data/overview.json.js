// Loaders read what the pipeline already committed. They never fetch: the site
// is a pure function of the snapshot in git, so `npm run dev` works offline and
// a build can be reproduced from any commit.
import {readFileSync} from "node:fs";

process.stdout.write(readFileSync("data/derived/overview.json", "utf-8"));

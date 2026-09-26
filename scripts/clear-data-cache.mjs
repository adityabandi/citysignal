// The loaders read committed snapshots outside src/. Observable cannot discover
// those dependencies, so never reuse their output across builds or preview runs.
// Retain the downloaded package cache; only derived data must be refreshed.
import { rmSync } from "node:fs";
rmSync(new URL("../src/.observablehq/cache/data/", import.meta.url), {
  recursive: true,
  force: true,
});

// The boundary file is committed once and never refetched; the loader just
// hands it to the page so the map has no network dependency at view time.
import {readFileSync} from "node:fs";

process.stdout.write(readFileSync("data/geo/madrid-districts.geojson", "utf-8"));

import {readFileSync} from "node:fs";
import {gunzipSync} from "node:zlib";
process.stdout.write(gunzipSync(readFileSync("data/derived/countries/sa.json.gz")));

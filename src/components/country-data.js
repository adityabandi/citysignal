import { FileAttachment } from "observablehq:stdlib";

const loaders = {
  es: () => FileAttachment("../data/countries/es.json").json(),
  pt: () => FileAttachment("../data/countries/pt.json").json(),
  us: () => FileAttachment("../data/countries/us.json").json(),
  cn: () => FileAttachment("../data/countries/cn.json").json(),
  in: () => FileAttachment("../data/countries/in.json").json(),
  jp: () => FileAttachment("../data/countries/jp.json").json(),
  de: () => FileAttachment("../data/countries/de.json").json(),
  gb: () => FileAttachment("../data/countries/gb.json").json(),
  fr: () => FileAttachment("../data/countries/fr.json").json(),
  it: () => FileAttachment("../data/countries/it.json").json(),
  ca: () => FileAttachment("../data/countries/ca.json").json(),
  au: () => FileAttachment("../data/countries/au.json").json(),
  br: () => FileAttachment("../data/countries/br.json").json(),
  mx: () => FileAttachment("../data/countries/mx.json").json(),
  kr: () => FileAttachment("../data/countries/kr.json").json(),
  id: () => FileAttachment("../data/countries/id.json").json(),
  sa: () => FileAttachment("../data/countries/sa.json").json(),
  tr: () => FileAttachment("../data/countries/tr.json").json(),
  za: () => FileAttachment("../data/countries/za.json").json(),
  ru: () => FileAttachment("../data/countries/ru.json").json(),
  nl: () => FileAttachment("../data/countries/nl.json").json(),
  ch: () => FileAttachment("../data/countries/ch.json").json(),
  se: () => FileAttachment("../data/countries/se.json").json(),
  no: () => FileAttachment("../data/countries/no.json").json(),
  pl: () => FileAttachment("../data/countries/pl.json").json(),
  sg: () => FileAttachment("../data/countries/sg.json").json(),
  ae: () => FileAttachment("../data/countries/ae.json").json(),
  ie: () => FileAttachment("../data/countries/ie.json").json(),
};
const cache = new Map();
export function loadCountry(code) {
  if (!loaders[code]) throw new Error("Unknown country");
  if (!cache.has(code))
    cache.set(
      code,
      loaders[code]().catch((error) => {
        cache.delete(code);
        throw error;
      }),
    );
  return cache.get(code);
}

---
title: Data quality
toc: false
---

```js
import { el, txt, nav, table, link, select, field } from "./components/desk.js";
const sources = await FileAttachment("data/sources.json").json();
const economy = await FileAttachment("data/economy-overview.json").json();
const rows = sources.sources ?? [];
const root = el("div", { class: "ec-app" });
const scope = select("Source universe", [
  ["country", "Country datasets"],
  ["all", "All datasets"],
]);
const body = el("div");
root.append(
  nav("quality"),
  el("header", { class: "desk-heading" }, [
    el("div", {}, [
      txt("span", "CITYSIGNAL / OPERATIONS", "ec-eyebrow"),
      txt("h1", "Data quality"),
      txt(
        "p",
        "Source status, observation dates and collection history.",
        "ec-muted",
      ),
    ]),
    field("Universe", scope),
  ]),
  body,
);
function render() {
  const selected = rows.filter(
    (s) => scope.value === "all" || economy.sources.includes(s.source_id),
  );
  body.replaceChildren(
    table(
      [
        "Source",
        "Status",
        "Cadence",
        "Latest observation",
        "Last check",
        "Details",
      ],
      selected.map((s) =>
        el("tr", {}, [
          el("td", {}, [
            s.docs_url
              ? link(s.publisher || s.source_id, s.docs_url)
              : txt("span", s.publisher || s.source_id),
            txt("small", s.source_id),
          ]),
          txt(
            "td",
            {
              ok: "Current",
              skipped: "Unchanged",
              partial: "Partial",
              failed: "Failed",
            }[s.status] ||
              s.status ||
              "—",
          ),
          txt("td", s.cadence || "—"),
          txt("td", s.latest_observation || "—"),
          txt("td", s.last_checked?.slice(0, 10) || "—"),
          el("td", {}, [
            el("details", {}, [
              txt("summary", "Metadata"),
              txt("p", s.license || ""),
              txt("p", s.declared?.notes?.trim() || s.attribution || ""),
              ...(s.last_error ? [txt("p", s.last_error)] : []),
            ]),
          ]),
        ]),
      ),
    ),
  );
}
scope.onchange = render;
render();
display(root);
```

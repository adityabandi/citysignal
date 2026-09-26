import { el, formatPeriod } from "./theme.js";
export { el, formatPeriod };
export const txt = (tag, text, className = "") =>
  el(tag, { text, class: className });
export const num = (v, digits = 2) =>
  v == null || !Number.isFinite(v)
    ? "—"
    : v.toLocaleString("en-GB", {
        maximumFractionDigits: digits,
        minimumFractionDigits: digits,
      });
export const signed = (v) => (v == null ? "—" : `${v > 0 ? "+" : ""}${num(v)}`);
export const seriesOf = (c) => [...c.metrics, ...c.alternative];
export const metricOf = (c, id) =>
  seriesOf(c).find((m) => m.id === id) ||
  (id === "bis_policy_rate"
    ? seriesOf(c).find((m) => m.id === "bis_euro_policy_rate")
    : null);
export const link = (label, href, cls = "ec-link") =>
  el("a", { href, text: label, class: cls });
export function button(label, action, cls = "ec-button") {
  const b = el("button", { type: "button", text: label, class: cls });
  b.onclick = action;
  return b;
}
export function select(label, options, value) {
  const s = el(
    "select",
    { class: "ec-select", "aria-label": label },
    options.map(([value, text]) => el("option", { value, text })),
  );
  if (value != null) s.value = value;
  return s;
}
export const field = (label, control) =>
  el("label", { class: "desk-field" }, [txt("span", label), control]);
export function nav(active = "monitor") {
  return el(
    "nav",
    { class: "ec-nav", "aria-label": "Primary navigation" },
    [
      ["monitor", "Market monitor", "./"],
      ["research", "Research", "./research"],
      ["data", "Data catalog", "./data-room"],
      ["quality", "Data quality", "./sources"],
    ].map(([key, label, url]) =>
      link(label, url, key === active ? "active" : ""),
    ),
  );
}
export function download(name, rows) {
  const cols = Object.keys(rows[0] || {});
  if (!cols.length) return;
  const quote = (v) => `"${String(v ?? "").replaceAll('"', '""')}"`;
  const csv = [cols, ...rows.map((r) => cols.map((c) => r[c]))]
    .map((r) => r.map(quote).join(","))
    .join("\r\n");
  const url = URL.createObjectURL(
    new Blob([csv], { type: "text/csv;charset=utf-8" }),
  );
  const a = el("a", { href: url, download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function table(headers, rows) {
  return el("div", { class: "ec-table-wrap" }, [
    el("table", { class: "ec-table" }, [
      el("thead", {}, [
        el(
          "tr",
          {},
          headers.map((h) =>
            typeof h === "string" ? txt("th", h) : el("th", {}, [h]),
          ),
        ),
      ]),
      el("tbody", {}, rows),
    ]),
  ]);
}

export function masthead(asOf) {
  return el("header", { class: "ec-topbar" }, [
    el("div", { class: "ec-wordmark" }, [
      txt("span", "▥", "ec-mark"),
      txt("span", "CITYSIGNAL"),
      txt("span", "GLOBAL DESK", "ec-tag"),
    ]),
    el("div", { class: "ec-actions" }, [
      txt("span", `Snapshot ${formatPeriod(asOf)}`, "ec-muted"),
      link("Data catalog ↗", "./data-room", "ec-button"),
    ]),
  ]);
}

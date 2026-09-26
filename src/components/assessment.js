import {
  el,
  txt,
  button,
  metricOf,
  num,
  signed,
  formatPeriod,
} from "./desk.js";

export function countryAssessment(country, openMetric) {
  const assessment = country.assessment;
  if (!assessment) return el("div");
  const evidence = (items) =>
    el(
      "div",
      { class: "assessment-evidence" },
      items.map((item) => {
        const m = metricOf(country, item.metric_id);
        if (!m) return txt("span", "");
        const b = button("", () => openMetric(m.id), "assessment-observation");
        b.append(
          txt("span", m.label),
          txt("strong", `${num(m.value, 1)} ${m.unit}`),
          txt("small", `${formatPeriod(item.period)} · ${m.source}`),
        );
        if (m.change != null)
          b.append(
            txt(
              "small",
              `${signed(m.change)} ${m.change_unit} / ${m.change_window}`,
            ),
          );
        return b;
      }),
    );
  const root = el(
    "section",
    { class: "country-assessment", "aria-label": "Country assessment" },
    [
      el("div", { class: "ec-section-heading" }, [
        txt("h2", "Country assessment"),
        txt("span", `As of ${formatPeriod(assessment.as_of)}`, "ec-muted"),
      ]),
      el(
        "div",
        { class: "assessment-grid" },
        assessment.pillars.map((p) =>
          el("details", { class: "assessment-pillar" }, [
            el("summary", {}, [
              txt("span", p.label, "assessment-label"),
              txt("strong", p.headline),
            ]),
            evidence(p.evidence),
            txt("p", p.detail, "ec-muted"),
          ]),
        ),
      ),
    ],
  );
  if (assessment.relationships.length)
    root.append(
      el("details", { class: "ec-method assessment-relations" }, [
        txt(
          "summary",
          `Cross-signal relationships (${assessment.relationships.length})`,
        ),
        ...assessment.relationships.map((r) =>
          el("article", {}, [
            txt("h3", r.title),
            txt("p", r.detail),
            evidence(r.evidence),
          ]),
        ),
      ]),
    );
  root.append(
    el("details", { class: "ec-method" }, [
      txt("summary", "Market transmission & methodology"),
      ...assessment.channels.map((c) =>
        el("p", {}, [
          txt("strong", `${c.label}. `),
          document.createTextNode(c.text),
        ]),
      ),
      txt("p", assessment.method),
      txt(
        "p",
        `${assessment.families.length} signal families. Observation dates and units appear with each contributor.`,
      ),
    ]),
  );
  return root;
}

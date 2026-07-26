#import "@preview/tablex:0.0.8": tablex, cellx
#set page(paper: "a4", margin: (top: 18mm, bottom: 18mm, left: 18mm, right: 18mm))
#set text(font: "Noto Serif", size: 10pt, hyphenate: false)
#set par(spacing: 0.9em)

#let data = json(sys.inputs.data)
#let accent = rgb(57, 135, 229)  // #3987e5

#let format_num(val, decimals: 0) = {
  if val == none or val == null {
    "—"
  } else if decimals == 0 {
    str(int(calc.round(val)))
  } else {
    str(calc.round(val, digits: decimals))
  }
}

#let em_dash = "—"

// ================================================================
// HEADER WITH AGENT BRANDING
// ================================================================
#let header = {
  block(width: 100%, height: 60pt, {
    if data.agent.logo_path != "" and data.agent.logo_path != none {
      // Try to load logo if path is non-empty
      let logo_path = data.agent.logo_path
      // Since we can't check file existence in typst, we guard with an if
      // For now, we just print the path; in practice a missing file will error
      // A production setup might embed the logo differently
      box(width: 50pt, {
        image(logo_path, width: 50pt, height: 40pt)
      })
    }

    block(width: 100% - 60pt, inset: (left: 15pt), {
      text(size: 12pt, weight: "bold", fill: black)[#data.agent.name]
      linebreak()
      text(size: 9pt, fill: rgb(80, 80, 80))[#data.agent.company]
      linebreak()
      text(size: 8pt, fill: rgb(100, 100, 100))[#data.agent.phone] #h(1em) text(size: 8pt, fill: rgb(100, 100, 100))[#data.agent.email]
    })
  })
  line(length: 100%, stroke: (paint: accent, thickness: 1pt))
}

#header

// ================================================================
// DISTRICT TITLE AND KEY METRICS
// ================================================================
#heading(level: 1, text(size: 20pt)[District #data.district_code])

#v(0.3em)

#let metrics = data.district_metrics

#grid(
  columns: (1fr, 1fr),
  gutter: 15pt,

  block(fill: rgb(245, 248, 252), inset: 12pt, radius: 4pt, {
    text(size: 8pt, weight: "bold", fill: accent)[RENTAL PRICE]
    linebreak()
    text(size: 16pt, weight: "bold", fill: black, font: "IBM Plex Mono")[#format_num(metrics.rent_m2, decimals: 2) €/m²]
  }),

  block(fill: rgb(245, 248, 252), inset: 12pt, radius: 4pt, {
    text(size: 8pt, weight: "bold", fill: accent)[POPULATION]
    linebreak()
    text(size: 16pt, weight: "bold", fill: black, font: "IBM Plex Mono")[#format_num(metrics.population, decimals: 0)]
    #v(0.2em)
    text(size: 7pt, fill: rgb(100, 100, 100))[
      12M change: #format_num(metrics.population_yoy, decimals: 2)%
    ]
  }),
)

#v(0.5em)

#grid(
  columns: (1fr, 1fr, 1fr),
  gutter: 12pt,

  block(fill: rgb(250, 250, 250), inset: 10pt, radius: 4pt, {
    text(size: 7pt, fill: rgb(80, 80, 80), weight: "bold")[BUSINESS VACANCY]
    linebreak()
    text(size: 12pt, weight: "bold", fill: black, font: "IBM Plex Mono")[#format_num(metrics.vacancy_rate, decimals: 1)%]
  }),

  block(fill: rgb(250, 250, 250), inset: 10pt, radius: 4pt, {
    text(size: 7pt, fill: rgb(80, 80, 80), weight: "bold")[HOSPITALITY STOCK]
    linebreak()
    text(size: 12pt, weight: "bold", fill: black, font: "IBM Plex Mono")[#format_num(metrics.horeca_stock, decimals: 0)]
  }),

  block(fill: rgb(250, 250, 250), inset: 10pt, radius: 4pt, {
    text(size: 7pt, fill: rgb(80, 80, 80), weight: "bold")[TOURIST LETS]
    linebreak()
    text(size: 12pt, weight: "bold", fill: black, font: "IBM Plex Mono")[#format_num(metrics.vut_licensed, decimals: 0)]
  }),
)

#v(0.3em)

#block(fill: rgb(250, 250, 250), inset: 10pt, radius: 4pt, {
  text(size: 7pt, fill: rgb(80, 80, 80), weight: "bold")[BUILDING PERMITS GRANTED (LATEST)]
  linebreak()
  text(size: 12pt, weight: "bold", fill: black, font: "IBM Plex Mono")[#format_num(metrics.licences_granted, decimals: 0) licences]
})

// ================================================================
// RANKING COMPARISON
// ================================================================
#v(0.8em)

#heading(level: 2, text(size: 12pt)[How This District Ranks])

#text(size: 9pt, fill: rgb(80, 80, 80))[
  Ranked by rental price, this district is *#data.ranking.rank of #data.ranking.total* districts in Madrid.
]

// ================================================================
// FORECAST PANEL
// ================================================================
#v(0.8em)

#heading(level: 2, text(size: 12pt)[Madrid Forecast Panel])

#text(size: 8pt, fill: rgb(100, 100, 100))[
  The following forecasts apply to Madrid province or city (not this district specifically). Ranges shown are p10–p50–p90.
]

#v(0.5em)

#tablex(
  columns: (1.5fr, 2.5fr, 1.5fr),
  align: (left, left, right),
  header-rows: 1,
  fill: (col, row) => {
    if row == 0 { accent } else { none }
  },

  text(size: 8pt, weight: "bold", fill: white)[Target],
  text(size: 8pt, weight: "bold", fill: white)[Question],
  text(size: 8pt, weight: "bold", fill: white)[Range],

  ..data.forecasts.map(f => (
    text(size: 7pt, fill: black)[#f.target_id],
    text(size: 7pt, fill: rgb(60, 60, 60))[#f.question],
    text(size: 7pt, fill: black, font: "IBM Plex Mono")[
      #format_num(f.p10, decimals: 0) – #format_num(f.p50, decimals: 0) – #format_num(f.p90, decimals: 0)
    ],
  )).flatten()
)

// ================================================================
// FOOTER
// ================================================================
#v(1fr)

#line(length: 100%, stroke: (paint: accent, thickness: 0.5pt))

#text(size: 7pt, fill: rgb(120, 120, 120))[
  Prepared by #data.agent.name
]

#text(size: 7pt, fill: rgb(150, 150, 150))[
  Data from Madrid City Council, INE, MIVAU, SEPE and other official sources. Generated #data.generated_at.
]

#text(size: 7pt, fill: rgb(150, 150, 150), style: "italic")[
  CitySignal — Open-source market forecasting for real estate.
]

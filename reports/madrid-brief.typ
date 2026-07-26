#set page(paper: "a4", margin: (top: 20mm, bottom: 20mm, left: 20mm, right: 20mm))
#set text(font: "Noto Serif", size: 11pt, hyphenate: false)
#set par(spacing: 1.2em)

#let data = json(sys.inputs.data)
#let accent = rgb(57, 135, 229)  // #3987e5

#let format_num(val, decimals: 0) = {
  if val == none {
    "—"
  } else if decimals == 0 {
    str(int(calc.round(val)))
  } else {
    str(calc.round(val, digits: decimals))
  }
}

#let format_signed_num(val, decimals: 2) = {
  if val == none {
    "not enough inputs reporting"
  } else if val >= 0 {
    "+" + str(calc.round(val, digits: decimals))
  } else {
    str(calc.round(val, digits: decimals))
  }
}

#let get_verdict(val) = {
  if val == none {
    ""
  } else if val > 0.5 {
    "above normal"
  } else if val < -0.5 {
    "below normal"
  } else {
    "about normal"
  }
}

#let format_month(date_str) = {
  let parts = date_str.split("-")
  if parts.len() >= 2 {
    let year = parts.at(0)
    let month = parts.at(1)
    let month_names = ("January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December")
    let month_num = int(month)
    if month_num >= 1 and month_num <= 12 {
      month_names.at(month_num - 1) + " " + year
    } else {
      date_str
    }
  } else {
    date_str
  }
}

#let model_name(m) = {
  if m == "random_walk" {
    "random walk"
  } else if m == "seasonal_naive" {
    "seasonal naive"
  } else if m == "drift" {
    "drift"
  } else if m == "analog" {
    "analog"
  } else {
    m
  }
}

#let em_dash = "—"

// ================================================================
// COVER PAGE
// ================================================================
#page(header: none, footer: none)[
  #v(4cm)
  #text(size: 36pt, weight: "bold", fill: black)[Madrid Market Brief]
  #v(0.5em)
  #text(size: 14pt, fill: rgb(100, 100, 100))[
    #format_month(data.month)
  ]
  #v(2em)

  // Regime section
  #box(
    width: 100%,
    fill: rgb(250, 250, 250),
    inset: 15pt,
    radius: 6pt,
  )[
    #text(size: 12pt, weight: "bold", fill: black)[Market Regime]
    #v(0.5em)
    #text(size: 14pt, weight: "bold", fill: accent)[#data.regime.label]
    #v(0.5em)
    #text(size: 10pt, fill: rgb(60, 60, 60))[
      #data.regime.reading
    ]
  ]

  #v(1.5em)

  // Index verdicts
  #text(size: 12pt, weight: "bold", fill: black)[Indices]
  #v(0.3em)
  #text(size: 8pt, fill: rgb(100, 100, 100))[in standard deviations from this city's normal]
  #v(0.5em)
  #for (idx_name, idx) in data.indices {
    box(width: 100%, inset: (left: 10pt, bottom: 8pt))[
      #text(size: 10pt, weight: "bold", fill: black)[#idx.label]
      #h(1em)
      #text(size: 9pt, weight: "bold", fill: accent)[#format_signed_num(idx.value, decimals: 2)]
      #h(0.5em)
      #text(size: 9pt, fill: rgb(80, 80, 80))[#get_verdict(idx.value)]
      #v(0.15em)
      #text(size: 8pt, fill: rgb(100, 100, 100))[#idx.question]
    ]
  }

  #v(3cm)
  #text(size: 8pt, fill: rgb(150, 150, 150))[
    Prepared by CitySignal
  ]
]

// ================================================================
// FORECAST TABLE
// ================================================================
#page[
  #heading(level: 1, [Forecast Summary])

  #text(size: 9pt, fill: rgb(100, 100, 100))[
    Quantile intervals (p10 / p50 / p90) for the next issued targets. Where noted, the selected model does not beat its baseline.
  ]

  #v(1em)

  #table(
    columns: (2fr, 3fr, 1fr, 1fr, 1fr, 1.5fr, 0.8fr),
    align: (left, left, right, right, right, left, left),

    fill: (col, row) => {
      if row == 0 { accent } else { none }
    },

    text(size: 9pt, weight: "bold", fill: white)[Target],
    text(size: 9pt, weight: "bold", fill: white)[Question],
    text(size: 9pt, weight: "bold", fill: white)[p10],
    text(size: 9pt, weight: "bold", fill: white)[p50],
    text(size: 9pt, weight: "bold", fill: white)[p90],
    text(size: 9pt, weight: "bold", fill: white)[Model],
    text(size: 9pt, weight: "bold", fill: white)[Status],

    ..data.forecasts.map(f => (
      text(size: 8pt, fill: black)[#f.target_id],
      text(size: 8pt, fill: rgb(60, 60, 60))[#f.question],
      text(size: 8pt, fill: black)[#format_num(f.p10, decimals: 0)],
      text(size: 8pt, fill: black)[#format_num(f.p50, decimals: 0)],
      text(size: 8pt, fill: black)[#format_num(f.p90, decimals: 0)],
      text(size: 8pt, fill: black)[#model_name(f.model)],
      if f.does_not_beat_baseline {
        text(size: 8pt, fill: rgb(200, 0, 0), weight: "bold")[Baseline]
      } else {
        text(size: 8pt, fill: green)[OK]
      },
    )).flatten()
  )

  #v(1.5em)
  #text(size: 8pt, fill: rgb(150, 150, 150), style: "italic")[
    "Baseline" indicates the model does not outperform a naive alternative.
  ]
]

// ================================================================
// TRACK RECORD
// ================================================================
#page[
  #heading(level: 1, [Track Record])

  #let track = data.track_record

  #if track.scored > 0 {
    text(size: 10pt, fill: black)[
      *#track.scored forecasts have matured and been scored.* #track.pending are still pending.
    ]

    v(1em)

    table(
      columns: (2fr, 1.5fr, 1.5fr, 1.5fr, 1fr),
      align: (left, right, right, right, center),

      fill: (col, row) => {
        if row == 0 { accent } else { none }
      },

      text(size: 9pt, weight: "bold", fill: white)[Target],
      text(size: 9pt, weight: "bold", fill: white)[Predicted],
      text(size: 9pt, weight: "bold", fill: white)[Actual],
      text(size: 9pt, weight: "bold", fill: white)[Error %],
      text(size: 9pt, weight: "bold", fill: white)[Inside 80?],

      ..track.forecasts.map(f => (
        text(size: 8pt, fill: black)[#f.target_id],
        text(size: 8pt, fill: black)[#format_num(f.predicted_p50, decimals: 1)],
        text(size: 8pt, fill: black)[#format_num(f.actual, decimals: 1)],
        text(size: 8pt, fill: black)[#format_num(f.pct_error, decimals: 1)],
        text(size: 8pt, fill: if f.inside_80 == true { green } else if f.inside_80 == false { rgb(200, 0, 0) } else { black }, weight: if f.inside_80 != none { "bold" } else { "regular" })[
          #if f.inside_80 == true { "✓" } else if f.inside_80 == false { "✗" } else { "—" }
        ],
      )).flatten()
    )
  } else {
    text(size: 11pt, fill: rgb(80, 80, 80))[
      No forecasts have yet matured. #track.pending forecasts are pending scoring.
    ]
  }
]

// ================================================================
// DISTRICTS
// ================================================================
#page[
  #heading(level: 1, [Districts Ranked])

  #text(size: 9pt, fill: rgb(100, 100, 100))[
    Ranked by current rental price per square metre.
  ]

  #v(1em)

  #table(
    columns: (0.6fr, 2fr, 1fr, 1fr, 1fr, 1fr),
    align: (center, left, right, right, right, right),

    fill: (col, row) => {
      if row == 0 { accent } else { none }
    },

    text(size: 9pt, weight: "bold", fill: white)[Rank],
    text(size: 9pt, weight: "bold", fill: white)[District],
    text(size: 9pt, weight: "bold", fill: white)[€/m²],
    text(size: 9pt, weight: "bold", fill: white)[Population],
    text(size: 9pt, weight: "bold", fill: white)[Vacancy %],
    text(size: 9pt, weight: "bold", fill: white)[Licensed Lets],

    ..data.districts.enumerate().map(((i, d)) => (
      text(size: 8pt, fill: black)[#(i+1)],
      text(size: 8pt, fill: black)[District #d.code],
      text(size: 8pt, fill: black)[#format_num(d.rent_m2, decimals: 2)],
      text(size: 8pt, fill: black)[#format_num(d.population, decimals: 0)],
      text(size: 8pt, fill: black)[#format_num(d.vacancy_rate, decimals: 1)],
      text(size: 8pt, fill: black)[#format_num(d.vut_licensed, decimals: 0)],
    )).flatten()
  )
]

// ================================================================
// DATA APPENDIX
// ================================================================
#page[
  #heading(level: 1, [Data Sources])

  #text(size: 9pt, fill: rgb(100, 100, 100))[
    Every metric carried in this report comes from an official source. Dates shown reflect the most recent observation available.
  ]

  #v(1em)

  #text(size: 8pt, fill: rgb(80, 80, 80), weight: "bold")[
    Property transfers (INE)
  ]
  #text(size: 8pt, fill: rgb(100, 100, 100))[
    Province-level count of registered property transfers. Leads official price statistics.
  ]

  #v(0.5em)

  #text(size: 8pt, fill: rgb(80, 80, 80), weight: "bold")[
    Appraised housing value (MIVAU)
  ]
  #text(size: 8pt, fill: rgb(100, 100, 100))[
    Quarterly valuation per square metre. The value banks lend against.
  ]

  #v(0.5em)

  #text(size: 8pt, fill: rgb(80, 80, 80), weight: "bold")[
    Registered unemployment (SEPE)
  ]
  #text(size: 8pt, fill: rgb(100, 100, 100))[
    Monthly count of people signed on at the job office in the city.
  ]

  #v(0.5em)

  #text(size: 8pt, fill: rgb(80, 80, 80), weight: "bold")[
    District data (Madrid City Council)
  ]
  #text(size: 8pt, fill: rgb(100, 100, 100))[
    Monthly population, business premises inventory, tourist dwellings licensed, and building permits. Geographic scope: the 21 districts of Madrid city.
  ]

  #v(1em)

  #text(size: 8pt, fill: rgb(150, 150, 150), style: "italic")[
    Search indices (Google Trends) represent attention rather than measured demand or activity. They reflect how often people are searching for topics, not how many are actually buying, renting, or relocating.
  ]

  #v(1em)

  #text(size: 8pt, fill: rgb(100, 100, 100))[
    *CitySignal* is an open system for honest market forecasting. Every number shown here is generated from published data and is reproducible. No figures are hidden, no models are proprietary, and no track record is revised.
  ]
]

// ATS-safe CV template for jobkit, styled after Jake's Resume (LaTeX):
// Computer Modern, a large bold name over an underlined contact line,
// small-caps section headings with a rule beneath, entries indented under
// them as a bold title over an italic "Company, Location | Dates" line.
//
// ATS rules this keeps: single column; no tables or grids; nothing in the
// page header or footer; standard section headings; each job's title sits
// directly above its company and dates. Data strings are inserted as text
// and never evaluated as markup.
#let cv = json(sys.inputs.at("data", default: "data.json"))
#let c = cv.contact

#set document(title: c.name + " - CV", author: c.name)
#set page(paper: "a4", margin: (x: 36pt, top: 40pt, bottom: 36pt), header: none, footer: none)
#set text(font: "New Computer Modern", size: 9pt, lang: "en", hyphenate: false)
#set par(justify: false, leading: 0.52em, spacing: 0.7em)
#set list(indent: 0pt, body-indent: 4pt, spacing: 0.52em, marker: text(size: 7pt, baseline: -0.5pt)[•])

#let indent = 10.8pt  // 0.15in, where entries and skills sit under a heading
#let bullets-indent = 6pt

// Typography LaTeX did for free: curly apostrophes, and "2021 - Present"
// printed as "2021 – Present". The ATS check folds both back.
#let t(s) = s.replace("'", "’")
#let dashed(s) = t(s).replace(" - ", " – ")

#let section(title) = block(
  width: 100%, above: 13pt, below: 6pt, inset: (bottom: 2.5pt), stroke: (bottom: 0.4pt),
  text(size: 12pt, smallcaps(title)),
)

#let place-of(org, location) = if location != "" { org + ", " + location } else { org }

#let entry(title, place, dates, bullets) = pad(left: indent, block(above: 11pt, below: 0pt, {
  block(below: 3pt, sticky: true, {
    text(weight: "bold", t(title))
    linebreak()
    emph(t(place) + " | " + dashed(dates))
  })
  if bullets.len() > 0 {
    pad(left: bullets-indent, list(..bullets.map(b => if b.label != "" {
      text(weight: "bold", t(b.label) + ":") + " " + t(b.text)
    } else {
      t(b.text)
    })))
  }
}))

#align(center, {
  text(size: 24.9pt, weight: "bold", c.name)
  let pieces = ()
  if c.phone != "" { pieces.push(c.phone) }
  if c.email != "" { pieces.push(underline(link("mailto:" + c.email, c.email))) }
  for l in c.links { pieces.push(underline(link(l.url, l.label))) }
  if c.location != "" { pieces.push(c.location) }
  if pieces.len() > 0 {
    v(-2pt)
    block(text(size: 9pt, pieces.join(" | ")))
  }
})

#section("Professional Profile")
#par(t(cv.profile))

#section("Professional Experience")
#for job in cv.experience {
  entry(job.title, place-of(job.company, job.location), job.dates, job.bullets)
}

#section("Education")
#for s in cv.education {
  entry(s.degree, place-of(s.school, s.location), s.dates, s.bullets)
}

#section("Skills")
#pad(left: indent, {
  for s in cv.skills {
    par(text(weight: "bold", t(s.category) + ":") + " " + t(s.items.join(", ")))
  }
})

#for s in cv.sections {
  section(t(s.title))
  pad(left: indent, par(t(s.items.join(", "))))
}

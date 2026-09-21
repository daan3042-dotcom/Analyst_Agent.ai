# Rejected Alternatives

Things that were considered and deliberately not built, kept here so a
future session doesn't re-propose them without knowing they were already
evaluated.

- **Beneish M-Score, random-walk model, random-forest model** —
  considered, not built. **UNKNOWN**: the specific reasoning given at the
  time isn't fully recoverable from what's available to this
  reconstruction. If the project owner remembers, worth capturing here.
- **A full 3-statement/debt-schedule model** with a proper balance-sheet
  rollforward — judged out of scope for a 2-person fundamental-research
  operation; that's investment-banking/M&A-modeling scale, not needed for
  this project's purpose.
- **A ~150-file "bank-grade" architecture** (proposed via an external
  document) — reviewed section by section; only the Monte Carlo
  simulation and the lineage/provenance manifest were judged genuinely
  valuable and adopted. The rest was judged mismatched to this project's
  scale.
- **Automatic peer discovery** (no manual `--peers` input required) — no
  reliable free data source was found. A rougher SIC-code-based
  suggestion was floated but never actually attempted; status is open,
  not firmly rejected.
- **Page-level source citations** ("see page 64") — not feasible; SEC's
  structured XBRL data has no page concept (that only applies to raw PDF
  filings, a different data source than what this pipeline uses).
- **A consensus-analyst-estimates feed** — would require a paid data
  provider; not pursued.
- **Splitting `framework.py` into multiple files** — judged as pure code
  organization with no functional benefit; not done.
- **An A–E "evidence tier" labeling system and a formal source-hierarchy
  (Tier 1–7)** — proposed as a way to make fact-vs-inference separation
  in report text more rigorous. **Genuinely undecided, not rejected** — it
  sits in tension with this project's established style of avoiding
  visible in-text tags/labels in report prose, and the project owner has
  not yet chosen a direction. Worth raising again before building.
- **A combined bar+line chart type and a volatility-band chart type** —
  noted as available chart ideas, not built.
- **Reducing `MAX_REVISION_ROUNDS` below 2**, and **narrowing what the 4
  reviewers re-check after round 1** (only re-checking changed sections
  instead of the full report) — both considered as cost optimizations;
  both explicitly declined in favor of the fuller, more expensive check.
  See ADR-002.
- **Patch-based self-correction** (as opposed to full-report
  regeneration) — see ADR-002 for the full reasoning.
- **Segment-level financial data** (revenue/margin by business segment) —
  investigated. SEC's simple JSON API only exposes consolidated
  (whole-company) figures, not the dimensional/segment breakdown. The two
  paths to get it were both judged disproportionate to attempt quickly:
  - The `edgartools` package likely pulls in compiled `lxml`/`pyarrow` —
    the same risk class as the `hmmlearn`/`voyageai` incidents (see
    ADR-003) — and this could not be verified live before deciding.
  - Writing a from-scratch raw-XBRL parser (reading `<context>` dimension
    elements plus a separate label-linkbase file for readable segment
    names) is a substantially bigger and more fragile undertaking than
    anything else in this codebase — segment tagging conventions vary
    company to company, unlike e.g. Form 4's flat, standardized schema.
  Recommended as its own focused future effort if the project owner wants
  it, not a quick addition.

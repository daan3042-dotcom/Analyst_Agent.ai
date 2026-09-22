# TCE Financial Analyst Agent

An AI-assisted equity research pipeline built for **The Collective Edge
(TCE)**. Give it a stock ticker; it produces a structured, 18-section
HTML deep-dive report — combining real financial data (SEC filings,
market data, macro data) with Claude's analytical writing, styled in the
company's own brand colors.

The core design principle: **Python computes every number, Claude never
does financial arithmetic itself.** See `CLAUDE.md` for the full set of
standing rules this project follows.

## What it produces

For a given ticker, a report covering (where data allows):

- Company profile, business model, value chain, competitive position
- Historical financials (SEC primary, FMP fallback for non-US filers),
  peer comparison, ownership & governance
- Macro exposure (FRED), commodity/FX exposure (Alpha Vantage)
- Insider transactions (SEC Form 4), short interest (FINRA),
  speculative futures positioning (CFTC Commitments of Traders),
  options-implied volatility vs. historical volatility
- Quantitative risk: VaR, Sharpe/Sortino, rolling beta, HMM regime
  detection
- Scenario analysis, sensitivity analysis, and a Monte Carlo simulation
- Altman Z-Score, Piotroski F-Score, reverse-DCF (market-implied growth)
- 7 forensic accounting checks
- A source-provenance ("lineage") manifest for every key figure
- Devil's-advocate section, monitoring/kill-criteria, and a single,
  clearly-isolated section (18) for directional analytical opinion —
  every other section stays strictly neutral

Every report passes an automated review (a self-check, then 4 parallel
specialized reviewers, then up to 2 correction rounds) before being
saved. If issues remain, the file is saved with a `NEEDS_REVIEW` flag
rather than presented as clean — this is a deliberate feature, not a bug.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your own keys
```

Required API keys (see `.env.example`): `ANTHROPIC_API_KEY`,
`ALPHAVANTAGE_API_KEY`, `FRED_API_KEY`, `FMP_API_KEY`, `VOYAGE_API_KEY`.

> Note: the code currently reads these from real environment variables,
> not from `.env` directly — see the note in `.env.example`.

## Usage

```bash
python src/agent/analyst_agent.py TICKER
python src/agent/analyst_agent.py TICKER --peers PEER1 PEER2
```

Reports are written to `output/`. Per-ticker analysis history (used for
the calibration score and kill-criteria monitoring) is stored in
`track_record/`.

To check whether a previously-analyzed company's kill-criteria have
since been breached, without generating a full report:

```bash
python src/tracking/monitor_kill_criteria.py [TICKER]
```

To see the cross-report calibration score (how often past kill-criteria
held vs. were breached):

```bash
python src/tracking/track_record.py
```

## Library / knowledge base

Drop PDFs into `library/pdfs/` and URLs (articles or YouTube videos) into
`library/urls.txt`, then run:

```bash
python src/knowledge/library_index.py
```

This builds a semantic search index Claude can query during analysis.
Re-running the script only indexes new material.

## Tests

```bash
pytest
```

238 tests as of the last migration. `pytest.ini` sets `pythonpath = src`
so this works without any manual path setup.

## Project structure & documentation

- `CLAUDE.md` — the project briefing Claude Code reads automatically
- `docs/architecture.md` — full pipeline and module breakdown
- `docs/requirements.md` — functional/non-functional requirements
- `docs/decisions/` — architecture decision records (the "why" behind
  non-obvious choices)
- `docs/rejected-alternatives.md` — what was considered and not built,
  and why
- `docs/roadmap.md` — what's next
- `docs/data-sources.md` — every external API/data source and its quirks
- `docs/project-state.md` — living "where things stand" document
- `conversations/original-development-chat.md` — archived export of the
  original development conversation this project was built in

## A note on the codebase's language

Inline comments and console output are largely in Dutch — DD and this
project's development process are Dutch-speaking. This is a deliberate,
consistent choice, not something to change during future work.

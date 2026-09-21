# Requirements

## Purpose

An AI-assisted equity research pipeline for The Collective Edge (TCE), a
two-person trading/investment operation. Given a stock ticker, it produces
a structured, 18-section HTML "deep dive" report combining real financial
data with Claude's analytical writing — built specifically so Claude never
performs financial arithmetic itself. The stated long-term ambition is to
eventually extend this into a multi-agent system (a sector agent, a macro
agent, and an orchestrator) once this single agent is "solid" — see
`docs/roadmap.md`.

## Functional requirements

- Given a ticker (and optionally a list of peer tickers), produce a
  complete 18-section HTML report, styled in the company's own brand
  colors.
- All financial figures must come from real, verifiable data sources —
  never invented, never computed by the LLM itself when a deterministic
  calculation is possible.
- The report must include, where data allows: historical financials (SEC
  primary, FMP fallback for non-US filers), a peer comparison, macro
  context (FRED), commodity/FX exposure (Alpha Vantage), insider
  transaction history (SEC Form 4), short interest (FINRA),
  options-market-implied volatility, quantitative risk models (VaR,
  Sharpe/Sortino, rolling beta, HMM regime detection), scenario/
  sensitivity analysis with a Monte Carlo simulation, screening scores
  (Altman Z, Piotroski F), a reverse-DCF read of market-implied growth,
  forensic accounting checks, and a provenance/lineage manifest for every
  key figure.
- Section 18 ("Variant Perception") is the only section permitted a
  directional analytical view; sections 1–17 must stay neutral (no
  buy/sell language, no price targets).
- Every generated report must pass through an automated quality-control
  pipeline (self-check, then 4 parallel specialized reviewers, then up to
  2 correction rounds) before being saved; if issues remain after 2
  rounds, the report is saved with a `NEEDS_REVIEW` flag rather than
  presented as clean.
- The system retains a history of every analysis performed on a given
  ticker (not just the most recent one), so a re-analysis can reference
  prior findings and evaluate whether earlier kill-criteria were
  breached.
- A small library/knowledge base (PDFs, articles, YouTube transcripts the
  user supplies) is semantically searchable and available to Claude
  during analysis.
- A standalone script can check a ticker's stored kill-criteria against
  fresh data without generating a full report.
- A cross-report calibration score aggregates, across all analyzed
  tickers, how often past structured kill-criteria held vs. were
  breached.

## Non-functional requirements

- **Cost is secondary to quality.** Explicit, standing project priority.
- **Graceful degradation.** A missing or failing data source, or an
  unavailable optional tool, must never crash the whole pipeline — it
  should be logged and the report generated with what's available.
- **No compiled/native dependencies where avoidable**, given the
  development machine's Windows security policy has repeatedly blocked
  compiled extensions built from source. See
  `docs/decisions/ADR-003-http-api-over-sdk-for-native-deps.md`.
- **Deterministic checks over trust.** Any place a real bug was found
  where Claude mis-stated, fabricated, or miscopied a number, the fix is
  a code-level backstop that checks the final text/chart output against
  the actual computed value.
- Reasonable test coverage is expected for new logic (238 tests as of the
  last migration).

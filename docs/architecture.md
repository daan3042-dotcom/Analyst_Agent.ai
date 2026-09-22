# Architecture

## Pipeline overview

```
User runs: python src/analyst_agent.py TICKER [--peers TICKER1 TICKER2 ...]
       │
       ▼
[1] Data fetch (deterministic, no LLM):
     SEC EDGAR (primary) / FMP (non-US fallback) / Yahoo Finance (market data)
     FRED (macro) · Alpha Vantage (commodities/FX) · SEC Form 4 (insiders)
     FINRA (short interest) · yfinance options chain (implied vol)
     forensics.py (7 checks) · verified_metrics (~15 ratios, computed once)
     Altman Z · Piotroski F · reverse-DCF · VaR/Sharpe/Sortino/rolling beta
     HMM regime detection (own NumPy implementation) · prior track_record
       │
       ▼
[2] Prompt assembly (framework.py) + tool-use loop with Claude:
     Claude can call 10 tools (news search, playbook/library search,
     financial projection, sensitivity analysis, Monte Carlo, commodity
     price, CFTC futures positioning, FX rate, consistency-assessment,
     event price reaction)
       │
       ▼
[3] Internal self-check (same conversation, cheaper than a full re-review)
       │
       ▼
[4] Quality control: 4 parallel specialized reviewers
     (Cijfers/Numbers, Neutraliteit/Neutrality, Volledigheid/Completeness,
     Kruisverwijzing/Cross-reference) + deterministic consistency_check.py
     backstops → up to 2 correction rounds (issues are removed/fixed, not
     merely reworded) → NEEDS_REVIEW flag if issues remain after 2 rounds
       │
       ▼
[5] Executive summary written · lineage/provenance manifest built ·
     track_record.py snapshot saved (full history, not overwritten) ·
     brand colors looked up · final HTML rendered (render.py, 29 chart
     types) and saved to output/
```

## The 18-section report structure

Sections 1–14 are standard company/financial coverage (profile, business
model, value chain, competitive position, financial performance, ratios
vs. peers, balance sheet, capital allocation, macro exposure, recent
developments, ownership & governance).

- **15 — Scenario Analysis**: bear/base/bull projections, sensitivity
  analysis, and a mandatory Monte Carlo simulation. All probabilities and
  thresholds here must be explicitly labeled as analyst-assigned, never
  presented as empirically derived.
- **16 — Devil's Advocate**: a mandatory section that actively argues
  against the report's own conclusions.
- **17 — Monitoring & Kill-Criteria**: concrete, measurable thresholds
  that would undermine the thesis; ends with a `kill-criteria-recap`
  chart. On a re-analysis of the same company, this section evaluates
  whether prior kill-criteria held or were breached (see
  `docs/decisions/ADR-004-track-record-full-history.md`).
- **18 — Variant Perception**: the **only** section permitted a
  directional analytical view. It opens with an explicit disclaimer,
  is deliberately the most elaborate section, and its directional
  framing must never leak into sections 1–17.

## Module map (`src/` subpackage layout)

| File | Responsibility |
|---|---|
| `agent/analyst_agent.py` | Orchestrates the whole pipeline end to end |
| `framework/framework.py` | The 18-section prompt, all writing rules, chart-type documentation |
| `framework/tools.py` | The 10 tools Claude can call during analysis |
| `reporting/render.py` | Renders the final HTML report; 29 chart types |
| `data/sec_data.py` | SEC EDGAR financials (primary US source) + Form 4 insider transaction parsing |
| `data/fmp_data.py` | Financial Modeling Prep — fallback for non-US filers |
| `data/fred_data.py` | Macro data (fed funds rate, 10Y yield, CPI, unemployment) |
| `data/data_fetch.py` | yfinance-based data: prices, VaR, Sharpe/Sortino, rolling beta, HMM regime detection, options-implied-volatility analysis |
| `data/commodity_data.py` | Alpha Vantage commodity/FX prices |
| `data/cftc_data.py` | CFTC Commitments of Traders (speculative futures positioning) — see `docs/data-sources.md` for its schema-discovery approach and reliability caveat |
| `data/finra_data.py` | Short interest data — see `docs/data-sources.md` for its schema-discovery approach and reliability caveat |
| `analysis/forensics.py` | 7 forensic accounting flags + ~15 deterministically-computed verified metrics |
| `analysis/consistency_check.py` | Deterministic checks that the report's text/charts match the real computed values |
| `analysis/reverse_dcf.py` | Market-implied growth rate / WACC read; also forward intrinsic value estimate |
| `analysis/altman_z.py` | Altman Z-Score (bankruptcy risk screen) |
| `analysis/piotroski_score.py` | Piotroski F-Score (fundamental quality screen) |
| `analysis/financial_model.py` | 3-scenario projection engine, sensitivity analysis, Monte Carlo simulation |
| `analysis/peer_analysis.py` | Peer comparison (only when the user supplies peer tickers) |
| `tracking/track_record.py` | Full per-ticker analysis history; structured kill-criteria extraction; cross-report calibration score |
| `analysis/self_consistency.py` | Self-consistency sampling (Wang et al., 2022) applied to *subjective* judgments (e.g. an economic-moat score) — asks Claude the same question 3x independently and takes the median |
| `reporting/lineage.py` | Builds the source-provenance manifest included in every report |
| `analysis/simple_hmm.py` | Own pure-NumPy Gaussian HMM (Baum-Welch + Viterbi) — replaces `hmmlearn` |
| `knowledge/library_index.py` | Occasional script: indexes PDFs/articles/YouTube transcripts into the searchable library |
| `knowledge/library_search.py` | Runtime semantic search over that library (Voyage AI HTTP API for embeddings) |
| `knowledge/library_sources.py` | Fetches/extracts text from a URL (YouTube transcript or article) for indexing |
| `reporting/color_safety.py` | Objective WCAG contrast-ratio calculation — guards against unreadable color combinations |
| `reporting/validate_custom_html.py` | Safety net for the one chart type where Claude writes free-form HTML/CSS |
| `tracking/monitor_kill_criteria.py` | Standalone script: checks fresh data against stored kill-criteria thresholds without running a full report |

## Structure

```
src/
├── agent/          ← analyst_agent.py
├── framework/      ← framework.py, tools.py
├── data/           ← data_fetch.py, sec_data.py, fmp_data.py,
│                     fred_data.py, commodity_data.py, cftc_data.py, finra_data.py
├── analysis/       ← forensics.py, consistency_check.py, reverse_dcf.py,
│                     altman_z.py, piotroski_score.py, financial_model.py,
│                     peer_analysis.py, simple_hmm.py, self_consistency.py
├── knowledge/      ← library_index.py, library_search.py, library_sources.py
├── reporting/      ← render.py, color_safety.py, validate_custom_html.py,
│                     lineage.py
└── tracking/       ← track_record.py, monitor_kill_criteria.py
```

Every internal import is now package-qualified (e.g.
`from data.data_fetch import ...`), with an `__init__.py` per subpackage.
This was executed incrementally, one subpackage per commit with the full
test suite green after each step, per the guidance in
`docs/decisions/ADR-005-flat-layout-for-initial-migration.md` (which
covers why the initial migration deliberately started flat instead).
`lineage.py` was not originally listed in this diagram — folded into
`reporting/` since it feeds the source-provenance manifest into the same
final-report-assembly pipeline stage as `render.py` (see the pipeline
overview above). The four scripts meant to be run directly as
`python src/<subpkg>/<file>.py` (`agent/analyst_agent.py`,
`tracking/track_record.py`, `tracking/monitor_kill_criteria.py`,
`knowledge/library_index.py`) each start with a small `sys.path` shim,
active only when run as `__main__`, so that invocation keeps working
without needing `python -m` or a manually-set `PYTHONPATH`.

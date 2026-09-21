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
     Claude can call 9 tools (news search, playbook/library search,
     financial projection, sensitivity analysis, Monte Carlo, commodity
     price, FX rate, consistency-assessment, event price reaction)
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

## Module map (current flat `src/` layout)

| File | Responsibility |
|---|---|
| `analyst_agent.py` | Orchestrates the whole pipeline end to end |
| `framework.py` | The 18-section prompt, all writing rules, chart-type documentation |
| `tools.py` | The 9 tools Claude can call during analysis |
| `render.py` | Renders the final HTML report; 29 chart types |
| `data_fetch.py` | yfinance-based data: prices, VaR, Sharpe/Sortino, rolling beta, HMM regime detection, options-implied-volatility analysis |
| `sec_data.py` | SEC EDGAR financials (primary US source) + Form 4 insider transaction parsing |
| `fmp_data.py` | Financial Modeling Prep — fallback for non-US filers |
| `fred_data.py` | Macro data (fed funds rate, 10Y yield, CPI, unemployment) |
| `commodity_data.py` | Alpha Vantage commodity/FX prices |
| `finra_data.py` | Short interest data — see `docs/data-sources.md` for its schema-discovery approach and reliability caveat |
| `forensics.py` | 7 forensic accounting flags + ~15 deterministically-computed verified metrics |
| `consistency_check.py` | Deterministic checks that the report's text/charts match the real computed values |
| `reverse_dcf.py` | Market-implied growth rate / WACC read; also forward intrinsic value estimate |
| `altman_z.py` | Altman Z-Score (bankruptcy risk screen) |
| `piotroski_score.py` | Piotroski F-Score (fundamental quality screen) |
| `financial_model.py` | 3-scenario projection engine, sensitivity analysis, Monte Carlo simulation |
| `peer_analysis.py` | Peer comparison (only when the user supplies peer tickers) |
| `track_record.py` | Full per-ticker analysis history; structured kill-criteria extraction; cross-report calibration score |
| `self_consistency.py` | Self-consistency sampling (Wang et al., 2022) applied to *subjective* judgments (e.g. an economic-moat score) — asks Claude the same question 3x independently and takes the median |
| `lineage.py` | Builds the source-provenance manifest included in every report |
| `simple_hmm.py` | Own pure-NumPy Gaussian HMM (Baum-Welch + Viterbi) — replaces `hmmlearn` |
| `library_index.py` | Occasional script: indexes PDFs/articles/YouTube transcripts into the searchable library |
| `library_search.py` | Runtime semantic search over that library (Voyage AI HTTP API for embeddings) |
| `library_sources.py` | Fetches/extracts text from a URL (YouTube transcript or article) for indexing |
| `color_safety.py` | Objective WCAG contrast-ratio calculation — guards against unreadable color combinations |
| `validate_custom_html.py` | Safety net for the one chart type where Claude writes free-form HTML/CSS |
| `monitor_kill_criteria.py` | Standalone script: checks fresh data against stored kill-criteria thresholds without running a full report |

## Target structure (not yet executed — see `docs/roadmap.md`)

```
src/
├── agent/          ← analyst_agent.py
├── framework/      ← framework.py, tools.py
├── data/           ← data_fetch.py, sec_data.py, fmp_data.py,
│                     fred_data.py, commodity_data.py, finra_data.py
├── analysis/       ← forensics.py, consistency_check.py, reverse_dcf.py,
│                     altman_z.py, piotroski_score.py, financial_model.py,
│                     peer_analysis.py, simple_hmm.py, self_consistency.py
├── knowledge/      ← library_index.py, library_search.py, library_sources.py
├── reporting/      ← render.py, color_safety.py, validate_custom_html.py
└── tracking/       ← track_record.py, monitor_kill_criteria.py
```

This requires updating every internal import (currently flat, e.g.
`from data_fetch import ...`) to the new package paths, plus `__init__.py`
files per subpackage. Deliberately deferred as a first, test-verified
Claude Code task rather than attempted blind during the initial migration
— see `docs/decisions/ADR-005-flat-layout-for-initial-migration.md`.

# Current Project State

**Last updated:** 2026-09-21 (at the point of migration into this repository)

## Current architecture

See `docs/architecture.md`. Flat `src/` layout (see
`docs/decisions/ADR-005-flat-layout-for-initial-migration.md`), 27 Python
modules plus a test suite.

## Completed

- Full 18-section report pipeline, end to end, verified against multiple
  real tickers (across different sectors, capital structures, and data
  quality situations — including at least one pre-revenue company, one
  SPAC-merger-year anomaly, and one company with a Congressional-inquiry
  situation).
- 4-reviewer + 2-correction-round quality control, with `NEEDS_REVIEW`
  flagging confirmed working correctly on genuinely messy real data.
- Deterministic verified-metrics layer (~15 ratios), 7 forensic checks,
  Altman Z, Piotroski F, reverse-DCF, scenario/sensitivity/Monte Carlo
  analysis.
- VaR, Sharpe/Sortino, rolling beta, HMM regime detection (own
  implementation), options-implied volatility, short interest, insider
  transactions.
- Source-provenance (lineage) manifest, brand-color lookup, 29 chart
  types.
- Per-ticker full history (`track_record.py`), structured kill-criteria,
  cross-report calibration score, standalone kill-criteria monitor.
- Semantic library/RAG search (Voyage AI), replacing an earlier local-
  model approach that hit a Windows dependency wall.
- 238 tests passing.

## Currently working on / just finished

- This migration itself — moving the project from a single long chat
  session into this repository.

## Known problems

- `finra_data.py` (short interest) is unverified against live data — see
  `docs/data-sources.md`.
- See `docs/roadmap.md` item 1 — the `src/` subfolder reorganization is
  outstanding.

## Next priorities

1. Verify `finra_data.py` against real tickers.
2. Reorganize `src/` into subpackages (incrementally, test-verified).
3. Pick up an item from `docs/roadmap.md` — the "what changed since last
   analysis" diff is likely the highest-value next feature.

## Open questions needing the project owner's input

See `docs/roadmap.md`'s final section and `docs/rejected-alternatives.md`
for the evidence-tiering question specifically.

# Roadmap

Ordered roughly by what makes sense to tackle first — not a strict
priority ranking beyond item 1.

## 1. Subfolder reorganization of `src/` — DONE

Executed incrementally, one subpackage per commit with the full test
suite green after each step, as this item asked. See
`docs/decisions/ADR-005-flat-layout-for-initial-migration.md` for why it
wasn't done during the initial migration, and `docs/architecture.md`
for the resulting structure and module map.

## 2. Verify `finra_data.py` against live data

Never tested against a real FINRA response during development (no
network access in that environment). Run `python src/finra_data.py
TICKER` for a few real tickers and confirm the schema-discovery approach
actually finds the right fields. See `docs/data-sources.md`.

## 3. "What changed since the last analysis" diff

A deterministic comparison (not an LLM judgment) of the current
`verified_metrics` against the most recent prior snapshot for the same
ticker, surfaced automatically on a re-analysis — e.g. "operating margin
11.2% → 14.8%, 2 forensic flags resolved, net debt down $200M." Discussed
and wanted; not yet designed or built.

## 4. Schedule `monitor_kill_criteria.py`

Currently a manually-run script. Wiring it into a scheduler (cron, n8n,
or similar) so it runs automatically (e.g. weekly) across the whole
tracked portfolio was discussed as a natural next step but never
attempted.

## 5. A visible "data quality scorecard"

A compact summary shown in the report's hero section: how many verified
metrics were available, how many unresolved discrepancies remain, how
many correction rounds were needed. All of this data is already computed
internally during report generation — this is purely a presentation
addition, not a new data source.

## 6. Segment-level financial data

Deliberately deferred — see `docs/rejected-alternatives.md` for why.
Worth its own focused session if pursued, given the real technical risk
involved (see that entry for detail).

## 7. Multi-agent architecture

The project owner's stated long-term intent: once this single agent is
"solid," extend into a sector agent, a macro agent, and an orchestrator
— possibly answering free-text questions via a router+synthesis layer,
rather than only producing the fixed 18-section report. No concrete
trigger has been defined yet for when the single-agent version counts as
"solid enough" to start this. Two things flagged as relevant when this
work starts:

- The calculation/data layer (`data_fetch.py`, `sec_data.py`, etc.) is
  already reasonably decoupled from the report-generation logic
  (`framework.py`, `analyst_agent.py`) — a good sign for reuse across
  multiple future agents.
- Once an orchestrator starts invoking this agent for smaller, narrower
  questions rather than always requesting a full 18-section report,
  always running the full pipeline (4 reviewers, 2 correction rounds)
  would be a mismatch for that use case. A lighter invocation mode will
  likely be needed then — not designed yet, and not needed until that
  point.

## Open, undecided items (not exactly roadmap, but worth resolving)

- Whether to adopt the A–E evidence-tiering / source-hierarchy system —
  see `docs/rejected-alternatives.md`.
- Whether the 1-hour prompt-cache TTL actually reduced cost as intended —
  the project owner said he'd test this; no result has been reported back
  as of this migration.

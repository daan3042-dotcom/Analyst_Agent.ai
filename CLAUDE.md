# CLAUDE.md — TCE Financial Analyst Agent

This file is read automatically at the start of every Claude Code session
in this repository. It is the permanent project briefing — treat it as
higher-priority context than anything you infer from the code alone.

## What this project is

A Python pipeline that takes a stock ticker and produces a structured,
18-section HTML equity research report, combining real financial data
with Claude's analytical writing. Built for **The Collective Edge (TCE)**,
a two-person trading/investment operation. Full detail: `docs/architecture.md`.

## The one rule that matters most

**Python computes, Claude narrates.** Every number in a report that can be
calculated deterministically — a margin, a ratio, a growth rate, a
Monte Carlo distribution — is computed once, in Python, and handed to
Claude as a finished value. Claude never re-derives a number from raw
data itself. This is not a style preference; it was adopted after real,
observed cases of Claude silently computing a wrong figure. Any change
that reintroduces LLM arithmetic for something Python could compute
instead should be treated as a regression, not a simplification.

## Standing priorities (in order)

1. **Correctness and professionalism over token cost.** The project's
   owner (DD) has repeatedly and explicitly prioritized output quality
   over API spend. Do not propose a cost optimization that trades away
   reliability without flagging that tradeoff explicitly.
2. **No compiled/native dependencies where avoidable.** The development
   machine (Windows, a very new Python version) has repeatedly hit a
   security policy that blocks compiled extensions built from source.
   Three real incidents (`sentence-transformers`, the `voyageai` SDK,
   `hmmlearn`) were each replaced with a direct HTTP call or a from-scratch
   pure-NumPy implementation. Before adding any new dependency, check
   whether it (or its transitive dependencies) includes compiled/native
   code — see `docs/decisions/ADR-003-http-api-over-sdk-for-native-deps.md`.
3. **Deterministic checks over trust.** When a bug is found where the LLM
   output didn't match the real computed value, fix it with a code-level
   check in `consistency_check.py` that verifies the final text/chart
   against the actual value — not only a stronger prompt instruction.

## Where to find things (current, flat `src/` layout — see note below)

| Looking for... | File |
|---|---|
| Pipeline orchestration | `src/analyst_agent.py` |
| The 18-section prompt & writing rules | `src/framework.py` |
| Tools Claude can call | `src/tools.py` |
| HTML rendering / chart types | `src/render.py` |
| SEC financials + insider transactions | `src/sec_data.py` |
| Non-US financials fallback | `src/fmp_data.py` |
| Macro data | `src/fred_data.py` |
| Market data, VaR/Sharpe/beta/regime/options | `src/data_fetch.py` |
| Short interest | `src/finra_data.py` |
| Forensic checks + verified metrics | `src/forensics.py` |
| Output-vs-reality consistency checks | `src/consistency_check.py` |
| Scenario/sensitivity/Monte Carlo | `src/financial_model.py` |
| Per-ticker history + calibration score | `src/track_record.py` |
| Standalone kill-criteria monitor | `src/monitor_kill_criteria.py` |
| Library/RAG search | `src/library_search.py`, `src/library_index.py` |
| Tests | `tests/test_agent.py` |
| Architecture decisions (the "why") | `docs/decisions/` |
| Full architecture | `docs/architecture.md` |
| Current status / open items | `docs/project-state.md` |

**Note on the flat layout:** `src/` is currently flat (all files are
siblings, using plain imports like `from data_fetch import ...`) — not
yet split into the `src/agent/`, `src/data/`, `src/analysis/` subfolders
described in `docs/architecture.md`'s target structure. That split is a
deliberately deferred first task (see `docs/roadmap.md`) — it touches
every file's imports, and was left for a dedicated, test-verified Claude
Code session rather than done blind during the initial migration.

## Before making a change

- Read `docs/decisions/` for anything relevant — a design choice that
  looks suboptimal from the code alone often has a documented reason.
- Run `pytest` (uses `pytest.ini`'s `pythonpath = src`, no manual setup
  needed) before and after any change.
- New deterministic logic should come with a test in `tests/test_agent.py`
  — ideally one realistic "correct" case and one "known bug" regression
  case, following the existing pattern in that file.
- If you're about to add a new pip dependency, check priority #2 above
  first.

## What NOT to do without asking

- Don't reduce the 4-reviewer / 2-correction-round quality control
  pipeline to save cost — DD has explicitly declined this tradeoff twice.
- Don't let `NEEDS_REVIEW` reports be treated as a bug to "fix away" —
  it's a deliberate signal that a report needs human review, and several
  real runs have confirmed it fires correctly on genuinely messy company
  data.
- Don't add directional/opinionated language outside Section 18 of a
  report — see `docs/architecture.md`'s section on the 18-section
  structure.
- Don't silently swallow an error from a data source — the established
  pattern is: catch it, return `{"error": "..."}`, log it, and let the
  rest of the pipeline continue with whatever data is available.

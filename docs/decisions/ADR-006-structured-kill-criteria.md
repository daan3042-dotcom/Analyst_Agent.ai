# ADR-006 — Structured, Machine-Checkable Kill-Criteria

## Status
Accepted

## Decision
The `kill-criteria-recap` chart type (rendered at the end of Section 17
of every report) can now carry, per criterion, an optional structured
form — `{description, metric_key, operator, threshold}` — alongside or
instead of a plain human-readable sentence. `metric_key` must match a
real key from the report's `verified_metrics`.

## Reason
The cross-report calibration score (see ADR-004) needs to mechanically
check "was this kill-criterion breached?" against later data. The
original kill-criteria were stored purely as prose sentences (e.g.
"Capex within 15% of the PFS estimate = neutral/positive signal, 30%+
overrun = negative signal") — readable by a human, but not something
code could evaluate. This decision adds a structured, optional
machine-checkable form specifically to unlock that.

## Alternatives considered
Having Claude (or a separate LLM call) *judge*, after the fact, whether a
given prose kill-criterion was breached by comparing it against new data.
Rejected as inconsistent with this project's core principle (ADR-001) —
introducing LLM judgment into what should be a deterministic, auditable
calibration score defeats its purpose.

## Consequences
- Not every kill-criterion can be structured this way — qualitative
  criteria (e.g. "loss of a specific government contract") have no
  corresponding `verified_metrics` key and must stay as plain prose. This
  is expected and fine; the calibration score only counts what's
  structured.
- Reports generated *before* this change have no structured criteria at
  all — `compute_calibration_score()` and `monitor_kill_criteria.py` will
  correctly report "nothing to check yet" for those, not an error. The
  calibration score only starts accumulating meaningful data going
  forward from this change.
- `render.py`'s renderer for this chart type accepts both the old
  (plain-string) and new (structured-object) forms per criterion, so
  older report-generation code paths and older saved reports keep
  rendering correctly.

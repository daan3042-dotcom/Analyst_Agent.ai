# ADR-004 — Keep Full Per-Ticker History, Not Just the Latest Snapshot

## Status
Accepted

## Decision
`track_record.py` stores every analysis ever run for a given ticker as an
append-only list, rather than overwriting the previous snapshot on each
re-run.

## Reason
The original, simpler design only kept the most recent snapshot per
ticker — enough to support "did our last set of kill-criteria hold up?"
narration within a single re-analysis. It was changed specifically to
make a **cross-report calibration score** possible: a score that answers
"across every company we've ever analyzed, and every kill-criterion we
ever set, how often did the thesis actually hold vs. get breached?" This
requires the full history, not just the latest point.

## Alternatives considered
Keeping only the latest snapshot (the original design) and accepting that
a calibration score simply wasn't buildable. Rejected once it became
clear the calibration score was a priority — the marginal cost of storing
full history (a JSON array instead of a single object, per ticker) is
low.

## Consequences
- `load_previous_report()` still returns only the *latest* snapshot, to
  keep the existing "does this re-analysis address prior kill-criteria"
  narration in `framework.py` working unchanged.
- A new `load_full_history()` and `compute_calibration_score()` were
  added to `track_record.py` for the new cross-report use case.
- Backward compatibility: older, single-snapshot track-record files
  (written before this change) are read transparently — the loading
  logic treats a bare object as a one-item history rather than requiring
  a manual migration step.
- This ADR is tightly coupled to ADR-006 (structured kill-criteria) —
  full history alone isn't enough for an automated calibration score;
  the criteria themselves also needed a machine-checkable form.

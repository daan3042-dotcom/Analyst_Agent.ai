# ADR-001 — Python Computes, Claude Narrates

## Status
Accepted (founding principle — the single most load-bearing decision in
this project)

## Decision
Every number in a report that can be calculated deterministically (a
margin, a ratio, a growth rate, a Monte Carlo distribution, a VaR figure)
is computed once, in Python, and handed to Claude as a finished,
"verified" value. Claude never re-derives such a number from raw data
itself — it only narrates and interprets a value it's already been given.

## Reason
Observed real cases, during development, of Claude silently computing an
incorrect ratio (e.g. an operating margin) when left to do the arithmetic
itself from raw figures — a subtle, hard-to-catch failure mode, since the
output reads confidently correct.

## Alternatives considered
Trusting Claude's own arithmetic, with a reviewer pass to catch errors
after the fact. Rejected: catching an arithmetic error after generation is
strictly worse than preventing it — a reviewer might miss a subtle miscalculation,
and even when caught it costs a full correction round.

## Consequences
- Every new metric added to a report needs a corresponding Python
  function, not just a prompt instruction.
- `forensics.py`'s `verified_metrics` and the various dedicated modules
  (`altman_z.py`, `piotroski_score.py`, `reverse_dcf.py`,
  `financial_model.py`, `data_fetch.py`'s risk functions) exist
  specifically to keep this rule enforceable.
- `consistency_check.py` exists as a second line of defense: even with
  this rule, Claude occasionally miscopies or fabricates a number when
  transcribing a verified value into prose or a chart — this module
  catches that class of bug deterministically, at the text/chart level,
  rather than trusting the rule alone.

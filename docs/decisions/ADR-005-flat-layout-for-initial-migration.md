# ADR-005 — Flat `src/` Layout for the Initial Migration

## Status
Accepted (for the initial migration only — see Consequences)

## Decision
This migration copies all Python modules into a single, flat `src/`
directory, preserving every existing plain import (`from data_fetch
import ...`) exactly as-is, rather than reorganizing into the
`src/agent/`, `src/data/`, `src/analysis/` subfolder structure shown as
the eventual target in `docs/architecture.md`.

## Reason
Splitting into subfolders requires rewriting every internal import across
every file (dozens of cross-module imports) to the new dotted paths, plus
adding `__init__.py` files per subpackage. This is a genuine refactor,
not a mechanical file move, and the migration plan this project followed
explicitly warns against refactoring during the initial migration step:
get a verified, working baseline into version control first, then do
structural changes as separate, reviewable, test-verified commits.

## Alternatives considered
Doing the full subfolder reorganization immediately, verifying it via the
existing test suite before considering the migration complete. Rejected
for this initial migration specifically because the party doing the
reorganization (an AI assistant working from a chat transcript, without
the ability to have the project owner run and confirm the result on their
own machine in real time) judged the "verify then hand off" cycle safer
done incrementally by Claude Code, working directly in the repository
with full test-and-revert capability, rather than attempted all at once
outside that environment.

## Consequences
- The flat layout was verified working before hand-off: the full test
  suite passes via `pytest.ini`'s `pythonpath = src`, and running
  `python src/analyst_agent.py --help` from the repository root produces
  the expected argument-parsing behavior with no import errors.
- The subfolder reorganization is the first item in `docs/roadmap.md`,
  explicitly scoped as its own task.
- Anyone picking up that task should do it incrementally (e.g. one
  subpackage at a time) and re-run the full test suite after each step,
  rather than moving every file at once.

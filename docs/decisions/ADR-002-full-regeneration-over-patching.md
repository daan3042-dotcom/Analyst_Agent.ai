# ADR-002 — Full Regeneration Over Patching for Self-Correction

## Status
Accepted

## Decision
When the review pipeline finds issues, the correction step regenerates the
entire report text, rather than applying targeted patches to only the
flagged sentences/sections.

## Reason
Patching was seriously considered as a cost-saving measure (a full
regeneration costs far more output tokens than a small patch). It was
rejected for three concrete risks, identified during design discussion:

1. **Fragile matching.** A patch requires Claude to quote the exact
   original text to replace; any small mismatch (a slightly different
   wording than what's actually in the report) means the patch silently
   fails to apply — with no error, just a missed fix.
2. **Context blindness.** The kind of cross-reference bug this project's
   4th reviewer ("Kruisverwijzing") specifically hunts for — the same
   fact stated inconsistently in two different sections — requires
   catching and fixing *every* occurrence. A full regeneration naturally
   re-derives a consistent narrative; a set of isolated patches can easily
   miss one of several affected locations.
3. **Ripple effects.** Fixing one figure can require a related but
   unflagged sentence elsewhere to change too (e.g. correcting a growth
   rate in Section 5 might require Section 13's summary to shift in
   framing). A full regeneration handles this implicitly; a patch does
   not.

## Alternatives considered
Patch-based correction (rejected, see above). A hybrid — patch only for
small, isolated, single-location fixes, full regeneration otherwise — was
raised as a possible middle ground but not pursued, given the added
complexity of deciding which category a given issue falls into.

## Consequences
- Each correction round costs roughly as much as the initial generation
  in output tokens. Given the project's standing "quality over cost"
  priority (see `docs/requirements.md`), this was judged an acceptable
  tradeoff.
- `MAX_REVISION_ROUNDS` is capped at 2; reducing it further, or narrowing
  what the 4 reviewers re-check after round 1 to only the changed
  sections, were both proposed as further cost optimizations and both
  explicitly declined for the same reason.

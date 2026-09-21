# ADR-003 — Prefer a Direct HTTP API Call Over a Vendor SDK

## Status
Accepted

## Decision
When a third-party service's only practical advantage via its official
SDK package is convenience (as opposed to functionality genuinely
unavailable over a plain HTTP call), call its REST API directly with
`requests` instead of adding the SDK as a dependency.

## Reason
Three real, separate incidents on the development machine (Windows, a
very new Python release many packages don't yet ship prebuilt wheels
for), each costing significant debugging time:

1. **`sentence-transformers`** (used for the library/RAG feature's local
   embeddings) pulled in `scipy` and `scikit-learn`, both of which have
   compiled extensions. A Windows security policy (Smart App Control)
   blocked those compiled files from loading, crashing the entire agent
   at startup — not just the library feature — because `tools.py`
   imports the library module unconditionally at load time.
2. **The `voyageai` SDK package** (the intended replacement for
   `sentence-transformers`, chosen specifically to avoid compiled
   dependencies) turned out to pull in `langchain_core`, which pulls in
   `uuid_utils`, a Rust-compiled package — hitting the *same* Windows
   block via a completely different dependency chain.
3. **`hmmlearn`** (regime detection) had no prebuilt wheel for the
   installed Python version and failed to build from source, requiring a
   C++ compiler the development machine didn't have.

In cases 1 and 2, the fix was to call the provider's REST API directly
with `requests` (already a proven, reliable dependency in this project)
instead of using their SDK. In case 3, no HTTP API existed for the
underlying algorithm, so a from-scratch pure-NumPy implementation
(`simple_hmm.py`) was written instead — the same underlying principle:
avoid pulling in compiled code that isn't already known to work.

## Alternatives considered
Installing Microsoft's C++ Build Tools to allow compiled packages to
build from source. Rejected as disproportionate (a multi-GB install) for
a single feature, and it doesn't fully solve the problem — the OS-level
policy blocking compiled *pre-built* wheels (not just source builds)
would still be a risk for other packages in the future.

## Consequences
- Before adding any new dependency, check whether it (or its transitive
  dependencies) contains compiled/native code. This is now called out
  explicitly in `CLAUDE.md`.
- `library_search.py`'s imports are wrapped defensively (try/except) so
  that even a future, currently-unforeseen import failure disables only
  that one feature rather than crashing the whole pipeline — a second,
  independent layer of protection against this class of failure.
- `simple_hmm.py` was verified against the same synthetic test scenario
  originally used to validate `hmmlearn`'s output, to confirm comparable
  accuracy before being trusted as its replacement.

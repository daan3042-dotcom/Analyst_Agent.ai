# Data Sources

| Source | Used for | Key required | Notes |
|---|---|---|---|
| SEC EDGAR (`data.sec.gov`) | Primary US financials, Form 4 insider transactions | No — requires a descriptive User-Agent header | Most authoritative source; preferred over all others when available |
| Financial Modeling Prep (FMP) | Financials for non-US filers (SEC fallback) | `FMP_API_KEY` | Same data shape as the SEC path, so the rest of the pipeline doesn't need to distinguish |
| Yahoo Finance (via `yfinance`) | Market data, price history, options chains | No | Also used as a fallback when SEC/FMP are missing a figure — always explicitly labeled as a separate source when used, never silently merged with SEC data |
| FRED | Macro data: fed funds rate, 10Y treasury yield, CPI, unemployment | `FRED_API_KEY` | — |
| Alpha Vantage | Commodity and FX prices | `ALPHAVANTAGE_API_KEY` | — |
| FINRA (`api.finra.org`) | Short interest | No | **Reliability caveat below — read before trusting this in production.** |
| Voyage AI (`api.voyageai.com`) | Embeddings for the library/RAG search feature | `VOYAGE_API_KEY` | Called via raw HTTP (`requests`), not the `voyageai` SDK — see ADR-003 |
| Anthropic API | The analysis/report-writing itself | `ANTHROPIC_API_KEY` | — |

## FINRA short interest — reliability caveat

`finra_data.py`'s dataset name (`equityShortInterestStandardized`) and
field names were determined from FINRA's public documentation and
historical API-change announcements, **not from a live test** — the
development environment had no network access to FINRA's API. To reduce
the risk of the exact field names having changed since, the code fetches
FINRA's own `/metadata` endpoint first and matches fields by keyword
rather than hardcoding names outright; if the expected fields aren't
found, it returns a clear error rather than silently using the wrong
field. **This is the single least-verified integration in the project.**
Before relying on it: run `python src/finra_data.py TICKER` for a few
real, liquid tickers and confirm the output looks sensible.

## Why SEC is preferred over Yahoo Finance when both have a figure

SEC (or FMP for non-US filers) is the primary, authoritative source
throughout this pipeline. Yahoo Finance is used only for what SEC
doesn't provide (live market price, market cap, forward-looking
multiples) or as an explicit fallback. When both sources disagree on
something, the report is expected to say so and prefer the SEC/FMP
figure — this is enforced by prompt instruction in `framework.py`, not by
a separate deterministic check.

## A general principle worth preserving

A multiple that combines a market price with a fundamental figure (e.g.
EV/EBITDA, P/E, P/B) is *never* purely "SEC-verified," even when the
fundamental half of the calculation is — the market-price half always
comes from a different source. This distinction was added to
`framework.py` after a real reviewer-caught bug where a computed multiple
was mislabeled as fully SEC-sourced.

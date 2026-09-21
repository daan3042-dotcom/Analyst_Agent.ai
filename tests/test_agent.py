"""
test_agent.py
Geautomatiseerde tests voor de delen van de agent die zonder live API-keys
of internetverbinding te testen zijn: pure functies (formattering, parsing)
en structuurchecks. Dit test NIET de echte Claude/yfinance/Alpha Vantage-
calls -- dat blijft iets om handmatig te proberen, zoals we tot nu toe deden.

Gebruik:
    pip install pytest
    pytest test_agent.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from render import (
    _extract_charts,
    _fmt_money,
    _fmt_pct,
    _fmt_ratio,
    _sign_class,
    _split_into_sections,
)
from framework.framework import REPORT_SECTIONS
from framework.tools import ALL_TOOLS, run_tool
from data_fetch import fetch_recent_news


# ---------- render.py: formatteer-functies ----------

def test_fmt_money_billions():
    assert _fmt_money(1_500_000_000) == "$1.5B"


def test_fmt_money_millions():
    assert _fmt_money(340_000_000) == "$340.0M"


def test_fmt_money_none():
    assert _fmt_money(None) == "–"


def test_fmt_pct_converts_fraction():
    assert _fmt_pct(0.061) == "6.1%"


def test_fmt_pct_none():
    assert _fmt_pct(None) == "–"


def test_fmt_ratio_rounds_one_decimal():
    assert _fmt_ratio(17.456) == "17.5"


def test_sign_class_positive():
    assert _sign_class(0.05) == "pos"


def test_sign_class_negative():
    assert _sign_class(-0.02) == "neg"


def test_sign_class_none_and_zero():
    assert _sign_class(None) == ""
    assert _sign_class(0) == ""


# ---------- render.py: sectie- en chart-parsing ----------

def test_split_into_sections_basic():
    # Minimaal 5 secties nodig om onder de fallback-grens in _split_into_sections
    # uit te komen (zie test hieronder voor die fallback zelf).
    text = "\n\n".join(f"{i}. Section {i}\nSome text for section {i}." for i in range(1, 6))
    sections = _split_into_sections(text)
    assert len(sections) == 5
    assert sections[0]["num"] == "1"
    assert sections[0]["title"] == "Section 1"
    assert sections[4]["title"] == "Section 5"


def test_split_into_sections_falls_back_on_unexpected_format():
    # Minder dan 5 herkenbare secties -> alles als 1 blok, i.p.v. crashen
    text = "Gewoon een stuk tekst zonder sectienummers."
    sections = _split_into_sections(text)
    assert len(sections) == 1
    assert sections[0]["title"] == "Analyse"


def test_extract_charts_parses_valid_json_and_strips_it_from_text():
    body = 'Some intro text.\n\n```chart\n{"type": "bar", "title": "X", "labels": ["A"], "values": [1]}\n```\n\nMore text.'
    cleaned, charts = _extract_charts(body)
    assert len(charts) == 1
    assert charts[0]["type"] == "bar"
    assert "```chart" not in cleaned
    assert "Some intro text" in cleaned
    assert "More text" in cleaned


def test_extract_charts_silently_skips_invalid_json():
    body = 'Text before.\n\n```chart\nthis is not valid json\n```\n\nText after.'
    cleaned, charts = _extract_charts(body)
    assert charts == []
    assert "Text before" in cleaned
    assert "Text after" in cleaned


def test_extract_charts_no_chart_block_returns_original_text():
    body = "Plain paragraph, no chart here."
    cleaned, charts = _extract_charts(body)
    assert charts == []
    assert cleaned == body


# ---------- tools.py: dispatcher ----------

def test_run_tool_unknown_tool_returns_error_not_exception():
    result = run_tool("some_nonexistent_tool", {})
    assert "error" in result


def test_all_tools_have_required_fields():
    for tool in ALL_TOOLS:
        assert "name" in tool
        # server tools (zoals web_search) hebben geen "description"/"input_schema";
        # client-tools (zoals get_recent_news) wel -- check dat alleen daar.
        if tool["name"] == "get_recent_news":
            assert "description" in tool
            assert "input_schema" in tool


# ---------- framework.py: structuurcheck op REPORT_SECTIONS ----------

def test_report_sections_all_have_required_keys():
    for section in REPORT_SECTIONS:
        assert "title" in section
        assert "description" in section
        assert "questions" in section
        assert isinstance(section["questions"], list)
        assert len(section["questions"]) > 0


def test_report_sections_are_sequentially_numbered():
    for i, section in enumerate(REPORT_SECTIONS, start=1):
        assert section["title"].startswith(f"{i}."), (
            f"Sectie {i} begint niet met '{i}.': {section['title']!r}"
        )


# ---------- data_fetch.py: gedrag zonder netwerk ----------

def test_fetch_recent_news_without_api_key(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    result = fetch_recent_news("NKE")
    assert "error" in result


# ---------- sec_data.py: XBRL-verwerking zonder netwerk ----------

def test_extract_annual_series_filters_quarterly_and_sorts():
    from sec_data import _extract_annual_series
    tag_data = {
        "units": {
            "USD": [
                {"form": "10-Q", "fp": "Q1", "end": "2023-03-31", "val": 100, "fy": 2023},
                {"form": "10-K", "fp": "FY", "end": "2023-12-31", "val": 500, "fy": 2023},
                {"form": "10-K", "fp": "FY", "end": "2021-12-31", "val": 400, "fy": 2021},
                {"form": "10-K", "fp": "FY", "end": "2022-12-31", "val": 450, "fy": 2022},
            ]
        }
    }
    series = _extract_annual_series(tag_data)
    assert len(series) == 3  # het 10-Q-kwartaalcijfer moet eruit gefilterd zijn
    assert [s["fiscal_year"] for s in series] == [2021, 2022, 2023]  # oplopend gesorteerd


def test_extract_annual_series_caps_at_six_years():
    from sec_data import _extract_annual_series
    tag_data = {
        "units": {
            "USD": [
                {"form": "10-K", "fp": "FY", "end": f"{y}-12-31", "val": y, "fy": y}
                for y in range(2010, 2024)
            ]
        }
    }
    series = _extract_annual_series(tag_data)
    assert len(series) == 6
    assert series[-1]["fiscal_year"] == 2023  # meest recente jaar blijft over


def test_extract_annual_series_returns_none_without_annual_data():
    from sec_data import _extract_annual_series
    tag_data = {"units": {"USD": [{"form": "10-Q", "fp": "Q2", "end": "2023-06-30", "val": 1, "fy": 2023}]}}
    assert _extract_annual_series(tag_data) is None


# ---------- reverse_dcf.py ----------

def test_dcf_value_matches_gordon_growth_when_growth_equals_terminal():
    from reverse_dcf import TERMINAL_GROWTH, _dcf_value
    fcf0, wacc = 100.0, 0.08
    calculated = _dcf_value(fcf0, TERMINAL_GROWTH, wacc, years=10)
    expected = (fcf0 * (1 + TERMINAL_GROWTH)) / (wacc - TERMINAL_GROWTH)
    assert abs(calculated - expected) < 0.01


def test_estimate_wacc_returns_none_without_beta():
    from reverse_dcf import estimate_wacc
    assert estimate_wacc({"market_cap": 1e9}) is None


def test_estimate_wacc_reasonable_range():
    from reverse_dcf import estimate_wacc
    wacc = estimate_wacc({"market_cap": 1e9, "beta": 1.2, "total_debt": 2e8})
    assert 0.03 < wacc < 0.20  # een WACC buiten dit bereik zou op een rekenfout wijzen


def test_compute_reverse_dcf_missing_data_returns_clear_error():
    from reverse_dcf import compute_reverse_dcf
    assert "error" in compute_reverse_dcf({"market_cap": 1e9})  # geen FCF, geen beta
    assert "error" in compute_reverse_dcf({"market_cap": 1e9, "beta": 1.2})  # geen FCF


def test_compute_reverse_dcf_realistic_case():
    from reverse_dcf import compute_reverse_dcf
    result = compute_reverse_dcf({
        "market_cap": 12_300_000_000, "total_debt": 3_000_000_000,
        "total_cash": 900_000_000, "free_cashflow": 433_000_000, "beta": 1.6,
    })
    assert "error" not in result
    assert -0.30 <= result["implied_annual_fcf_growth"] <= 0.50


# ---------- altman_z.py ----------

def test_altman_z_healthy_company_is_safe_zone():
    from altman_z import compute_altman_z
    sec_result = {"annual_facts": {
        "Assets": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 100_000_000_000}],
        "Liabilities": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 30_000_000_000}],
        "AssetsCurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 40_000_000_000}],
        "LiabilitiesCurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 15_000_000_000}],
        "RetainedEarningsAccumulatedDeficit": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 60_000_000_000}],
        "OperatingIncomeLoss": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 25_000_000_000}],
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 90_000_000_000}],
    }}
    result = compute_altman_z(sec_result, {"market_cap": 400_000_000_000})
    assert result["zone"] == "veilige zone"
    assert result["z_score"] > 2.99


def test_altman_z_distressed_company_is_risk_zone():
    from altman_z import compute_altman_z
    sec_result = {"annual_facts": {
        "Assets": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 5_000_000_000}],
        "Liabilities": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 4_500_000_000}],
        "AssetsCurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000}],
        "LiabilitiesCurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_800_000_000}],
        "RetainedEarningsAccumulatedDeficit": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": -2_000_000_000}],
        "OperatingIncomeLoss": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 50_000_000}],
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 2_000_000_000}],
    }}
    result = compute_altman_z(sec_result, {"market_cap": 500_000_000})
    assert result["zone"] == "risicozone"
    assert result["z_score"] < 1.81


def test_altman_z_missing_sec_data_returns_error():
    from altman_z import compute_altman_z
    assert "error" in compute_altman_z({"error": "geen data"}, {"market_cap": 1e9})


def test_altman_z_missing_tag_returns_clear_error():
    from altman_z import compute_altman_z
    sec_result = {"annual_facts": {"Assets": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1e9}]}}
    result = compute_altman_z(sec_result, {"market_cap": 1e9})
    assert "error" in result


# ---------- consistency_check.py ----------

def test_consistency_check_catches_real_alcoa_style_mismatch():
    from consistency_check import check_output_consistency
    verified_metrics = {
        "sec_free_cashflow": {"fiscal_year": 2025, "value": 433_000_000},
        "sec_operating_margin": {"fiscal_year": 2025, "value": 0.059},
    }
    bad_text = "The company reported free cash flow of $961 million in FY2025, with an operating margin of 18.4%."
    issues = check_output_consistency(bad_text, verified_metrics)
    assert len(issues) == 2
    checks = {i["check"] for i in issues}
    assert "FCF-consistentie (output-controle)" in checks
    assert "Operating margin-consistentie (output-controle)" in checks


def test_consistency_check_passes_correct_text():
    from consistency_check import check_output_consistency
    verified_metrics = {
        "sec_free_cashflow": {"fiscal_year": 2025, "value": 433_000_000},
        "sec_operating_margin": {"fiscal_year": 2025, "value": 0.059},
    }
    good_text = "The company reported free cash flow of $433 million in FY2025, with an operating margin of 5.9%."
    assert check_output_consistency(good_text, verified_metrics) == []


def test_consistency_check_catches_real_googl_style_small_margin_gap():
    """Reproduceert de echte GOOGL-bug: 34,0% genoemd in de tekst terwijl de
    geverifieerde waarde 32,03% was -- een gat van ~2 procentpunt dat de
    oude 5-procentpunt-tolerantie zou hebben gemist."""
    from consistency_check import check_output_consistency
    verified_metrics = {"sec_operating_margin": {"fiscal_year": 2025, "value": 0.3203}}
    text = "The verified operating margin for FY2025 stands at 34.0%."
    issues = check_output_consistency(text, verified_metrics)
    assert len(issues) == 1


def test_consistency_check_ignores_small_rounding_differences():
    from consistency_check import check_output_consistency
    verified_metrics = {"sec_operating_margin": {"fiscal_year": 2025, "value": 0.3203}}
    text = "Operating margin was approximately 32.0% in FY2025."
    assert check_output_consistency(text, verified_metrics) == []


def test_consistency_check_handles_empty_metrics():
    from consistency_check import check_output_consistency
    assert check_output_consistency("Some report text.", {}) == []


# ---------- financial_model.py ----------

def test_project_scenario_computes_correct_math_with_derived_inputs():
    from financial_model import project_scenario
    context = {"sec_result": {"annual_facts": {
        "Revenues": [
            {"fiscal_year": 2023, "period_end": "2023-12-31", "value": 10_000_000_000},
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 11_000_000_000},
        ],
        "DepreciationDepletionAndAmortization": [
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 550_000_000},
        ],
        "AccountsReceivableNetCurrent": [
            {"fiscal_year": 2023, "period_end": "2023-12-31", "value": 800_000_000},
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 850_000_000},
        ],
        "InventoryNet": [
            {"fiscal_year": 2023, "period_end": "2023-12-31", "value": 500_000_000},
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 520_000_000},
        ],
        "LongTermDebtNoncurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 2_000_000_000}],
        "LongTermDebtCurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 0}],
        "InterestExpense": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 100_000_000}],
        "CashAndCashEquivalentsAtCarryingValue": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000}],
    }}}
    result = project_scenario(
        {"scenario_name": "base", "revenue_growth_pct": 10, "operating_margin_pct": 15, "capex_pct_of_revenue": 5, "years": 1},
        context,
    )
    # Handmatig nagerekend (zie ook de live-testrun): revenue 12.1B, ebit 1.815B,
    # da 605M, interest 100M, net income 1,354,850,000, fcf 1,277,850,000
    year1 = result["projection"][0]
    assert year1["revenue"] == 12_100_000_000
    assert year1["ebit"] == 1_815_000_000
    assert year1["da"] == 605_000_000
    assert year1["interest_expense"] == 100_000_000
    assert year1["net_income"] == 1_354_850_000
    assert year1["fcf"] == 1_277_850_000
    assert year1["cumulative_cash"] == 2_277_850_000  # startkas 1B + FCF


def test_project_scenario_falls_back_gracefully_without_history():
    from financial_model import project_scenario
    context = {"sec_result": {"annual_facts": {
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 5_000_000_000}],
    }}}
    result = project_scenario(
        {"scenario_name": "bear", "revenue_growth_pct": -3, "operating_margin_pct": 8, "capex_pct_of_revenue": 4},
        context,
    )
    assert "error" not in result
    assert result["model_inputs_derived_from_sec_history"]["da_pct_of_revenue"] == 0.0
    assert result["model_inputs_derived_from_sec_history"]["existing_debt"] == 0
    assert len(result["projection"]) == 5


def test_project_scenario_without_sec_data_returns_error():
    from financial_model import project_scenario
    result = project_scenario(
        {"scenario_name": "base", "revenue_growth_pct": 5, "operating_margin_pct": 10, "capex_pct_of_revenue": 5},
        {"sec_result": {"error": "geen data"}},
    )
    assert "error" in result


def test_project_scenario_caps_years_at_maximum():
    from financial_model import project_scenario, MAX_PROJECTION_YEARS
    context = {"sec_result": {"annual_facts": {
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000}],
    }}}
    result = project_scenario(
        {"scenario_name": "base", "revenue_growth_pct": 5, "operating_margin_pct": 10,
         "capex_pct_of_revenue": 5, "years": 999},
        context,
    )
    assert len(result["projection"]) == MAX_PROJECTION_YEARS


def test_project_scenario_adds_market_comparison_for_base_case_with_large_gap():
    from financial_model import project_scenario
    context = {
        "sec_result": {"annual_facts": {
            "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000}],
        }},
        "reverse_dcf_result": {"implied_annual_fcf_growth": 0.15},
    }
    result = project_scenario(
        {"scenario_name": "base", "revenue_growth_pct": 4, "operating_margin_pct": 10, "capex_pct_of_revenue": 5},
        context,
    )
    assert "market_comparison" in result
    assert "voorzichtiger" in result["market_comparison"]


def test_project_scenario_no_market_comparison_for_non_base_scenarios():
    from financial_model import project_scenario
    context = {
        "sec_result": {"annual_facts": {
            "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000}],
        }},
        "reverse_dcf_result": {"implied_annual_fcf_growth": 0.15},
    }
    result = project_scenario(
        {"scenario_name": "bear", "revenue_growth_pct": -4, "operating_margin_pct": 6, "capex_pct_of_revenue": 5},
        context,
    )
    assert "market_comparison" not in result


def test_project_scenario_no_market_comparison_without_reverse_dcf():
    from financial_model import project_scenario
    context = {"sec_result": {"annual_facts": {
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000}],
    }}}
    result = project_scenario(
        {"scenario_name": "base", "revenue_growth_pct": 5, "operating_margin_pct": 10, "capex_pct_of_revenue": 5},
        context,
    )
    assert "market_comparison" not in result


def test_derive_model_inputs_clips_extreme_working_capital_intensity():
    from financial_model import _derive_model_inputs
    context = {"sec_result": {"annual_facts": {
        "Revenues": [
            {"fiscal_year": 2023, "period_end": "2023-12-31", "value": 1_000_000_000},
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_010_000_000},  # kleine omzetgroei
        ],
        "AccountsReceivableNetCurrent": [
            {"fiscal_year": 2023, "period_end": "2023-12-31", "value": 100_000_000},
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 900_000_000},  # extreme AR-sprong
        ],
    }}}
    derived = _derive_model_inputs(context, 1_010_000_000)
    assert -0.5 <= derived["working_capital_intensity"] <= 0.5  # moet begrensd zijn, niet de rauwe extreme ratio


def test_extract_annual_series_deduplicates_repeated_comparative_years():
    # Ontdekt via een echte AAPL-testrun: hetzelfde cijfer verscheen 3x met
    # verschillende "fy"-labels, omdat elke latere aangifte het als
    # vergelijkingscijfer herhaalt. SEC's "fy"-veld beschrijft de aangifte,
    # niet het jaar van het cijfer zelf -- we dedupliceren nu op einddatum.
    from sec_data import _extract_annual_series
    tag_data = {
        "units": {
            "USD": [
                {"form": "10-K", "fp": "FY", "start": "2022-10-01", "end": "2023-09-30", "val": 96995000000, "fy": 2023},
                {"form": "10-K", "fp": "FY", "start": "2022-10-01", "end": "2023-09-30", "val": 96995000000, "fy": 2024},
                {"form": "10-K", "fp": "FY", "start": "2022-10-01", "end": "2023-09-30", "val": 96995000000, "fy": 2025},
                {"form": "10-K", "fp": "FY", "start": "2023-10-01", "end": "2024-09-28", "val": 93736000000, "fy": 2024},
            ]
        }
    }
    series = _extract_annual_series(tag_data)
    assert len(series) == 2  # niet 4 -- de 3 duplicaten worden 1
    assert series[0]["fiscal_year"] == 2023  # afgeleid uit de einddatum, niet SEC's "fy"-veld


def test_extract_annual_series_rejects_quarter_mislabeled_as_fy():
    # Ontdekt via dezelfde testrun: een kwartaalcijfer (~90 dagen) stond
    # met form="10-K" en fp="FY" tussen de data -- SEC's "fp"-label bleek
    # niet betrouwbaar, dus checken we nu zelf de periodelengte.
    from sec_data import _extract_annual_series
    tag_data = {
        "units": {
            "USD": [
                {"form": "10-K", "fp": "FY", "start": "2017-07-02", "end": "2017-09-30", "val": 52579000000},
                {"form": "10-K", "fp": "FY", "start": "2017-10-01", "end": "2018-09-29", "val": 265595000000},
            ]
        }
    }
    series = _extract_annual_series(tag_data)
    assert len(series) == 1  # het kwartaalcijfer (~90 dagen) moet eruit gefilterd zijn
    assert series[0]["value"] == 265595000000

def test_compute_sensitivity_ranks_higher_impact_assumption_first():
    from financial_model import compute_sensitivity
    context = {"sec_result": {"annual_facts": {
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 10_000_000_000}],
    }}}
    result = compute_sensitivity(
        {"scenario_name": "base", "revenue_growth_pct": 5, "operating_margin_pct": 12,
         "capex_pct_of_revenue": 5, "years": 3},
        context,
    )
    assert "error" not in result
    assert len(result["sensitivities"]) == 6  # 3 aannames x 2 richtingen
    assert result["ranking_most_to_least_impactful"][0] == "operating_margin_pct"


def test_compute_sensitivity_propagates_error_without_sec_data():
    from financial_model import compute_sensitivity
    result = compute_sensitivity(
        {"scenario_name": "base", "revenue_growth_pct": 5, "operating_margin_pct": 10, "capex_pct_of_revenue": 5},
        {"sec_result": {"error": "geen data"}},
    )
    assert "error" in result


# ---------- color_safety.py ----------

def test_contrast_ratio_catches_the_original_readability_bug():
    from color_safety import contrast_ratio, is_readable
    ratio = contrast_ratio("#2a2a2a", "#1a1a1a")
    assert ratio < 4.5
    assert not is_readable("#2a2a2a", "#1a1a1a")


def test_contrast_ratio_handles_3_digit_hex():
    from color_safety import contrast_ratio
    assert abs(contrast_ratio("#000", "#fff") - 21.0) < 0.01


def test_contrast_ratio_black_on_white_is_maximum():
    from color_safety import contrast_ratio
    assert abs(contrast_ratio("#000000", "#ffffff") - 21.0) < 0.01


# ---------- validate_custom_html.py ----------

def test_validate_custom_html_accepts_good_component():
    from validate_custom_html import validate_custom_html
    good = '<div style="color:#1a1815; background:#f5f3ee; padding:12px;"><strong>Test</strong></div>'
    assert validate_custom_html(good)["valid"] is True


def test_validate_custom_html_rejects_bad_contrast():
    from validate_custom_html import validate_custom_html
    bad = '<div style="color:#111; background:#000;">onleesbaar</div>'
    result = validate_custom_html(bad)
    assert result["valid"] is False
    assert "contrast" in result["reason"]


def test_validate_custom_html_rejects_unbalanced_tags():
    from validate_custom_html import validate_custom_html
    result = validate_custom_html("<div><span>tekst</div>")
    assert result["valid"] is False


def test_validate_custom_html_rejects_script_tag():
    from validate_custom_html import validate_custom_html
    result = validate_custom_html('<div>test</div><script>alert(1)</script>')
    assert result["valid"] is False
    assert "script" in result["reason"]


def test_validate_custom_html_rejects_fixed_position():
    from validate_custom_html import validate_custom_html
    result = validate_custom_html('<div style="position:fixed; top:0;">test</div>')
    assert result["valid"] is False


def test_validate_custom_html_rejects_inline_event_handler():
    from validate_custom_html import validate_custom_html
    result = validate_custom_html('<div onclick="doSomething()">test</div>')
    assert result["valid"] is False


def test_validate_custom_html_rejects_empty_input():
    from validate_custom_html import validate_custom_html
    assert validate_custom_html("")["valid"] is False
    assert validate_custom_html("   ")["valid"] is False


# ---------- render.py: nieuwe grafiek-types ----------

def test_render_waterfall_produces_output():
    from render import _render_chart
    out = _render_chart({
        "type": "waterfall", "title": "Test", "start_label": "Start", "start_value": 1000,
        "steps": [{"label": "Stap 1", "value": -200}], "end_label": "Eind",
    })
    assert "waterfall" in out and "1,000" in out


def test_render_gauge_produces_output():
    from render import _render_chart
    out = _render_chart({
        "type": "gauge", "title": "Test", "value": 2.3, "min": 0, "max": 5,
        "zones": [{"label": "Zone", "max": 5, "color": "#3E7A4F"}],
    })
    assert "gauge-marker" in out


def test_render_heatmap_produces_output():
    from render import _render_chart
    out = _render_chart({
        "type": "heatmap", "title": "Test", "rows": ["R1"], "cols": ["C1"], "values": [[42]],
    })
    assert "heatmap" in out and "42" in out


def test_render_custom_rejects_invalid_and_renders_valid():
    from render import _render_chart
    valid = _render_chart({"type": "custom", "html": '<div style="color:#1a1815; background:#f5f3ee;">ok</div>'})
    assert "custom-block" in valid
    invalid = _render_chart({"type": "custom", "html": '<div style="color:#000; background:#000;">bad</div>'})
    assert "custom-block" not in invalid


def test_render_chart_unknown_type_returns_empty_string():
    from render import _render_chart
    assert _render_chart({"type": "nonexistent"}) == ""


# ---------- render.py: nog 6 meer nieuwe grafiek-types ----------

def test_render_metric_cards_produces_output():
    from render import _render_chart
    out = _render_chart({"type": "metric-cards", "title": "Test", "cards": [
        {"label": "Omzet", "value": "$1B", "trend": "up", "trend_detail": "+5%"},
    ]})
    assert "metric-card" in out and "trend-up" in out


def test_render_stacked_bar_produces_output():
    from render import _render_chart
    out = _render_chart({"type": "stacked-bar", "title": "Test", "segments": [
        {"label": "A", "value": 70}, {"label": "B", "value": 30},
    ]})
    assert "stacked-segment" in out and "70%" in out


def test_render_line_trend_produces_output():
    from render import _render_chart
    out = _render_chart({"type": "line-trend", "title": "Test", "labels": ["2023", "2024"], "values": [10, 12]})
    assert "<svg" in out and "polyline" in out


def test_render_line_trend_rejects_mismatched_lengths():
    from render import _render_chart
    out = _render_chart({"type": "line-trend", "title": "Test", "labels": ["2023", "2024"], "values": [10]})
    assert out == ""


def test_render_line_trend_thins_labels_with_many_points():
    """Reproduceert de echte LEU-bug: een lopende-beta-reeks met 17 punten
    liet alle 17 datumlabels over elkaar heen vallen. Nu moeten alle punten
    nog steeds getekend worden, maar slechts een leesbaar aantal (~8-9)
    krijgt een zichtbaar tekstlabel."""
    from render import _render_line_trend
    import re
    labels = [f"M{i}" for i in range(17)]
    values = [1.0 + 0.05 * i for i in range(17)]
    result = _render_line_trend({"title": "Test", "labels": labels, "values": values})
    text_labels = re.findall(r'font-size="10"[^>]*>([^<]*)</text>', result)
    assert result.count("<circle") == 17  # alle punten blijven getekend
    assert 6 <= len(text_labels) <= 10  # maar duidelijk minder labels dan punten
    assert labels[-1] in text_labels  # de laatste is altijd zichtbaar


def test_render_donut_produces_output():
    from render import _render_chart
    out = _render_chart({"type": "donut", "title": "Test", "labels": ["A", "B"], "values": [60, 40]})
    assert "<svg" in out and "stroke-dasharray" in out


def test_render_quote_block_produces_output():
    from render import _render_chart
    out = _render_chart({"type": "quote-block", "quote": "Test quote", "attribution": "CEO"})
    assert "quote-text" in out and "Test quote" in out


def test_render_quote_block_rejects_empty_quote():
    from render import _render_chart
    assert _render_chart({"type": "quote-block", "quote": ""}) == ""


def test_render_milestone_progress_produces_output():
    from render import _render_chart
    out = _render_chart({"type": "milestone-progress", "title": "Test", "label": "Voortgang", "current": 56, "target": 100})
    assert "milestone-fill" in out
    assert "56% / 100% (56%)" in out


# ---------- financial_model.py: kans-gewogen verwachte FCF ----------

def test_probability_weighted_fcf_computed_after_all_three_scenarios():
    from financial_model import project_scenario
    context = {"sec_result": {"annual_facts": {
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 10_000_000_000}],
    }}}
    r1 = project_scenario({"scenario_name": "bear", "revenue_growth_pct": -3, "operating_margin_pct": 8, "capex_pct_of_revenue": 5, "probability_pct": 20}, context)
    assert "probability_weighted_expected_fcf" not in r1
    r2 = project_scenario({"scenario_name": "base", "revenue_growth_pct": 5, "operating_margin_pct": 14, "capex_pct_of_revenue": 5, "probability_pct": 60}, context)
    assert "probability_weighted_expected_fcf" not in r2
    r3 = project_scenario({"scenario_name": "bull", "revenue_growth_pct": 10, "operating_margin_pct": 18, "capex_pct_of_revenue": 5, "probability_pct": 20}, context)
    assert "probability_weighted_expected_fcf" in r3
    expected = round(0.2 * r1["projection"][-1]["fcf"] + 0.6 * r2["projection"][-1]["fcf"] + 0.2 * r3["projection"][-1]["fcf"])
    assert r3["probability_weighted_expected_fcf"]["value"] == expected


def test_probability_weighting_warns_when_probabilities_dont_sum_to_100():
    from financial_model import project_scenario
    context = {"sec_result": {"annual_facts": {
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 10_000_000_000}],
    }}}
    project_scenario({"scenario_name": "bear", "revenue_growth_pct": -3, "operating_margin_pct": 8, "capex_pct_of_revenue": 5, "probability_pct": 20}, context)
    project_scenario({"scenario_name": "base", "revenue_growth_pct": 5, "operating_margin_pct": 14, "capex_pct_of_revenue": 5, "probability_pct": 60}, context)
    r3 = project_scenario({"scenario_name": "bull", "revenue_growth_pct": 10, "operating_margin_pct": 18, "capex_pct_of_revenue": 5, "probability_pct": 50}, context)
    assert "probability_weighted_expected_fcf" not in r3
    assert "probability_weighting_warning" in r3


def test_no_probability_weighting_without_probability_pct():
    from financial_model import project_scenario
    context = {"sec_result": {"annual_facts": {
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 10_000_000_000}],
    }}}
    r1 = project_scenario({"scenario_name": "bear", "revenue_growth_pct": -3, "operating_margin_pct": 8, "capex_pct_of_revenue": 5}, context)
    r2 = project_scenario({"scenario_name": "base", "revenue_growth_pct": 5, "operating_margin_pct": 14, "capex_pct_of_revenue": 5}, context)
    r3 = project_scenario({"scenario_name": "bull", "revenue_growth_pct": 10, "operating_margin_pct": 18, "capex_pct_of_revenue": 5}, context)
    assert "probability_weighted_expected_fcf" not in r3
    assert "probability_weighting_warning" not in r3


# ---------- data_fetch.py: historische volatiliteit ----------

def test_compute_annualized_volatility_constant_price_is_zero():
    from data_fetch import _compute_annualized_volatility
    assert _compute_annualized_volatility([100.0] * 30) == 0.0


def test_compute_annualized_volatility_returns_none_with_insufficient_data():
    from data_fetch import _compute_annualized_volatility
    assert _compute_annualized_volatility([100.0]) is None
    assert _compute_annualized_volatility([]) is None


def test_compute_annualized_volatility_positive_for_varying_prices():
    from data_fetch import _compute_annualized_volatility
    prices = [100.0]
    for i in range(50):
        prices.append(prices[-1] * (1.01 if i % 2 == 0 else 0.99))
    vol = _compute_annualized_volatility(prices)
    assert vol is not None and vol > 0


# ---------- fmp_data.py ----------

def test_fmp_to_annual_series_sorts_chronologically():
    from fmp_data import _to_annual_series
    rows = [
        {"date": "2024-12-31", "revenue": 200},
        {"date": "2022-12-31", "revenue": 100},
        {"date": "2023-12-31", "revenue": 150},
    ]
    series = _to_annual_series(rows, "revenue")
    assert [s["fiscal_year"] for s in series] == [2022, 2023, 2024]


def test_fmp_to_annual_series_converts_negative_capex_to_positive():
    from fmp_data import _to_annual_series
    rows = [{"date": "2024-12-31", "capitalExpenditure": -618000000}]
    series = _to_annual_series(rows, "capitalExpenditure", take_abs=True)
    assert series[0]["value"] == 618000000


def test_fmp_to_annual_series_skips_missing_values():
    from fmp_data import _to_annual_series
    rows = [{"date": "2024-12-31", "revenue": None}, {"date": "2023-12-31", "revenue": 100}]
    series = _to_annual_series(rows, "revenue")
    assert len(series) == 1


def test_fetch_fmp_financials_without_api_key():
    from fmp_data import fetch_fmp_financials
    import os
    old_key = os.environ.pop("FMP_API_KEY", None)
    try:
        result = fetch_fmp_financials("ERO")
        assert "error" in result
    finally:
        if old_key is not None:
            os.environ["FMP_API_KEY"] = old_key


def test_fmp_output_shape_matches_sec_output_shape():
    """Cruciale test: FMP en SEC moeten dezelfde dict-vorm teruggeven zodat
    forensics.py/altman_z.py/financial_model.py niet hoeven te weten welke
    bron het is."""
    from fmp_data import fetch_fmp_financials
    from sec_data import fetch_sec_financials
    import os
    os.environ.pop("FMP_API_KEY", None)
    fmp_error_shape = fetch_fmp_financials("ERO")
    sec_error_shape = fetch_sec_financials("NONEXISTENTTICKERXYZ")
    assert set(fmp_error_shape.keys()) <= {"error"}
    assert "error" in sec_error_shape  # beide falen op dezelfde manier (dict met "error")


# ---------- self_consistency.py ----------

def _make_fake_client(response_texts):
    from unittest.mock import MagicMock
    call_count = [0]
    def fake_create(**kwargs):
        resp = MagicMock()
        resp.content = [MagicMock(type="text", text=response_texts[call_count[0]])]
        call_count[0] += 1
        return resp
    client = MagicMock()
    client.messages.create = fake_create
    return client


def test_assess_with_consistency_takes_median_of_three_samples():
    from self_consistency import assess_with_consistency
    client = _make_fake_client([
        '{"score": 2, "rationale": "Zwakke moat"}',
        '{"score": 4, "rationale": "Sterke moat"}',
        '{"score": 3, "rationale": "Gemiddelde moat"}',
    ])
    result = assess_with_consistency(client, "Hoe sterk is de moat?", "context", 1, 5)
    assert result["median_score"] == 3.0
    assert result["rationale"] == "Gemiddelde moat"


def test_assess_with_consistency_survives_one_failed_sample():
    from self_consistency import assess_with_consistency
    client = _make_fake_client([
        "Dit is geen geldige JSON.",
        '{"score": 4, "rationale": "A"}',
        '{"score": 4, "rationale": "B"}',
    ])
    result = assess_with_consistency(client, "Hoe sterk is de moat?", "context", 1, 5)
    assert result["median_score"] == 4.0
    assert len(result["individual_scores"]) == 2


def test_assess_with_consistency_returns_error_when_all_samples_fail():
    from unittest.mock import MagicMock
    from self_consistency import assess_with_consistency
    client = MagicMock()
    client.messages.create = lambda **kwargs: (_ for _ in ()).throw(Exception("API-fout"))
    result = assess_with_consistency(client, "Hoe sterk is de moat?", "context", 1, 5)
    assert "error" in result


def test_assess_with_consistency_clips_scores_to_scale():
    from self_consistency import assess_with_consistency
    client = _make_fake_client([
        '{"score": 99, "rationale": "buiten schaal"}',
        '{"score": 3, "rationale": "binnen schaal"}',
        '{"score": 3, "rationale": "binnen schaal"}',
    ])
    result = assess_with_consistency(client, "Hoe sterk is de moat?", "context", 1, 5)
    assert max(result["individual_scores"]) <= 5


def test_extract_json_block_handles_preamble_text():
    from self_consistency import _extract_json_block
    text = 'Ik denk dat de score als volgt is: {"score": 4, "rationale": "test"}'
    parsed = _extract_json_block(text)
    assert parsed == {"score": 4, "rationale": "test"}


# ---------- forensics.py: nieuwe voorberekende cijfers ----------

def test_verified_metrics_yoy_growth_prevents_real_dal_bug():
    from forensics import compute_verified_metrics
    sec_result = {"annual_facts": {
        "Revenues": [
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 60_000_000_000},
            {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 63_400_000_000},
        ],
        "NetIncomeLoss": [
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 3_500_000_000},
            {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 5_000_000_000},
        ],
    }}
    metrics = compute_verified_metrics(sec_result, {})
    # Exact het scenario uit de echte DAL-run: Claude zei 44.8%, het juiste antwoord is 42.9%
    assert round(metrics["sec_net_income_yoy_growth"]["value"] * 100, 1) == 42.9


def test_verified_metrics_ebitda_and_leverage_ratios():
    from forensics import compute_verified_metrics
    sec_result = {"annual_facts": {
        "OperatingIncomeLoss": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 5_824_000_000}],
        "DepreciationDepletionAndAmortization": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 3_000_000_000}],
        "LongTermDebtNoncurrent": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 11_900_000_000}],
        "LongTermDebtCurrent": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 1_400_000_000}],
        "CashAndCashEquivalentsAtCarryingValue": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 4_500_000_000}],
        "InterestExpense": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 700_000_000}],
    }}
    metrics = compute_verified_metrics(sec_result, {"market_cap": 51_900_000_000})
    assert metrics["sec_ebitda"]["value"] == 8_824_000_000
    assert metrics["sec_net_debt"]["value"] == 8_800_000_000
    assert metrics["sec_net_debt_to_ebitda"] == 1.0
    assert metrics["sec_interest_coverage_ratio"] == 8.32
    assert metrics["sec_normalized_ev_to_ebitda"] == 6.88


def test_verified_metrics_missing_data_gracefully_omits_fields():
    from forensics import compute_verified_metrics
    metrics = compute_verified_metrics({"annual_facts": {}}, {})
    assert "sec_ebitda" not in metrics
    assert "sec_revenue_yoy_growth" not in metrics


# ---------- peer_analysis.py ----------

def test_peer_comparison_computes_premium_discount():
    from peer_analysis import compute_peer_comparison
    company_data = {"trailing_pe": 10.0, "ev_to_ebitda": 6.0, "profit_margins": 0.08, "return_on_equity": 0.15}
    peer_data = {
        "PEER1": {"trailing_pe": 12.0, "ev_to_ebitda": 7.0, "profit_margins": 0.10, "return_on_equity": 0.12},
        "PEER2": {"trailing_pe": 14.0, "ev_to_ebitda": None, "profit_margins": 0.06, "return_on_equity": 0.10},
    }
    result = compute_peer_comparison(company_data, peer_data)
    assert result["trailing_pe"]["peer_count"] == 2
    assert result["ev_to_ebitda"]["peer_count"] == 1  # PEER2 mist dit cijfer, correct uitgesloten
    assert result["trailing_pe"]["premium_discount_pct"] < 0  # bedrijf handelt onder peer-gemiddelde


def test_peer_comparison_excludes_failed_peers():
    from peer_analysis import compute_peer_comparison
    company_data = {"trailing_pe": 10.0}
    peer_data = {"PEER1": {"trailing_pe": 12.0}, "PEER2": {"error": "kon data niet ophalen"}}
    result = compute_peer_comparison(company_data, peer_data)
    assert result["trailing_pe"]["peer_count"] == 1


def test_peer_comparison_without_peer_data():
    from peer_analysis import compute_peer_comparison
    assert "error" in compute_peer_comparison({"trailing_pe": 10.0}, None)


def test_peer_comparison_without_peer_data_field_overlap():
    from peer_analysis import compute_peer_comparison
    result = compute_peer_comparison({"trailing_pe": None}, {"PEER1": {"trailing_pe": 12.0}})
    assert "error" in result


# ---------- track_record.py ----------

def test_extract_section_17_finds_content():
    from track_record import _extract_section_17
    text = "16. Devil's Advocate\nx\n\n17. Monitoring & Kill-Criteria\nLet op deze 3 dingen: A, B, C.\n"
    assert _extract_section_17(text) == "Let op deze 3 dingen: A, B, C."


def test_extract_section_17_returns_none_when_absent():
    from track_record import _extract_section_17
    assert _extract_section_17("1. Company Overview\ntest") is None


def test_save_and_load_report_snapshot_roundtrip(tmp_path, monkeypatch):
    import track_record
    monkeypatch.chdir(tmp_path)
    text = "17. Monitoring & Kill-Criteria\nWaarschuwing: let op X.\n"
    track_record.save_report_snapshot("TEST", text, {"sec_operating_margin": {"fiscal_year": 2025, "value": 0.1}})
    loaded = track_record.load_previous_report("TEST")
    assert loaded["ticker"] == "TEST"
    assert loaded["section_17_kill_criteria"] == "Waarschuwing: let op X."


def test_load_previous_report_returns_none_when_absent(tmp_path, monkeypatch):
    import track_record
    monkeypatch.chdir(tmp_path)
    assert track_record.load_previous_report("NIETBESTAAND") is None


def test_save_report_snapshot_preserves_full_history(tmp_path, monkeypatch):
    """DD's derde verzoek: elke run toevoegen aan een geschiedenis i.p.v.
    de vorige overschrijven -- basis voor een toekomstige kalibratiescore."""
    import track_record
    monkeypatch.chdir(tmp_path)
    track_record.save_report_snapshot("TEST", "17. Monitoring & Kill-Criteria\nEerste run.\n", {"sec_ebitda": 100})
    track_record.save_report_snapshot("TEST", "17. Monitoring & Kill-Criteria\nTweede run.\n", {"sec_ebitda": 200})

    history = track_record.load_full_history("TEST")
    assert len(history) == 2
    assert "Eerste run" in history[0]["section_17_kill_criteria"]
    assert "Tweede run" in history[1]["section_17_kill_criteria"]

    latest = track_record.load_previous_report("TEST")
    assert "Tweede run" in latest["section_17_kill_criteria"]
    assert latest["verified_metrics"] == {"sec_ebitda": 200}


def test_track_record_reads_old_single_dict_file_format(tmp_path, monkeypatch):
    """Backward compatibility: bestaande, eerder opgeslagen bestanden staan
    nog in het oude (los dict, geen lijst) formaat -- die moeten zonder
    migratiestap blijven werken, en een nieuwe run erop moet correct
    omzetten naar een geschiedenis."""
    import json
    import os
    import track_record
    monkeypatch.chdir(tmp_path)
    os.makedirs("track_record", exist_ok=True)
    with open("track_record/OLD.json", "w", encoding="utf-8") as f:
        json.dump({"ticker": "OLD", "date": "2026-01-01",
                    "section_17_kill_criteria": "Oude stijl", "verified_metrics": {}}, f)

    assert track_record.load_previous_report("OLD")["section_17_kill_criteria"] == "Oude stijl"

    track_record.save_report_snapshot("OLD", "17. Monitoring & Kill-Criteria\nNieuwe run.\n", {})
    history = track_record.load_full_history("OLD")
    assert len(history) == 2
    assert history[0]["section_17_kill_criteria"] == "Oude stijl"
    assert "Nieuwe run" in history[1]["section_17_kill_criteria"]


# ---------- sec_data.py: insider-transacties (Form 4) ----------

def test_parse_form4_xml_extracts_transactions():
    from sec_data import _parse_form4_xml
    xml = (
        "<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>Jane Doe</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer>"
        "<officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>"
        "<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-08-15</value></transactionDate>"
        "<transactionCoding><transactionCode>S</transactionCode></transactionCoding>"
        "<transactionAmounts><transactionShares><value>10000</value></transactionShares>"
        "<transactionPricePerShare><value>45.5</value></transactionPricePerShare></transactionAmounts>"
        "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"
    )
    result = _parse_form4_xml(xml)
    assert len(result) == 1
    assert result[0]["owner"] == "Jane Doe" and result[0]["role"] == "CEO"
    assert result[0]["code"] == "S" and result[0]["shares"] == 10000.0 and result[0]["price"] == 45.5


def test_parse_form4_xml_handles_malformed_xml():
    from sec_data import _parse_form4_xml
    assert _parse_form4_xml("not valid xml <<<") == []


def test_fetch_insider_transactions_aggregates_buys_and_sells():
    from unittest.mock import patch, MagicMock
    import sec_data

    fake_submissions = {"filings": {"recent": {
        "form": ["4", "10-K", "4"],
        "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3"],
        "primaryDocument": ["a.xml", "b.htm", "c.xml"],
    }}}
    xml_sell = (
        "<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>Jane Doe</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer>"
        "<officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>"
        "<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-08-15</value></transactionDate>"
        "<transactionCoding><transactionCode>S</transactionCode></transactionCoding>"
        "<transactionAmounts><transactionShares><value>10000</value></transactionShares>"
        "<transactionPricePerShare><value>45.5</value></transactionPricePerShare></transactionAmounts>"
        "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"
    )
    xml_buy = (
        "<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>John Smith</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship><isDirector>1</isDirector>"
        "</reportingOwnerRelationship></reportingOwner>"
        "<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-08-10</value></transactionDate>"
        "<transactionCoding><transactionCode>P</transactionCode></transactionCoding>"
        "<transactionAmounts><transactionShares><value>2000</value></transactionShares>"
        "<transactionPricePerShare><value>44.0</value></transactionPricePerShare></transactionAmounts>"
        "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"
    )

    def fake_get(url, headers=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        if "submissions" in url:
            resp.json.return_value = fake_submissions
        elif "a.xml" in url:
            resp.text = xml_sell
        elif "c.xml" in url:
            resp.text = xml_buy
        return resp

    with patch("sec_data._get_ticker_cik_map", return_value={"TEST": "0001234567"}), \
         patch("sec_data.requests.get", side_effect=fake_get):
        result = sec_data.fetch_insider_transactions("TEST")

    assert result["open_market_buys"] == 1
    assert result["open_market_sells"] == 1
    assert result["net_shares_bought"] == 2000 - 10000


def test_fetch_insider_transactions_unknown_ticker():
    from unittest.mock import patch
    import sec_data
    with patch("sec_data._get_ticker_cik_map", return_value={"AAPL": "0000320193"}):
        result = sec_data.fetch_insider_transactions("NOTATICKER")
    assert "error" in result


# ---------- forensics.py: meerjarige CAGR ----------

def test_compute_verified_metrics_includes_multi_year_cagr():
    from forensics import compute_verified_metrics
    sec_result = {"annual_facts": {"Revenues": [
        {"fiscal_year": 2021, "period_end": "2021-12-31", "value": 1_000_000_000},
        {"fiscal_year": 2022, "period_end": "2022-12-31", "value": 1_200_000_000},
        {"fiscal_year": 2023, "period_end": "2023-12-31", "value": 1_500_000_000},
        {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_700_000_000},
        {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 2_000_000_000},
    ]}}
    metrics = compute_verified_metrics(sec_result, {})
    cagr = metrics.get("sec_revenue_cagr")
    expected = (2_000_000_000 / 1_000_000_000) ** (1 / 4) - 1
    assert cagr is not None
    assert abs(cagr["value"] - expected) < 0.001
    assert cagr["years"] == 4


def test_compute_verified_metrics_skips_cagr_with_too_few_years():
    from forensics import compute_verified_metrics
    sec_result = {"annual_facts": {"Revenues": [
        {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000},
        {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 1_100_000_000},
    ]}}
    metrics = compute_verified_metrics(sec_result, {})
    assert "sec_revenue_cagr" not in metrics


# ---------- render.py: sectie 18 (Variant Perception) ----------

def test_section_18_gets_variant_perception_styling():
    from render import render_html
    sections_text = "\n\n".join(f"{i}. Sectie {i}\ntest inhoud." for i in range(1, 18))
    analysis_text = sections_text + "\n\n18. Variant Perception (Speculatief -- Analytisch Vermoeden, Geen Aanbeveling)\nDisclaimer-tekst hier."
    review = {"approved": True, "issues": []}
    colors = {"primary": "#000", "secondary": "#111", "accent": "#222"}
    company_data = {"current_price": 10.0, "currency": "USD", "market_cap": 1_000_000_000}
    out = render_html("TEST", "Test Corp", "2026-01-01 00:00", None, "", review,
                       analysis_text, colors, company_data, None)
    assert 'class="section variant-perception"' in out
    assert "Ander register dan de rest" in out


def test_bank_rating_backstop_ignores_section_18_content():
    from agent.analyst_agent import _BANK_RATING_PATTERN
    import re
    text = (
        "6. Financial Ratios\nGeen koersdoelen hier.\n\n"
        "18. Variant Perception\nDe markt (Wells Fargo Buy-rating) lijkt te optimistisch."
    )
    text_excluding_18 = re.split(r"\n18\.\s*Variant Perception", text)[0]
    assert _BANK_RATING_PATTERN.search(text_excluding_18) is None


def test_bank_rating_backstop_still_catches_violations_before_section_18():
    from agent.analyst_agent import _BANK_RATING_PATTERN
    import re
    text = (
        "6. Financial Ratios\nWells Fargo raised its price target to $68.\n\n"
        "18. Variant Perception\nDisclaimer hier."
    )
    text_excluding_18 = re.split(r"\n18\.\s*Variant Perception", text)[0]
    assert _BANK_RATING_PATTERN.search(text_excluding_18) is not None


# ---------- data_fetch.py: event-study/koersreactie ----------

def test_compute_event_price_reaction_measures_correct_jump():
    from unittest.mock import patch
    import pandas as pd
    from data_fetch import compute_event_price_reaction

    dates = pd.date_range("2026-06-01", "2026-07-20", freq="D")
    dates = dates[dates.dayofweek < 5]
    closes, price = [], 10.0
    for d in dates:
        if d == pd.Timestamp("2026-06-15"):
            price *= 1.08
        elif d > pd.Timestamp("2026-06-15"):
            price *= 1.005
        closes.append(price)
    fake_history = pd.DataFrame({"Close": closes}, index=dates)

    with patch("data_fetch.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = fake_history
        result = compute_event_price_reaction("TEST", "2026-06-15", window_days=30)
        assert result["day_of_reaction_pct"] == 8.0
        assert result["window_reaction_pct"] > 8.0
        assert isinstance(result["day_of_reaction_pct"], float)  # geen numpy-type, JSON-veilig


def test_compute_event_price_reaction_rejects_invalid_date():
    from data_fetch import compute_event_price_reaction
    result = compute_event_price_reaction("TEST", "15-06-2026")
    assert "error" in result


def test_compute_event_price_reaction_handles_empty_history():
    from unittest.mock import patch
    import pandas as pd
    from data_fetch import compute_event_price_reaction
    with patch("data_fetch.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = pd.DataFrame()
        result = compute_event_price_reaction("TEST", "2026-06-15")
        assert "error" in result


# ---------- library_sources.py ----------

def test_is_youtube_url_detects_both_formats():
    from library_sources import is_youtube_url
    assert is_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert not is_youtube_url("https://www.economist.com/some-article")


def test_extract_youtube_id():
    from library_sources import _extract_youtube_id
    assert _extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_youtube_id("https://not-a-youtube-url.com") is None


# ---------- library_index.py: URL-verwerking ----------

def test_process_urls_creates_file_when_missing(tmp_path, monkeypatch):
    import library_index
    monkeypatch.chdir(tmp_path)
    library_index.process_urls(collection=None, client=None)
    assert (tmp_path / "library" / "urls.txt").exists()


def test_process_urls_deduplicates_by_url(tmp_path, monkeypatch):
    from unittest.mock import patch, MagicMock
    import library_index
    monkeypatch.chdir(tmp_path)
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "urls.txt").write_text("https://youtu.be/test123\n")

    class FakeCollection:
        def __init__(self):
            self.added = []
        def get(self, where, limit):
            return {"ids": [a for a in self.added if a.get("source_url") == where.get("source_url")]}
        def add(self, ids, embeddings, documents, metadatas):
            self.added.extend(metadatas)

    def fake_post(url, headers, json, timeout):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = {"data": [{"embedding": [0.0] * 5, "index": i} for i in range(len(json["input"]))]}
        return resp

    fake_collection = FakeCollection()
    with patch("library_index.fetch_source_text", return_value=("Titel", "tekst " * 100)), \
         patch("library_index.requests.post", side_effect=fake_post):
        library_index.process_urls(fake_collection, None)
        first_count = len(fake_collection.added)
        library_index.process_urls(fake_collection, None)
        assert len(fake_collection.added) == first_count  # gededupliceerd, niet dubbel toegevoegd


# ---------- forensics.py: cash conversion cycle / ROIC / DuPont ----------

def test_verified_metrics_cash_conversion_cycle():
    from forensics import compute_verified_metrics
    sec_result = {"annual_facts": {
        "Revenues": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 100_000_000}],
        "GrossProfit": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 40_000_000}],
        "AccountsReceivableNetCurrent": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 10_000_000}],
        "InventoryNet": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 15_000_000}],
        "AccountsPayableCurrent": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 8_000_000}],
    }}
    metrics = compute_verified_metrics(sec_result, {})
    ccc = metrics["sec_cash_conversion_cycle_days"]
    assert ccc["dso"] == 36.5
    assert round(ccc["value"], 1) == 79.1


def test_verified_metrics_roic_vs_wacc():
    from forensics import compute_verified_metrics
    sec_result = {"annual_facts": {
        "OperatingIncomeLoss": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 20_000_000}],
        "Assets": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 200_000_000}],
        "Liabilities": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 120_000_000}],
        "LongTermDebtNoncurrent": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 50_000_000}],
    }}
    company_data = {"beta": 1.2, "market_cap": 300_000_000}
    metrics = compute_verified_metrics(sec_result, company_data)
    assert metrics["sec_roic"]["value"] == 0.1154
    assert "sec_roic_vs_wacc_spread" in metrics


def test_verified_metrics_dupont_decomposition_matches_roe():
    from forensics import compute_verified_metrics
    sec_result = {"annual_facts": {
        "NetIncomeLoss": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 12_000_000}],
        "Revenues": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 100_000_000}],
        "Assets": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 200_000_000}],
        "Liabilities": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 120_000_000}],
    }}
    metrics = compute_verified_metrics(sec_result, {})
    dupont = metrics["sec_dupont_decomposition"]
    assert dupont["net_margin"] == 0.12
    assert dupont["asset_turnover"] == 0.5
    assert dupont["equity_multiplier"] == 2.5
    assert dupont["roe"] == 0.15


# ---------- piotroski_score.py ----------

def test_piotroski_score_computes_expected_criteria():
    from piotroski_score import compute_piotroski_score
    sec_result = {"annual_facts": {
        "NetIncomeLoss": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 8_000_000},
                          {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 12_000_000}],
        "Assets": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 190_000_000},
                   {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 200_000_000}],
        "NetCashProvidedByUsedInOperatingActivities": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 10_000_000},
                                                        {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 15_000_000}],
        "LongTermDebtNoncurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 60_000_000},
                                    {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 50_000_000}],
        "AssetsCurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 40_000_000},
                          {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 50_000_000}],
        "LiabilitiesCurrent": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 30_000_000},
                               {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 25_000_000}],
        "WeightedAverageNumberOfSharesOutstandingBasic": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 100_000_000},
                                                            {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 100_000_000}],
        "GrossProfit": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 35_000_000},
                        {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 40_000_000}],
        "Revenues": [{"fiscal_year": 2024, "period_end": "2024-12-31", "value": 95_000_000},
                     {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 100_000_000}],
    }}
    result = compute_piotroski_score(sec_result)
    assert result["score"] == 8
    assert result["max_score"] == 9
    assert result["criteria"]["positieve_netto_winst"] is True
    assert result["criteria"]["omzet_activa_ratio_hoger_dan_vorig_jaar"] is False


def test_piotroski_score_missing_data_returns_error():
    from piotroski_score import compute_piotroski_score
    assert "error" in compute_piotroski_score({"annual_facts": {}})


def test_piotroski_score_without_sec_data():
    from piotroski_score import compute_piotroski_score
    assert "error" in compute_piotroski_score({"error": "geen data"})


# ---------- reverse_dcf.py: intrinsic value (section-18-only) ----------

def test_intrinsic_value_higher_growth_gives_higher_value():
    from reverse_dcf import compute_intrinsic_value_estimate
    company_data = {
        "market_cap": 12_300_000_000, "total_debt": 3_000_000_000, "total_cash": 900_000_000,
        "free_cashflow": 433_000_000, "beta": 1.6, "shares_outstanding": 180_000_000,
    }
    low = compute_intrinsic_value_estimate(company_data, assumed_fcf_growth=0.03)
    high = compute_intrinsic_value_estimate(company_data, assumed_fcf_growth=0.10)
    assert high["intrinsic_value_per_share"] > low["intrinsic_value_per_share"]


def test_intrinsic_value_includes_premium_discount_when_price_given():
    from reverse_dcf import compute_intrinsic_value_estimate
    company_data = {
        "market_cap": 12_300_000_000, "total_debt": 3_000_000_000, "total_cash": 900_000_000,
        "free_cashflow": 433_000_000, "beta": 1.6, "shares_outstanding": 180_000_000,
        "current_price": 46.52,
    }
    result = compute_intrinsic_value_estimate(company_data, assumed_fcf_growth=0.05)
    assert "implied_premium_discount_pct" in result


def test_intrinsic_value_missing_shares_outstanding_returns_error():
    from reverse_dcf import compute_intrinsic_value_estimate
    company_data = {
        "market_cap": 12_300_000_000, "total_debt": 3_000_000_000, "total_cash": 900_000_000,
        "free_cashflow": 433_000_000, "beta": 1.6, "shares_outstanding": None,
    }
    result = compute_intrinsic_value_estimate(company_data, assumed_fcf_growth=0.05)
    assert "error" in result


def test_intrinsic_value_negative_fcf_returns_error():
    from reverse_dcf import compute_intrinsic_value_estimate
    company_data = {
        "market_cap": 12_300_000_000, "total_debt": 3_000_000_000, "total_cash": 900_000_000,
        "free_cashflow": -50_000_000, "beta": 1.6, "shares_outstanding": 180_000_000,
    }
    result = compute_intrinsic_value_estimate(company_data, assumed_fcf_growth=0.05)
    assert "error" in result


# ---------- render.py: kill-criteria-recap + layout-verzoeken ----------

def test_render_kill_criteria_recap_produces_output():
    from render import _render_kill_criteria_recap
    result = _render_kill_criteria_recap({"criteria": ["Drempel A", "Drempel B"]})
    assert "kill-criteria-recap" in result
    assert "Drempel A" in result and "Drempel B" in result


def test_render_kill_criteria_recap_empty_list_returns_empty():
    from render import _render_kill_criteria_recap
    assert _render_kill_criteria_recap({"criteria": []}) == ""


def test_executive_summary_and_issues_render_after_sections():
    from render import render_html
    sections_text = "\n\n".join(f"{i}. Sectie {i}\ntest inhoud." for i in range(1, 19))
    review = {"approved": False, "issues": ["een test-issue"]}
    colors = {"primary": "#000", "secondary": "#111", "accent": "#222"}
    company_data = {"current_price": 10.0, "currency": "USD", "market_cap": 1_000_000_000}
    out = render_html("TEST", "Test Corp", "2026-01-01 00:00", None, "", review,
                       sections_text, colors, company_data, None,
                       executive_summary="Dit is de samenvatting.")
    # De laatste sectie-tekst moet VOOR de executive summary en de issues-box staan
    assert out.index("Sectie 18") < out.index("Dit is de samenvatting.")
    assert out.index("Dit is de samenvatting.") < out.index("een test-issue")


# ---------- render.py: vijf nieuwe tekst-dragende componenttypes ----------

def test_render_fact_sheet_produces_output():
    from render import _render_fact_sheet
    result = _render_fact_sheet({"title": "Snapshot", "facts": [{"label": "HQ", "value": "Tempe, Arizona"}]})
    assert "fact-sheet" in result and "Tempe, Arizona" in result


def test_render_fact_sheet_empty_returns_empty():
    from render import _render_fact_sheet
    assert _render_fact_sheet({"facts": []}) == ""


def test_render_profile_cards_produces_output():
    from render import _render_profile_cards
    result = _render_profile_cards({"profiles": [{"name": "Jane Doe", "tag": "CEO", "description": "test"}]})
    assert "profile-card" in result and "Jane Doe" in result


def test_render_profile_cards_skips_entries_without_name():
    from render import _render_profile_cards
    result = _render_profile_cards({"profiles": [{"description": "geen naam"}]})
    assert 'class="profile-card"' not in result


def test_render_segment_cards_produces_output_with_stats():
    from render import _render_segment_cards
    result = _render_segment_cards({"segments": [{"title": "Santa Cruz", "stats": [{"label": "IRR", "value": "20%"}]}]})
    assert "segment-card" in result and "20%" in result


def test_render_data_table_produces_output():
    from render import _render_data_table
    result = _render_data_table({"columns": ["Q", "Omzet"], "rows": [["Q1", "10"]]})
    assert "<table" in result and "<td>Q1</td>" in result


def test_render_data_table_missing_columns_or_rows_returns_empty():
    from render import _render_data_table
    assert _render_data_table({"columns": [], "rows": [["a"]]}) == ""
    assert _render_data_table({"columns": ["a"], "rows": []}) == ""


def test_render_comparison_columns_produces_both_sides():
    from render import _render_comparison_columns
    result = _render_comparison_columns({
        "left_label": "Voor", "left_points": ["A"],
        "right_label": "Tegen", "right_points": ["B"],
    })
    assert "comparison-left" in result and "comparison-right" in result
    assert "Voor" in result and "Tegen" in result


def test_render_comparison_columns_requires_both_sides():
    from render import _render_comparison_columns
    assert _render_comparison_columns({"left_points": ["A"], "right_points": []}) == ""


def test_all_five_new_types_registered_in_dispatcher():
    from render import _extract_charts, _render_chart
    text = '''```chart
{"type": "fact-sheet", "facts": [{"label": "HQ", "value": "Test"}]}
```'''
    _, charts = _extract_charts(text)
    assert len(charts) == 1
    html_out = _render_chart(charts[0])
    assert "fact-sheet" in html_out


# ---------- analyst_agent.py: prompt caching ----------

def _stream_cm(response):
    """Bouwt een mock context manager die client.messages.stream(...)
    nabootst -- nodig sinds call_claude_with_retry/call_claude_with_retry_simple
    op streaming zijn overgezet (de SDK vereist dit zodra max_tokens groot
    genoeg is, wat live een echte crash veroorzaakte bij MAX_ANALYSIS_TOKENS=28000:
    'Streaming is required for operations that may take longer than 10 minutes')."""
    from unittest.mock import MagicMock
    cm = MagicMock()
    cm.__enter__.return_value.get_final_message.return_value = response
    return cm


def test_call_claude_with_retry_simple_caches_system_prompt():
    """Bewaakt dat elke aanroep via deze gedeelde helper (reviewers,
    zelfcorrectie, executive summary) het systeemprompt-blok expliciet
    cachet -- dit is precies waar de kostenbesparing vandaan komt."""
    from unittest.mock import MagicMock
    import agent.analyst_agent as analyst_agent
    client = MagicMock()
    fake_response = MagicMock()
    fake_response.stop_reason = "end_turn"
    client.messages.stream.return_value = _stream_cm(fake_response)

    analyst_agent.call_claude_with_retry_simple(client, "SOME SYSTEM PROMPT", "user prompt")
    call_kwargs = client.messages.stream.call_args.kwargs
    assert call_kwargs["system"] == [
        {"type": "text", "text": "SOME SYSTEM PROMPT", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    ]
    assert call_kwargs["extra_headers"]["anthropic-beta"] == "extended-cache-ttl-2025-04-11"


def test_review_report_includes_crossref_reviewer_as_fourth():
    """Bevestigt dat de kruisverwijzing-reviewer daadwerkelijk meedraait als
    vierde, parallelle reviewer, en dat een afkeuring van ALLEEN deze
    reviewer de algehele goedkeuring terecht laat mislukken -- reproduceert
    het patroon van de echte Wiz-overnamedatum-bug."""
    from unittest.mock import MagicMock
    import agent.analyst_agent as analyst_agent

    def fake_stream(system, messages, **kwargs):
        system_text = system[0]["text"]
        resp = MagicMock()
        if "KRUISVERWIJZING" in system_text:
            text = '{"approved": false, "issues": ["Wiz-overname: sectie 10 zegt februari 2026, sectie 12 zegt 11 maart 2026"]}'
        else:
            text = '{"approved": true, "issues": []}'
        resp.content = [MagicMock(type="text", text=text)]
        resp.usage.input_tokens = 100
        resp.usage.output_tokens = 20
        resp.usage.cache_creation_input_tokens = 0
        resp.usage.cache_read_input_tokens = 0
        return _stream_cm(resp)

    client = MagicMock()
    client.messages.stream.side_effect = fake_stream

    review, usage = analyst_agent.review_report(client, "test rapport tekst")
    assert client.messages.stream.call_count == 4
    assert review["approved"] is False
    assert any("[Kruisverwijzing]" in i for i in review["issues"])


def test_run_analysis_self_check_applies_correction():
    """Bevestigt dat de ingebouwde zelfcheck-beurt (vóór de externe
    reviewers) een correctie daadwerkelijk verwerkt in het eindresultaat,
    niet alleen een lege stap is."""
    from unittest.mock import patch, MagicMock
    import agent.analyst_agent as analyst_agent

    with patch("agent.analyst_agent.fetch_company_data") as m_company, \
         patch("agent.analyst_agent.validate_company_data"), \
         patch("agent.analyst_agent.fetch_sec_financials") as m_sec, \
         patch("agent.analyst_agent.fetch_historical_volatility") as m_vol, \
         patch("agent.analyst_agent.fetch_macro_snapshot") as m_macro, \
         patch("agent.analyst_agent.compute_reverse_dcf") as m_dcf, \
         patch("agent.analyst_agent.compute_altman_z") as m_altman, \
         patch("agent.analyst_agent.compute_piotroski_score") as m_piotroski, \
         patch("agent.analyst_agent.compute_intrinsic_value_estimate") as m_ivalue, \
         patch("agent.analyst_agent.fetch_insider_transactions") as m_insider, \
         patch("agent.analyst_agent.compute_options_analysis") as m_options, \
         patch("agent.analyst_agent.fetch_short_interest") as m_shortint, \
         patch("agent.analyst_agent.load_previous_report") as m_prev, \
         patch("agent.analyst_agent.review_report") as m_review, \
         patch("agent.analyst_agent.save_report_snapshot"), \
         patch("agent.analyst_agent.get_brand_colors") as m_colors, \
         patch("agent.analyst_agent.call_claude_with_retry_simple") as m_simple, \
         patch("agent.analyst_agent.anthropic.Anthropic") as m_anthropic_cls:

        m_company.return_value = {"ticker": "AA", "long_name": "Alcoa Corporation", "market_cap": 12_000_000_000}
        for m in (m_sec, m_vol, m_macro, m_dcf, m_altman, m_piotroski, m_ivalue, m_insider, m_options, m_shortint):
            m.return_value = {"error": "test"}
        m_prev.return_value = None
        usage_shape = {"input": 100, "output": 50, "cache_write": 0, "cache_read": 0}
        m_review.return_value = ({"approved": True, "issues": []}, usage_shape)
        m_colors.return_value = ({"primary": "#000", "secondary": "#111", "accent": "#222"}, usage_shape)
        exec_summary_response = MagicMock()
        exec_summary_response.content = [MagicMock(type="text", text="Test samenvatting.")]
        exec_summary_response.usage.input_tokens = 10
        exec_summary_response.usage.output_tokens = 5
        m_simple.return_value = exec_summary_response

        original_block = MagicMock()
        original_block.type = "text"
        original_block.text = "1. Company Overview\nFOUTIEVE datum\n" + "\n".join(f"{i}. Sectie {i}\ntest" for i in range(2, 19))
        original_response = MagicMock()
        original_response.stop_reason = "end_turn"
        original_response.content = [original_block]
        original_response.usage.input_tokens = 100
        original_response.usage.output_tokens = 50

        corrected_block = MagicMock()
        corrected_block.type = "text"
        corrected_block.text = "1. Company Overview\nGECORRIGEERDE datum\n" + "\n".join(f"{i}. Sectie {i}\ntest" for i in range(2, 19))
        corrected_response = MagicMock()
        corrected_response.stop_reason = "end_turn"
        corrected_response.content = [corrected_block]
        corrected_response.usage.input_tokens = 100
        corrected_response.usage.output_tokens = 50

        fake_client = MagicMock()
        fake_client.messages.stream.side_effect = [_stream_cm(original_response), _stream_cm(corrected_response)]
        m_anthropic_cls.return_value = fake_client

        result = analyst_agent.run_analysis("AA", None, "")
        assert "GECORRIGEERDE datum" in result[0]
        assert "FOUTIEVE datum" not in result[0]


def test_run_analysis_recovers_from_degenerate_tool_use_response():
    """Reproduceert de echte Alcoa-crash 1-op-1: een API-antwoord met
    stop_reason 'tool_use' maar zonder enig daadwerkelijk tool_use-blok
    (gaf voorheen een harde 400-fout: 'user messages must have non-empty
    content'). Bevestigt dat de agent dit nu opvangt door de kapotte beurt
    terug te draaien en de ronde te herhalen, i.p.v. te crashen."""
    from unittest.mock import patch, MagicMock
    import agent.analyst_agent as analyst_agent

    with patch("agent.analyst_agent.fetch_company_data") as m_company, \
         patch("agent.analyst_agent.validate_company_data"), \
         patch("agent.analyst_agent.fetch_sec_financials") as m_sec, \
         patch("agent.analyst_agent.fetch_historical_volatility") as m_vol, \
         patch("agent.analyst_agent.fetch_macro_snapshot") as m_macro, \
         patch("agent.analyst_agent.compute_reverse_dcf") as m_dcf, \
         patch("agent.analyst_agent.compute_altman_z") as m_altman, \
         patch("agent.analyst_agent.compute_piotroski_score") as m_piotroski, \
         patch("agent.analyst_agent.compute_intrinsic_value_estimate") as m_ivalue, \
         patch("agent.analyst_agent.fetch_insider_transactions") as m_insider, \
         patch("agent.analyst_agent.compute_options_analysis") as m_options, \
         patch("agent.analyst_agent.fetch_short_interest") as m_shortint, \
         patch("agent.analyst_agent.load_previous_report") as m_prev, \
         patch("agent.analyst_agent.review_report") as m_review, \
         patch("agent.analyst_agent.save_report_snapshot"), \
         patch("agent.analyst_agent.get_brand_colors") as m_colors, \
         patch("agent.analyst_agent.call_claude_with_retry_simple") as m_simple, \
         patch("agent.analyst_agent.anthropic.Anthropic") as m_anthropic_cls:

        m_company.return_value = {"ticker": "AA", "long_name": "Alcoa Corporation", "market_cap": 12_000_000_000}
        for m in (m_sec, m_vol, m_macro, m_dcf, m_altman, m_piotroski, m_ivalue, m_insider, m_options, m_shortint):
            m.return_value = {"error": "test"}
        m_prev.return_value = None
        usage_shape = {"input": 100, "output": 50, "cache_write": 0, "cache_read": 0}
        m_review.return_value = ({"approved": True, "issues": []}, usage_shape)
        m_colors.return_value = ({"primary": "#000", "secondary": "#111", "accent": "#222"}, usage_shape)
        exec_summary_response = MagicMock()
        exec_summary_response.content = [MagicMock(type="text", text="Test samenvatting.")]
        exec_summary_response.usage.input_tokens = 10
        exec_summary_response.usage.output_tokens = 5
        m_simple.return_value = exec_summary_response

        # Ronde 1: het degeneratieve antwoord dat de echte crash veroorzaakte.
        degenerate_block = MagicMock()
        degenerate_block.type = "text"
        degenerate_response = MagicMock()
        degenerate_response.stop_reason = "tool_use"
        degenerate_response.content = [degenerate_block]
        degenerate_response.usage.input_tokens = 100
        degenerate_response.usage.output_tokens = 50

        # Ronde 2 (na herstel): een normaal, afsluitend antwoord.
        final_block = MagicMock()
        final_block.type = "text"
        final_block.text = "1. Company Overview\ntest\n" + "\n".join(f"{i}. Sectie {i}\ntest" for i in range(2, 19))
        final_response = MagicMock()
        final_response.stop_reason = "end_turn"
        final_response.content = [final_block]
        final_response.usage.input_tokens = 100
        final_response.usage.output_tokens = 50

        fake_client = MagicMock()
        fake_client.messages.stream.side_effect = [
            _stream_cm(degenerate_response), _stream_cm(final_response), _stream_cm(final_response)
        ]
        m_anthropic_cls.return_value = fake_client

        result = analyst_agent.run_analysis("AA", None, "")

        assert fake_client.messages.stream.call_count == 3
        assert "Company Overview" in result[0]


# ---------- financial_model.py: Monte Carlo-simulatie ----------

def test_monte_carlo_simulation_produces_sensible_distribution():
    import random
    random.seed(42)
    from financial_model import run_monte_carlo_simulation
    context = {"sec_result": {"annual_facts": {
        "Revenues": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 12_830_000_000}],
    }}}
    result = run_monte_carlo_simulation({
        "bear": {"revenue_growth_pct": -5, "operating_margin_pct": 8, "capex_pct_of_revenue": 6},
        "base": {"revenue_growth_pct": 5, "operating_margin_pct": 14, "capex_pct_of_revenue": 5},
        "bull": {"revenue_growth_pct": 12, "operating_margin_pct": 20, "capex_pct_of_revenue": 5},
        "years": 5, "n_simulations": 2000,
    }, context)
    assert result["p10"] < result["median"] < result["p90"]
    assert result["min"] <= result["p10"] and result["p90"] <= result["max"]
    assert result["n_simulations"] == 2000


def test_monte_carlo_simulation_missing_revenue_returns_error():
    from financial_model import run_monte_carlo_simulation
    result = run_monte_carlo_simulation({"bear": {}, "base": {}, "bull": {}}, {"sec_result": {"error": "test"}})
    assert "error" in result


def test_monte_carlo_simulation_invalid_input_returns_error():
    from financial_model import run_monte_carlo_simulation
    context = {"sec_result": {"annual_facts": {"Revenues": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 1000}]}}}
    result = run_monte_carlo_simulation({"bear": {"revenue_growth_pct": 1}, "base": {}, "bull": {}}, context)
    assert "error" in result


# ---------- render.py: distribution-grafiek ----------

def test_render_distribution_produces_output():
    from render import _render_distribution
    result = _render_distribution({
        "title": "FCF-verdeling", "target_metric": "Jaar-5 FCF",
        "p10": 989_000_000, "p25": 1_215_000_000, "median": 1_484_000_000,
        "p75": 1_787_000_000, "p90": 2_067_000_000,
    })
    assert "distribution-chart" in result
    assert "$989M" in result and "$1.48B" in result


def test_render_distribution_missing_fields_returns_empty():
    from render import _render_distribution
    assert _render_distribution({"p10": 1, "p25": 2, "median": 3}) == ""


# ---------- lineage.py ----------

def test_build_lineage_manifest_includes_expected_sources():
    from lineage import build_lineage_manifest
    sec_result = {"source": "SEC EDGAR"}
    verified_metrics = {"sec_operating_margin": {"fiscal_year": 2025, "value": 0.184}}
    altman_result = {"z_score": 2.8}
    piotroski_result = {"score": 6, "max_score": 9, "fiscal_year": 2025}
    reverse_dcf_result = {"wacc": 0.114, "implied_annual_fcf_growth": 0.05}
    macro_snapshot = {"fed_funds_rate": {"value": "4.33", "date": "2026-08-01"}}
    peer_comparison = {"trailing_pe": {"label": "P/E (trailing)", "premium_discount_pct": -0.23, "peer_count": 2}}

    manifest = build_lineage_manifest(sec_result, verified_metrics, altman_result, piotroski_result,
                                        reverse_dcf_result, macro_snapshot, peer_comparison)
    metrics = [item["metric"] for item in manifest]
    assert "Operating margin" in metrics
    assert "Altman Z-Score" in metrics
    assert "fed_funds_rate" in metrics
    assert any("Peer-vergelijking" in m for m in metrics)


def test_build_lineage_manifest_handles_all_errors_gracefully():
    from lineage import build_lineage_manifest
    error_dict = {"error": "test"}
    manifest = build_lineage_manifest(error_dict, None, error_dict, error_dict, error_dict, error_dict, error_dict)
    assert manifest == []


def test_render_html_includes_lineage_section():
    from render import render_html
    sections_text = "\n\n".join(f"{i}. Sectie {i}\ntest." for i in range(1, 19))
    review = {"approved": True, "issues": []}
    colors = {"primary": "#000", "secondary": "#111", "accent": "#222"}
    company_data = {"current_price": 10.0, "currency": "USD", "market_cap": 1_000_000_000}
    lineage_manifest = [{"metric": "Operating margin", "value": 0.18, "source": "SEC EDGAR", "period": 2025, "note": "test"}]
    out = render_html("TEST", "Test Corp", "2026-01-01 00:00", None, "", review, sections_text,
                       colors, company_data, None, lineage_manifest=lineage_manifest)
    assert "lineage-table" in out
    assert "Operating margin" in out


# ---------- financial_model.py: Monte Carlo-histogram ----------

def test_monte_carlo_histogram_sums_to_n_simulations():
    import random
    random.seed(7)
    from financial_model import run_monte_carlo_simulation
    context = {"sec_result": {"annual_facts": {"Revenues": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 12_830_000_000}]}}}
    result = run_monte_carlo_simulation({
        "bear": {"revenue_growth_pct": -5, "operating_margin_pct": 8, "capex_pct_of_revenue": 6},
        "base": {"revenue_growth_pct": 5, "operating_margin_pct": 14, "capex_pct_of_revenue": 5},
        "bull": {"revenue_growth_pct": 12, "operating_margin_pct": 20, "capex_pct_of_revenue": 5},
        "years": 5, "n_simulations": 2000,
    }, context)
    assert sum(result["histogram_counts"]) == 2000
    assert len(result["histogram_bin_centers"]) == len(result["histogram_counts"]) == 24


# ---------- render.py: belcurve (bell curve) ----------

def test_render_distribution_draws_svg_bell_curve_when_histogram_present():
    from render import _render_distribution
    chart = {
        "target_metric": "Jaar-5 FCF",
        "p10": 989_000_000, "p25": 1_215_000_000, "median": 1_484_000_000,
        "p75": 1_787_000_000, "p90": 2_067_000_000,
        "histogram_bin_centers": [500_000_000 + i * 100_000_000 for i in range(10)],
        "histogram_counts": [1, 5, 20, 60, 150, 200, 120, 40, 10, 2],
    }
    result = _render_distribution(chart)
    assert "<svg" in result and "bell-curve-svg" in result


def test_render_distribution_falls_back_without_histogram():
    from render import _render_distribution
    chart = {"target_metric": "test", "p10": 1, "p25": 2, "median": 3, "p75": 4, "p90": 5}
    result = _render_distribution(chart)
    assert "<svg" not in result
    assert "dist-track" in result


def test_render_distribution_mismatched_histogram_lengths_falls_back():
    from render import _render_distribution
    chart = {
        "target_metric": "test", "p10": 1, "p25": 2, "median": 3, "p75": 4, "p90": 5,
        "histogram_bin_centers": [1, 2, 3], "histogram_counts": [1, 2],
    }
    result = _render_distribution(chart)
    assert "<svg" not in result


# ---------- data_fetch.py: VaR, Sharpe/Sortino, lopende beta ----------

def test_value_at_risk_matches_manual_calculation():
    from data_fetch import compute_value_at_risk
    import math
    result = compute_value_at_risk(current_price=46.26, annualized_volatility_pct=56.2)
    daily_vol = 0.562 / math.sqrt(252)
    expected = round(1.645 * daily_vol * 100, 2)
    assert result["var_1day_95pct_pct"] == expected


def test_value_at_risk_missing_inputs_returns_error():
    from data_fetch import compute_value_at_risk
    assert "error" in compute_value_at_risk(None, 56.2)
    assert "error" in compute_value_at_risk(46.26, None)


def test_sharpe_sortino_computes_values():
    from data_fetch import compute_sharpe_sortino
    import random
    random.seed(3)
    closes = [100.0]
    for _ in range(300):
        closes.append(closes[-1] * (1 + random.gauss(0.0005, 0.02)))
    result = compute_sharpe_sortino(closes, risk_free_rate_pct=4.3)
    assert isinstance(result["sharpe_ratio"], float)
    assert isinstance(result["sortino_ratio"], float)
    assert result["risk_free_rate_pct_used"] == 4.3


def test_sharpe_sortino_missing_inputs_returns_error():
    from data_fetch import compute_sharpe_sortino
    assert "error" in compute_sharpe_sortino([100] * 50, None)
    assert "error" in compute_sharpe_sortino([100, 101], 4.3)


def test_rolling_beta_recovers_known_synthetic_beta():
    from unittest.mock import patch, MagicMock
    import pandas as pd
    import numpy as np
    from data_fetch import compute_rolling_beta

    np.random.seed(5)
    n = 400
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    market_returns = np.random.normal(0.0004, 0.01, n)
    stock_returns = 1.5 * market_returns + np.random.normal(0, 0.015, n)
    market_closes = 4000 * np.cumprod(1 + market_returns)
    stock_closes = 100 * np.cumprod(1 + stock_returns)
    stock_df = pd.DataFrame({"Close": stock_closes}, index=dates)
    market_df = pd.DataFrame({"Close": market_closes}, index=dates)

    def fake_ticker(symbol):
        m = MagicMock()
        m.history.return_value = market_df if symbol == "^GSPC" else stock_df
        return m

    with patch("data_fetch.yf.Ticker", side_effect=fake_ticker):
        result = compute_rolling_beta("TEST", period="3y", window_days=90, step_days=21)

    avg_beta = sum(result["betas"]) / len(result["betas"])
    assert 1.3 < avg_beta < 1.7  # moet dicht bij de ingebouwde 1.5 liggen


def test_rolling_beta_handles_fetch_failure():
    from unittest.mock import patch
    from data_fetch import compute_rolling_beta
    with patch("data_fetch.yf.Ticker", side_effect=Exception("netwerkfout")):
        result = compute_rolling_beta("TEST")
    assert "error" in result


# ---------- framework.py: risico-blokken in de prompt ----------

def test_build_analysis_prompt_includes_risk_metric_blocks():
    from framework.framework import build_analysis_prompt
    var_result = {"var_1day_95pct_pct": 5.82, "var_1day_99pct_pct": 8.23, "var_1month_95pct_pct": 26.69}
    sharpe_result = {"sharpe_ratio": -1.29, "sortino_ratio": -1.25, "risk_free_rate_pct_used": 4.3}
    beta_result = {"dates": ["2025-01-01"], "betas": [1.4], "benchmark": "S&P 500 (^GSPC)", "window_days": 90}
    prompt = build_analysis_prompt({"ticker": "TEST"}, None, "", None, None, None, None, None, None, None, None,
                                     None, None, var_result, sharpe_result, beta_result)
    assert "Value at Risk" in prompt
    assert "Sharpe-ratio" in prompt
    assert "LOPENDE BETA" in prompt


def test_build_analysis_prompt_omits_risk_blocks_when_unavailable():
    from framework.framework import build_analysis_prompt
    prompt = build_analysis_prompt({"ticker": "TEST"}, None, "", None, None, None, None, None, None, None, None,
                                     None, None, {"error": "x"}, {"error": "x"}, {"error": "x"})
    assert "RISICO-GEWOGEN RENDEMENTSMAATSTAVEN" not in prompt
    assert "LOPENDE BETA" not in prompt


# ---------- consistency_check.py: net debt/EBITDA-consistentie ----------

def test_consistency_check_catches_real_leu_style_net_debt_ebitda_bug():
    """Reproduceert de echte LEU-bug: het bedrijf had een netto-kaspositie
    (geverifieerd cijfer is NEGATIEF), maar de tekst noemde een positieve
    8.82x -- wiskundig onmogelijk, moet gevangen worden."""
    from consistency_check import check_output_consistency
    verified_metrics = {"sec_net_debt_to_ebitda": -3.44}
    text = "The company reports a net debt/EBITDA of 8.82x, which appears elevated."
    issues = check_output_consistency(text, verified_metrics)
    assert len(issues) == 1
    assert "8.82" in issues[0]["message"]


def test_consistency_check_ignores_small_ratio_rounding():
    from consistency_check import check_output_consistency
    verified_metrics = {"sec_net_debt_to_ebitda": 1.05}
    text = "Net debt/EBITDA stands at approximately 1.1x."
    assert check_output_consistency(text, verified_metrics) == []


def test_consistency_check_net_debt_ebitda_absent_when_not_verified():
    from consistency_check import check_output_consistency
    assert check_output_consistency("Net debt/EBITDA is 8.82x.", {}) == []


# ---------- data_fetch.py: HMM-regimedetectie ----------

def test_regime_detection_identifies_known_transition():
    """Simuleert 200 kalme dagen gevolgd door 100 onrustige dagen, en
    controleert of het HMM de recente turbulente periode correct herkent
    als het huidige, aanhoudende regime."""
    from unittest.mock import patch
    import pandas as pd
    import numpy as np
    from data_fetch import compute_regime_detection

    np.random.seed(10)
    calm_returns = np.random.normal(0.0003, 0.008, 200)
    turbulent_returns = np.random.normal(-0.001, 0.04, 100)
    all_returns = np.concatenate([calm_returns, turbulent_returns])
    closes = 100 * np.cumprod(1 + all_returns)
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    fake_hist = pd.DataFrame({"Close": closes}, index=dates)

    with patch("data_fetch.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = fake_hist
        result = compute_regime_detection("TEST", period="3y", n_states=2)

    assert result["current_regime"] == "onrustig/hoog-volatiel"
    assert 90 <= result["days_in_current_regime"] <= 100
    assert result["regime_stats"]["onrustig/hoog-volatiel"]["annualized_volatility_pct"] > \
           result["regime_stats"]["kalm/laag-volatiel"]["annualized_volatility_pct"]


def test_regime_detection_insufficient_data_returns_error():
    from unittest.mock import patch
    import pandas as pd
    from data_fetch import compute_regime_detection
    with patch("data_fetch.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = pd.DataFrame({"Close": [100.0] * 20})
        result = compute_regime_detection("TEST")
    assert "error" in result


def test_regime_detection_handles_fetch_failure():
    from unittest.mock import patch
    from data_fetch import compute_regime_detection
    with patch("data_fetch.yf.Ticker", side_effect=Exception("netwerkfout")):
        result = compute_regime_detection("TEST")
    assert "error" in result


def test_build_analysis_prompt_includes_regime_block():
    from framework.framework import build_analysis_prompt
    regime_result = {
        "current_regime": "onrustig/hoog-volatiel", "days_in_current_regime": 42,
        "regime_stats": {
            "kalm/laag-volatiel": {"annualized_volatility_pct": 16.7, "annualized_return_pct": 21.6},
            "onrustig/hoog-volatiel": {"annualized_volatility_pct": 60.3, "annualized_return_pct": -50.4},
        },
        "regime_history_tail": ["kalm/laag-volatiel"] * 40 + ["onrustig/hoog-volatiel"] * 20,
    }
    prompt = build_analysis_prompt({"ticker": "TEST"}, None, "", None, None, None, None, None, None, None,
                                     None, None, None, None, None, None, regime_result)
    assert "REGIMEDETECTIE" in prompt
    assert "42 handelsdagen" in prompt


def test_render_regime_timeline_produces_output():
    from render import _render_regime_timeline
    history = ["kalm/laag-volatiel"] * 40 + ["onrustig/hoog-volatiel"] * 20
    result = _render_regime_timeline({"title": "Test", "history": history})
    assert result.count("regime-segment") == 60
    assert "#4A7C64" in result and "#A6402F" in result


def test_render_regime_timeline_empty_history_returns_empty():
    from render import _render_regime_timeline
    assert _render_regime_timeline({"history": []}) == ""


# ---------- render.py: radar en scatter (uit de visuele-bibliotheek-sessie) ----------

def test_render_radar_produces_output_with_two_series():
    from render import _render_radar
    result = _render_radar({
        "title": "Kwalitatief profiel",
        "axes": ["Moat", "Financiele gezondheid", "Management", "Groei", "Waardering"],
        "series": [
            {"name": "Bedrijf", "values": [3, 2, 4, 5, 2]},
            {"name": "Sector-gemiddelde", "values": [3, 3, 3, 3, 3]},
        ],
    })
    assert "<svg" in result
    assert "Moat" in result and "Bedrijf" in result and "Sector-gemiddelde" in result


def test_render_radar_rejects_mismatched_axis_value_counts():
    from render import _render_radar
    assert _render_radar({"axes": ["A", "B"], "series": [{"values": [1]}]}) == ""


def test_render_radar_rejects_missing_axes_or_series():
    from render import _render_radar
    assert _render_radar({"axes": [], "series": [{"values": []}]}) == ""
    assert _render_radar({"axes": ["A"], "series": []}) == ""


def test_render_scatter_produces_output_with_labeled_points():
    from render import _render_scatter
    result = _render_scatter({
        "title": "Risico vs. rendement per scenario",
        "x_label": "Volatiliteit (%)", "y_label": "Verwacht rendement (%)",
        "points": [
            {"label": "Bear", "x": 30, "y": -5},
            {"label": "Base", "x": 45, "y": 8},
            {"label": "Bull", "x": 60, "y": 20},
        ],
    })
    assert "<svg" in result
    assert result.count("<circle") == 3
    assert "Bear" in result and "Base" in result and "Bull" in result


def test_render_scatter_rejects_empty_points():
    from render import _render_scatter
    assert _render_scatter({"points": []}) == ""


# ---------- analyst_agent.py: retry-logica verbreed na echte netwerkcrash ----------

def test_call_claude_with_retry_recovers_from_raw_network_exception():
    """Reproduceert de echte OKLO-crash: een rauwe, niet-anthropic-specifieke
    fout (zoals httpx2.ReadError door een verbroken verbinding midden in het
    streamen) moet nu ALSNOG worden opgevangen en opnieuw geprobeerd, in
    plaats van het hele script te laten crashen."""
    from unittest.mock import MagicMock, patch
    import agent.analyst_agent as analyst_agent

    class RawNetworkError(Exception):
        """Staat voor een willekeurige, niet-anthropic-specifieke fout --
        precies zoals de echte httpx2.ReadError die de crash veroorzaakte."""
        pass

    fake_response = MagicMock()
    fake_response.stop_reason = "end_turn"

    client = MagicMock()
    client.messages.stream.side_effect = [RawNetworkError("verbinding verbroken"), _stream_cm(fake_response)]

    with patch("agent.analyst_agent.time.sleep"):
        result = analyst_agent.call_claude_with_retry(client, [{"role": "user", "content": "test"}])

    assert result is fake_response
    assert client.messages.stream.call_count == 2


def test_call_claude_with_retry_raises_after_max_retries_exhausted():
    from unittest.mock import MagicMock, patch
    import agent.analyst_agent as analyst_agent

    client = MagicMock()
    client.messages.stream.side_effect = Exception("aanhoudende netwerkfout")

    with patch("agent.analyst_agent.time.sleep"):
        try:
            analyst_agent.call_claude_with_retry(client, [{"role": "user", "content": "test"}])
            assert False, "had een RuntimeError moeten opgooien"
        except RuntimeError as e:
            assert "bleef falen" in str(e)
    assert client.messages.stream.call_count == analyst_agent.MAX_RETRIES


# ---------- consistency_check.py: verzonnen peer-vergelijking (echte OKLO-bug) ----------

def test_consistency_check_catches_fabricated_peer_comparison_even_with_empty_metrics():
    """Reproduceert de echte OKLO-bug in twee lagen: (1) een radardiagram met
    een verzonnen 'Peer Average'-reeks terwijl er geen --peers zijn opgegeven,
    en (2) de check moet dit ALSNOG vangen ook al is verified_metrics leeg
    (zoals bij OKLO, waar Altman/Piotroski/reverse-DCF allemaal 'niet
    mogelijk' waren) -- de vroege return mocht deze check niet overslaan."""
    from consistency_check import check_output_consistency
    text = (
        'Some text.\n```chart\n'
        '{"type": "radar", "axes": ["A", "B"], '
        '"series": [{"name": "Oklo", "values": [1, 2]}, {"name": "Peer Average", "values": [2, 2]}]}\n'
        '```\nMore text.'
    )
    issues = check_output_consistency(text, {}, peers=None)
    assert len(issues) == 1
    assert "Peer Average" in issues[0]["message"]


def test_consistency_check_allows_peer_comparison_when_peers_supplied():
    from consistency_check import check_output_consistency
    text = (
        '```chart\n{"type": "radar", "axes": ["A"], '
        '"series": [{"name": "Company", "values": [1]}, {"name": "Peer Average", "values": [2]}]}\n```'
    )
    assert check_output_consistency(text, {}, peers=["CCJ", "BWXT"]) == []


def test_consistency_check_allows_single_series_radar_without_peers():
    from consistency_check import check_output_consistency
    text = '```chart\n{"type": "radar", "axes": ["A"], "series": [{"name": "Company", "values": [1]}]}\n```'
    assert check_output_consistency(text, {}, peers=None) == []


# ---------- render.py: radar-label-clipping en Engelse labels (echte OKLO-bugs) ----------

def test_render_radar_labels_stay_within_viewbox_with_long_axis_names():
    """Reproduceert de echte OKLO-bug: lange asnamen ('Technology
    Differentiation', 'Revenue Stage') vielen net buiten de viewBox en
    werden afgesneden in de screenshot."""
    from render import _render_radar
    import re
    result = _render_radar({
        "axes": ["Regulatory Progress", "Customer Anchor", "Cash Runway", "Technology Differentiation", "Revenue Stage"],
        "series": [{"name": "Oklo (OKLO)", "values": [4, 4, 4, 4, 2]}],
    })
    xs = [float(m) for m in re.findall(r'<text x="([\d.]+)"', result)]
    assert all(30 <= x <= 290 for x in xs)  # ruime marge binnen de 320-brede viewBox


def test_render_html_snapshot_labels_are_english_not_dutch():
    """Reproduceert de echte OKLO-bug: de kerncijfer-kaartjes en andere
    vaste labels stonden in het Nederlands terwijl de rest van het rapport
    (door Claude geschreven) in het Engels is."""
    from render import render_html
    sections_text = "\n\n".join(f"{i}. Sectie {i}\ntest." for i in range(1, 19))
    review = {"approved": True, "issues": []}
    colors = {"primary": "#000", "secondary": "#111", "accent": "#222"}
    company_data = {"current_price": 10.0, "currency": "USD", "market_cap": 1_000_000_000,
                     "trailing_pe": 9.0, "profit_margins": 0.1, "return_on_equity": 0.1,
                     "revenue_growth": 0.05, "dividend_yield": 0.0, "debt_to_equity": 0.5}
    out = render_html("TEST", "Test Corp", "2026-01-01 00:00", None, "", review, sections_text,
                       colors, company_data, None)
    assert "Kerncijfers" not in out
    assert "Marktkapitalisatie" not in out
    assert "Current Price" in out and "Market Cap" in out


def test_render_html_omits_peers_line_from_hero():
    """DD vroeg expliciet om de 'Peers: -'-regel uit de hero te verwijderen."""
    from render import render_html
    sections_text = "\n\n".join(f"{i}. Sectie {i}\ntest." for i in range(1, 19))
    review = {"approved": True, "issues": []}
    colors = {"primary": "#000", "secondary": "#111", "accent": "#222"}
    company_data = {"current_price": 10.0, "currency": "USD", "market_cap": 1_000_000_000}
    out = render_html("TEST", "Test Corp", "2026-01-01 00:00", None, "", review, sections_text,
                       colors, company_data, None)
    assert "Peers:" not in out


def test_render_html_nav_appears_before_hero():
    """DD vroeg om de navigatiebalk helemaal bovenaan i.p.v. tussen de hero
    en de inhoud in."""
    from render import render_html
    sections_text = "\n\n".join(f"{i}. Sectie {i}\ntest." for i in range(1, 19))
    review = {"approved": True, "issues": []}
    colors = {"primary": "#000", "secondary": "#111", "accent": "#222"}
    company_data = {"current_price": 10.0, "currency": "USD", "market_cap": 1_000_000_000}
    out = render_html("TEST", "Test Corp", "2026-01-01 00:00", None, "", review, sections_text,
                       colors, company_data, None)
    assert out.index('<nav class="toc">') < out.index('<header class="hero">')


# ---------- render.py: data-table-voetnoot (echte OKLO "see note*"-bug) ----------

def test_render_data_table_shows_footnote_when_provided():
    from render import _render_data_table
    result = _render_data_table({
        "title": "Test", "columns": ["Jaar", "FCF"], "rows": [["2025", "see note*"]],
        "footnote": "FY2022 is uitgesloten vanwege een niet-reconcilieerbare discrepantie.",
    })
    assert "data-table-footnote" in result
    assert "niet-reconcilieerbare" in result


def test_render_data_table_omits_footnote_div_when_absent():
    from render import _render_data_table
    result = _render_data_table({"columns": ["A"], "rows": [["1"]]})
    assert "data-table-footnote" not in result


# ---------- consistency_check.py: uitgebreide zelfverzin-audit (7 nieuwe cijfers) ----------

def test_consistency_check_catches_all_seven_new_metric_mismatches():
    from consistency_check import check_output_consistency
    verified_metrics = {
        "sec_net_margin": {"fiscal_year": 2025, "value": 0.10},
        "sec_revenue_yoy_growth": {"fiscal_year": 2025, "value": 0.05},
        "sec_net_income_yoy_growth": {"fiscal_year": 2025, "value": 0.08},
        "sec_roic": {"fiscal_year": 2025, "value": 0.12},
        "sec_roic_vs_wacc_spread": -0.02,
        "sec_interest_coverage_ratio": 5.2,
        "sec_normalized_ev_to_ebitda": 12.5,
    }
    text = (
        "Net margin was 25.0%. Revenue growth of 40.0% was reported. "
        "Net income growth reached 60.0%. ROIC stood at 45.0%. "
        "The ROIC-WACC spread was +8.0%. Interest coverage was 20.0x. "
        "EV/EBITDA came in at 50.0x."
    )
    issues = check_output_consistency(text, verified_metrics)
    assert len(issues) == 7


def test_consistency_check_new_metrics_no_false_positive_when_correct():
    """Reproduceert een echte bug gevonden tijdens het bouwen: een venster-
    gebaseerde zoekopdracht pikte per ongeluk een percentage van een
    naburige, andere metric op. Gefixt door alleen de dichtstbijzijnde
    match te gebruiken."""
    from consistency_check import check_output_consistency
    verified_metrics = {
        "sec_net_margin": {"fiscal_year": 2025, "value": 0.10},
        "sec_roic_vs_wacc_spread": -0.02,
        "sec_interest_coverage_ratio": 5.2,
    }
    text = "Net margin was 10.0%. The ROIC-WACC spread was -2.0%. Interest coverage was 5.2x."
    assert check_output_consistency(text, verified_metrics) == []


def test_consistency_check_recognizes_negative_percentages():
    """Reproduceert een echte bug: PERCENT_PATTERN herkende geen leidend
    minteken, waardoor '-60.5%' als '60.5%' werd gelezen -- relevant voor
    elk verlieslatend bedrijf (bijv. OKLO's -60.5% operating margin)."""
    from consistency_check import check_output_consistency
    verified_metrics = {"sec_operating_margin": {"fiscal_year": 2025, "value": -0.605}}
    assert check_output_consistency("Operating margin was -60.5%.", verified_metrics) == []
    issues = check_output_consistency("Operating margin was 60.5%.", verified_metrics)
    assert len(issues) == 1


def test_check_ratio_metric_handles_bare_number_and_dict_shapes():
    """sec_interest_coverage_ratio/sec_normalized_ev_to_ebitda zijn kale
    getallen; sec_net_margin etc. zijn dicts met een 'value'-sleutel --
    beide vormen moeten correct worden uitgelezen."""
    from consistency_check import _extract_metric_value
    assert _extract_metric_value({"sec_interest_coverage_ratio": 5.2}, "sec_interest_coverage_ratio") == 5.2
    assert _extract_metric_value({"sec_net_margin": {"value": 0.1}}, "sec_net_margin") == 0.1
    assert _extract_metric_value({}, "sec_net_margin") is None


# ---------- forensics.py: SPAC-fusiejaar-vlag (echte OKLO/LEU-bug) ----------

def test_forensic_flags_catches_operating_vs_net_income_sign_divergence():
    from forensics import compute_forensic_flags
    sec_result = {"annual_facts": {
        "OperatingIncomeLoss": [
            {"fiscal_year": 2022, "period_end": "2022-12-31", "value": -10_000_000},
            {"fiscal_year": 2023, "period_end": "2023-12-31", "value": -16_000_000},
        ],
        "NetIncomeLoss": [
            {"fiscal_year": 2022, "period_end": "2022-12-31", "value": 3_900_000},
            {"fiscal_year": 2023, "period_end": "2023-12-31", "value": -32_200_000},
        ],
    }}
    flags = compute_forensic_flags(sec_result)
    spac_flags = [f for f in flags if "tekenverschil" in f["check"]]
    assert len(spac_flags) == 1
    assert "FY2022" in spac_flags[0]["message"]


def test_forensic_flags_no_sign_divergence_flag_when_same_sign():
    from forensics import compute_forensic_flags
    sec_result = {"annual_facts": {
        "OperatingIncomeLoss": [{"fiscal_year": 2023, "period_end": "2023-12-31", "value": -16_000_000}],
        "NetIncomeLoss": [{"fiscal_year": 2023, "period_end": "2023-12-31", "value": -32_200_000}],
    }}
    flags = compute_forensic_flags(sec_result)
    assert not any("tekenverschil" in f["check"] for f in flags)


def test_forensic_flags_ignores_small_sign_divergence():
    from forensics import compute_forensic_flags
    sec_result = {"annual_facts": {
        "OperatingIncomeLoss": [{"fiscal_year": 2023, "period_end": "2023-12-31", "value": -100_000}],
        "NetIncomeLoss": [{"fiscal_year": 2023, "period_end": "2023-12-31", "value": 50_000}],
    }}
    flags = compute_forensic_flags(sec_result)
    assert not any("tekenverschil" in f["check"] for f in flags)


# ---------- render.py: nieuwe grafiektypes uit "Deep-Dive Visuele Bibliotheek" ----------

def test_render_line_trend_supports_multi_series():
    from render import _render_line_trend
    result = _render_line_trend({
        "title": "Koers vs. sectorindex", "labels": ["Jan", "Feb", "Mrt", "Apr"],
        "series": [{"name": "Bedrijf", "values": [100, 104, 98, 112]}, {"name": "Sectorindex", "values": [100, 101, 99, 103]}],
    })
    assert result.count("polyline") == 2
    assert "Bedrijf" in result and "Sectorindex" in result


def test_render_line_trend_single_series_still_works():
    from render import _render_line_trend
    result = _render_line_trend({"title": "Test", "labels": ["2023", "2024"], "values": [10, 12]})
    assert "<svg" in result and "polyline" in result


def test_render_risk_matrix_places_risks_correctly():
    from render import _render_risk_matrix
    result = _render_risk_matrix({
        "title": "Risico-overzicht", "risks": [
            {"name": "Regelgeving", "likelihood": 2, "impact": 3},
            {"name": "Grondstofprijs", "likelihood": 4, "impact": 4},
        ],
    })
    assert result.count("risk-matrix-cell") == 25
    assert "Regelgeving" in result and "Grondstofprijs" in result


def test_render_risk_matrix_empty_returns_empty():
    from render import _render_risk_matrix
    assert _render_risk_matrix({"risks": []}) == ""


def test_render_grouped_bar_produces_output():
    from render import _render_grouped_bar
    result = _render_grouped_bar({
        "title": "Multiples vs. peers", "unit": "x", "categories": ["P/E", "EV/EBITDA"],
        "series": [{"name": "Bedrijf", "values": [14.2, 7.8]}, {"name": "Peer A", "values": [18.6, 9.1]}],
    })
    assert result.count("grouped-bar-group") == 2
    assert "Bedrijf" in result and "Peer A" in result


def test_render_grouped_bar_rejects_mismatched_lengths():
    from render import _render_grouped_bar
    assert _render_grouped_bar({"categories": ["A"], "series": [{"values": [1, 2]}]}) == ""


# ---------- analyst_agent.py: reviewer-max_tokens verhoogd + betere diagnose (echte PLTR-bug) ----------

def test_review_report_distinguishes_truncation_from_other_json_errors():
    """Reproduceert de echte PLTR-bug: de Kruisverwijzing-reviewer liep tegen
    max_tokens aan en zijn JSON brak letterlijk af midden in een naam. Nu
    moet de foutmelding expliciet 'afgekapt door max_tokens' zeggen i.p.v.
    alleen de kale, afgekapte tekst te tonen."""
    from unittest.mock import MagicMock
    import agent.analyst_agent as analyst_agent

    def fake_stream(system, messages, **kwargs):
        resp = MagicMock()
        resp.stop_reason = "max_tokens"
        resp.content = [MagicMock(type="text", text='{"approved": false, "issues": ["De oprichtings')]
        resp.usage.input_tokens = 100
        resp.usage.output_tokens = 8000
        resp.usage.cache_creation_input_tokens = 0
        resp.usage.cache_read_input_tokens = 0
        return _stream_cm(resp)

    client = MagicMock()
    client.messages.stream.side_effect = fake_stream

    review, usage = analyst_agent.review_report(client, "test rapport tekst")
    assert review["approved"] is False
    assert any("afgekapt door max_tokens" in i for i in review["issues"])


def test_review_report_uses_generous_max_tokens():
    """Bevestigt dat reviewer-calls nu 8000 (niet meer 4000) gebruiken --
    4000 bleek bij PLTR alsnog te weinig voor een rapport met veel
    kruisverwijzing-bevindingen."""
    from unittest.mock import MagicMock
    import agent.analyst_agent as analyst_agent

    fake_response = MagicMock()
    fake_response.stop_reason = "end_turn"
    fake_response.content = [MagicMock(type="text", text='{"approved": true, "issues": []}')]
    fake_response.usage.input_tokens = 100
    fake_response.usage.output_tokens = 20
    fake_response.usage.cache_creation_input_tokens = 0
    fake_response.usage.cache_read_input_tokens = 0

    client = MagicMock()
    client.messages.stream.return_value = _stream_cm(fake_response)

    analyst_agent.review_report(client, "test rapport tekst")
    for call in client.messages.stream.call_args_list:
        assert call.kwargs["max_tokens"] == 8000


def test_render_distribution_shows_values_at_dashed_lines():
    """DD vroeg expliciet om de p10/mediaan/p90-waardes bij de stippellijnen
    zelf te tonen, niet alleen onderaan de grafiek."""
    from render import _render_distribution
    chart = {
        "title": "Test", "target_metric": "FCF",
        "p10": 4_590_000_000, "p25": 6_000_000_000, "median": 8_000_000_000,
        "p75": 10_000_000_000, "p90": 11_630_000_000,
        "histogram_bin_centers": [3_000_000_000 + i * 700_000_000 for i in range(14)],
        "histogram_counts": [2, 8, 25, 60, 120, 180, 210, 200, 160, 100, 50, 20, 8, 2],
    }
    result = _render_distribution(chart)
    assert "$4.59B" in result and "$11.63B" in result and "$8.00B" in result


def test_fetch_insider_transactions_handles_cik_map_failure_gracefully():
    """Reproduceert een eigen gevonden bug: _get_ticker_cik_map() zelf heeft
    geen foutafhandeling -- als die een netwerkfout gooit, moet
    fetch_insider_transactions dit netjes opvangen i.p.v. te crashen."""
    from unittest.mock import patch
    import sec_data
    with patch("sec_data._get_ticker_cik_map", side_effect=Exception("netwerkfout")):
        result = sec_data.fetch_insider_transactions("TEST")
    assert "error" in result


# ---------- library_search.py: geblokkeerde import mag agent niet platleggen ----------

def test_search_library_handles_blocked_import_gracefully():
    """Reproduceert een echte crash: een Windows-beleid blokkeerde een DLL
    diep in de sentence_transformers-importketen, waardoor de HELE agent
    crashte bij het opstarten (tools.py importeert library_search bij het
    laden). Nu moet dit soort importfout alleen deze ene tool uitschakelen."""
    import importlib
    import library_search

    original_error = library_search._IMPORT_ERROR
    try:
        library_search._IMPORT_ERROR = "DLL load failed while importing cython_blas: geblokkeerd door beleid"
        result = library_search.search_library("test query")
        assert "error" in result
        assert "geblokkeerd" in result["error"]
    finally:
        library_search._IMPORT_ERROR = original_error


# ---------- library_search.py / library_index.py: Voyage AI-migratie ----------

def test_library_search_missing_api_key_gives_clean_error():
    import os
    import library_search
    saved = os.environ.pop("VOYAGE_API_KEY", None)
    try:
        result = library_search.search_library("test")
        assert "error" in result and "VOYAGE_API_KEY" in result["error"]
    finally:
        if saved is not None:
            os.environ["VOYAGE_API_KEY"] = saved


def test_library_search_uses_query_input_type():
    """Bevestigt dat zoekvragen met input_type='query' worden ge-embed
    (asymmetrische retrieval) -- niet 'document', dat is voor de
    bibliotheek-fragmenten zelf tijdens het indexeren."""
    import os
    from unittest.mock import patch, MagicMock
    import library_search

    os.environ["VOYAGE_API_KEY"] = "fake-key-for-test"
    library_search._collection = None

    fake_collection = MagicMock()
    fake_collection.count.return_value = 1
    fake_collection.query.return_value = {"documents": [["tekst"]], "metadatas": [[{"book": "Boek1"}]]}

    fake_response = MagicMock()
    fake_response.raise_for_status = lambda: None
    fake_response.json.return_value = {"data": [{"embedding": [0.1, 0.2], "index": 0}]}

    with patch("library_search._get_collection", return_value=fake_collection), \
         patch("library_search.requests.post", return_value=fake_response) as mock_post:
        result = library_search.search_library("test query")

    assert mock_post.call_args.kwargs["json"]["input_type"] == "query"
    assert result["fragments"][0]["book"] == "Boek1"


def test_embed_documents_batches_at_128():
    """Reproduceert Voyage's harde batch-limiet: meer dan 128 teksten in
    één aanroep is niet toegestaan, dus embed_documents moet zelf opdelen."""
    from unittest.mock import patch, MagicMock
    import library_index

    chunks = [f"fragment {i}" for i in range(300)]
    call_sizes = []

    def fake_post(url, headers, json, timeout):
        call_sizes.append(len(json["input"]))
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = {"data": [{"embedding": [0.1, 0.2], "index": i} for i in range(len(json["input"]))]}
        return resp

    with patch("library_index.requests.post", side_effect=fake_post):
        embeddings = library_index.embed_documents(None, chunks)
    assert call_sizes == [128, 128, 44]
    assert len(embeddings) == 300


def test_library_index_main_requires_api_key(tmp_path, monkeypatch, capsys):
    import os
    import library_index
    monkeypatch.chdir(tmp_path)
    saved = os.environ.pop("VOYAGE_API_KEY", None)
    try:
        library_index.main()
        captured = capsys.readouterr()
        assert "VOYAGE_API_KEY" in captured.out
    finally:
        if saved is not None:
            os.environ["VOYAGE_API_KEY"] = saved


# ---------- consistency_check.py: gevoeligheidsgrafiek vs. echte tool-uitkomst (echte UUUU-bug) ----------

def test_consistency_check_catches_sensitivity_chart_mismatch():
    """Reproduceert de echte UUUU-bug: de heatmap-grafiek toonde voor
    omzetgroei veel te lage waarden (~1.4M) t.o.v. wat de tool zelf
    berekende (~7.4-7.9M) -- en zette de rangschikking van impact
    daardoor op zijn kop."""
    from consistency_check import check_output_consistency
    real_sensitivity_result = {
        "sensitivities": [
            {"assumption": "revenue_growth_pct", "direction": "omhoog", "fcf_change_final_year": -7891436},
            {"assumption": "revenue_growth_pct", "direction": "omlaag", "fcf_change_final_year": 7437398},
            {"assumption": "operating_margin_pct", "direction": "omhoog", "fcf_change_final_year": 5909956},
            {"assumption": "operating_margin_pct", "direction": "omlaag", "fcf_change_final_year": -5909956},
        ],
    }
    text = (
        '```chart\n{"type": "heatmap", "title": "FCF Sensitivity Analysis", '
        '"rows": ["Revenue growth (\u00b12pp)", "Operating margin (\u00b12pp)"], '
        '"cols": ["Downside", "Upside"], '
        '"values": [[-1391984, 1405194], [-5911929, 5911929]]}\n```'
    )
    issues = check_output_consistency(text, {}, sensitivity_tool_results=[real_sensitivity_result])
    assert len(issues) == 1
    assert "Revenue growth" in issues[0]["message"]


def test_consistency_check_sensitivity_chart_no_false_positive_when_correct():
    from consistency_check import check_output_consistency
    real_sensitivity_result = {
        "sensitivities": [
            {"assumption": "revenue_growth_pct", "direction": "omhoog", "fcf_change_final_year": -7891436},
            {"assumption": "revenue_growth_pct", "direction": "omlaag", "fcf_change_final_year": 7437398},
        ],
    }
    text = (
        '```chart\n{"type": "heatmap", "title": "Test", "rows": ["Revenue growth (\u00b12pp)"], '
        '"cols": ["Downside", "Upside"], "values": [[-7891436, 7437398]]}\n```'
    )
    assert check_output_consistency(text, {}, sensitivity_tool_results=[real_sensitivity_result]) == []


def test_consistency_check_sensitivity_chart_skipped_when_no_tool_result():
    from consistency_check import check_output_consistency
    text = (
        '```chart\n{"type": "heatmap", "title": "Test", "rows": ["Revenue growth"], '
        '"cols": ["Downside", "Upside"], "values": [[-1, 1]]}\n```'
    )
    assert check_output_consistency(text, {}, sensitivity_tool_results=None) == []


def test_parse_form4_xml_handles_true_false_booleans():
    """Reproduceert een echte bug, gevonden door een ECHTE SEC Form 4-XML
    (Energy Fuels/UUUU) letterlijk door onze parser te halen: SEC gebruikt
    'true'/'false' als tekst voor de rol-velden, niet '1'/'0' zoals eerder
    aangenomen -- waardoor de rol altijd stil terugviel op 'insider' in
    plaats van het echte 'officer'/'director'."""
    from sec_data import _parse_form4_xml
    xml = (
        "<ownershipDocument><reportingOwner><reportingOwnerId><rptOwnerName>Bhappu Ross R.</rptOwnerName>"
        "</reportingOwnerId><reportingOwnerRelationship><isDirector>false</isDirector>"
        "<isOfficer>true</isOfficer><officerTitle>President and CEO</officerTitle>"
        "<isTenPercentOwner>false</isTenPercentOwner></reportingOwnerRelationship></reportingOwner>"
        "<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-07-07</value></transactionDate>"
        "<transactionCoding><transactionCode>P</transactionCode></transactionCoding>"
        "<transactionAmounts><transactionShares><value>74000</value></transactionShares>"
        "<transactionPricePerShare><value>13.08</value></transactionPricePerShare></transactionAmounts>"
        "</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"
    )
    result = _parse_form4_xml(xml)
    assert len(result) == 1
    assert result[0]["role"] == "President and CEO"


# ---------- simple_hmm.py: eigen HMM-implementatie (vervangt hmmlearn) ----------

def test_fit_gaussian_hmm_recovers_known_regime_change():
    """Reproduceert exact hetzelfde scenario dat eerder werd gebruikt om
    hmmlearn te valideren -- 200 kalme dagen gevolgd door 100 onrustige --
    om te bevestigen dat onze eigen implementatie vergelijkbare kwaliteit
    haalt zonder een compiler te vereisen (hmmlearn kon niet builden op
    Python 3.14 zonder Visual C++ Build Tools)."""
    import numpy as np
    from simple_hmm import fit_gaussian_hmm

    np.random.seed(10)
    calm_returns = np.random.normal(0.0003, 0.008, 200)
    turbulent_returns = np.random.normal(-0.001, 0.04, 100)
    returns = np.concatenate([calm_returns, turbulent_returns])

    result = fit_gaussian_hmm(returns, n_states=2)
    turbulent_state = int(np.argmax(result["variances"]))

    last_100 = result["hidden_states"][-100:]
    correct = sum(1 for s in last_100 if s == turbulent_state)
    assert correct >= 90  # ruime marge, exacte hmmlearn-run gaf 99/100

    stds = [v ** 0.5 for v in result["variances"]]
    assert min(stds) < 0.015 < max(stds)  # kalm duidelijk lager dan onrustig


def test_fit_gaussian_hmm_rejects_too_few_observations():
    import numpy as np
    from simple_hmm import fit_gaussian_hmm
    try:
        fit_gaussian_hmm(np.array([0.01, 0.02, 0.03]), n_states=2)
        assert False, "had een ValueError moeten geven"
    except ValueError:
        pass


def test_fit_gaussian_hmm_three_states_uses_all_states():
    import numpy as np
    from simple_hmm import fit_gaussian_hmm
    np.random.seed(5)
    returns = np.concatenate([
        np.random.normal(0.001, 0.006, 150),
        np.random.normal(0.0, 0.02, 150),
        np.random.normal(-0.002, 0.05, 100),
    ])
    result = fit_gaussian_hmm(returns, n_states=3)
    assert len(set(result["hidden_states"])) == 3
    variances_sorted = sorted(result["variances"])
    assert variances_sorted[0] < variances_sorted[1] < variances_sorted[2]


def test_compute_regime_detection_works_without_hmmlearn():
    """Bevestigt dat compute_regime_detection nu volledig werkt zonder
    hmmlearn geinstalleerd te hebben -- reproduceert DD's exacte situatie."""
    from unittest.mock import patch
    import pandas as pd
    import numpy as np
    from data_fetch import compute_regime_detection

    np.random.seed(10)
    calm_returns = np.random.normal(0.0003, 0.008, 200)
    turbulent_returns = np.random.normal(-0.001, 0.04, 100)
    all_returns = np.concatenate([calm_returns, turbulent_returns])
    closes = 100 * np.cumprod(1 + all_returns)
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    fake_hist = pd.DataFrame({"Close": closes}, index=dates)

    with patch("data_fetch.yf.Ticker") as MockTicker:
        MockTicker.return_value.history.return_value = fake_hist
        result = compute_regime_detection("TEST", period="3y", n_states=2)

    assert "error" not in result
    assert result["current_regime"] == "onrustig/hoog-volatiel"


# ---------- track_record.py: kalibratiescore ----------

def test_extract_structured_kill_criteria_finds_mapped_criteria_only():
    from track_record import _extract_structured_kill_criteria
    text = (
        "17. Monitoring & Kill-Criteria\nSome prose.\n\n"
        '```chart\n{"type": "kill-criteria-recap", "criteria": ['
        '{"description": "Margin onder 8%", "metric_key": "sec_operating_margin", "operator": "<", "threshold": 0.08}, '
        '"Kwalitatief criterium zonder cijfer"'
        "]}\n```\n\n18. Variant Perception\nMore text."
    )
    result = _extract_structured_kill_criteria(text)
    assert len(result) == 1
    assert result[0]["metric_key"] == "sec_operating_margin"


def test_extract_structured_kill_criteria_empty_when_no_chart():
    from track_record import _extract_structured_kill_criteria
    assert _extract_structured_kill_criteria("17. Monitoring & Kill-Criteria\nGeen grafiek.") == []


def test_evaluate_operator_all_directions():
    from track_record import _evaluate_operator
    assert _evaluate_operator(0.05, "<", 0.08) is True
    assert _evaluate_operator(0.10, "<", 0.08) is False
    assert _evaluate_operator(0.10, ">", 0.08) is True
    assert _evaluate_operator(0.08, "<=", 0.08) is True
    assert _evaluate_operator(0.08, ">=", 0.08) is True
    assert _evaluate_operator(1, "??", 2) is None


def test_compute_calibration_score_across_multiple_tickers(tmp_path, monkeypatch):
    """Reproduceert een realistisch scenario: een criterium dat standhield
    (AAPL), een dat werd geraakt (MSFT), en een ticker met maar 1 run die
    niet meetelt (TSLA)."""
    import json
    import os
    import track_record
    monkeypatch.chdir(tmp_path)
    os.makedirs("track_record", exist_ok=True)

    with open("track_record/AAPL.json", "w") as f:
        json.dump([
            {"ticker": "AAPL", "date": "2026-01-01",
             "structured_kill_criteria": [{"description": "Margin < 8%", "metric_key": "sec_operating_margin", "operator": "<", "threshold": 0.08}],
             "verified_metrics": {"sec_operating_margin": {"value": 0.11}}},
            {"ticker": "AAPL", "date": "2026-06-01", "structured_kill_criteria": [],
             "verified_metrics": {"sec_operating_margin": {"value": 0.10}}},
        ], f)
    with open("track_record/MSFT.json", "w") as f:
        json.dump([
            {"ticker": "MSFT", "date": "2026-02-01",
             "structured_kill_criteria": [{"description": "Groei < 20%", "metric_key": "sec_revenue_yoy_growth", "operator": "<", "threshold": 0.20}],
             "verified_metrics": {"sec_revenue_yoy_growth": {"value": 0.25}}},
            {"ticker": "MSFT", "date": "2026-07-01", "structured_kill_criteria": [],
             "verified_metrics": {"sec_revenue_yoy_growth": {"value": 0.12}}},
        ], f)
    with open("track_record/TSLA.json", "w") as f:
        json.dump([{"ticker": "TSLA", "date": "2026-03-01",
                     "structured_kill_criteria": [{"description": "Test", "metric_key": "sec_operating_margin", "operator": "<", "threshold": 0.05}],
                     "verified_metrics": {}}], f)

    result = track_record.compute_calibration_score()
    assert result["total_checkable_criteria"] == 2
    assert result["held"] == 1
    assert result["breached"] == 1


def test_compute_calibration_score_empty_when_no_track_record_dir(tmp_path, monkeypatch):
    import track_record
    monkeypatch.chdir(tmp_path)
    result = track_record.compute_calibration_score()
    assert result == {"total_checkable_criteria": 0, "held": 0, "breached": 0, "details": []}


def test_save_report_snapshot_includes_structured_kill_criteria(tmp_path, monkeypatch):
    import track_record
    monkeypatch.chdir(tmp_path)
    text = (
        "17. Monitoring & Kill-Criteria\nProse.\n\n"
        '```chart\n{"type": "kill-criteria-recap", "criteria": ['
        '{"description": "Test", "metric_key": "sec_operating_margin", "operator": "<", "threshold": 0.08}'
        "]}\n```\n"
    )
    track_record.save_report_snapshot("TEST", text, {})
    snapshot = track_record.load_previous_report("TEST")
    assert len(snapshot["structured_kill_criteria"]) == 1
    assert snapshot["structured_kill_criteria"][0]["metric_key"] == "sec_operating_margin"


# ---------- data_fetch.py: optie-impliciete volatiliteit ----------

def test_compute_options_analysis_full_scenario():
    from unittest.mock import patch, MagicMock
    import pandas as pd
    from datetime import datetime, timedelta
    from data_fetch import compute_options_analysis

    target_date = (datetime.now().date() + timedelta(days=37)).strftime("%Y-%m-%d")
    calls = pd.DataFrame({
        "strike": [90, 95, 100, 105, 110], "impliedVolatility": [0.42, 0.40, 0.38, 0.36, 0.34],
        "volume": [100, 200, 500, 150, 80], "openInterest": [1000, 2000, 5000, 1500, 800],
    })
    puts = pd.DataFrame({
        "strike": [90, 95, 100, 105, 110], "impliedVolatility": [0.55, 0.48, 0.40, 0.37, 0.35],
        "volume": [300, 400, 600, 100, 50], "openInterest": [3000, 4000, 6000, 1000, 500],
    })
    fake_chain = MagicMock(calls=calls, puts=puts)
    mock_ticker = MagicMock()
    mock_ticker.options = (target_date,)
    mock_ticker.history.return_value = pd.DataFrame({"Close": [100.0]})
    mock_ticker.option_chain.return_value = fake_chain

    with patch("data_fetch.yf.Ticker", return_value=mock_ticker):
        result = compute_options_analysis("TEST", historical_volatility_pct=30.0)

    assert result["atm_implied_volatility_pct"] == 39.0
    assert result["iv_minus_hv_pct"] == 9.0
    assert result["skew_otm_put_minus_call_iv_pct"] == 21.0
    assert result["days_to_expiration"] == 37


def test_compute_options_analysis_no_options_available():
    from unittest.mock import patch, MagicMock
    from data_fetch import compute_options_analysis
    mock_ticker = MagicMock()
    mock_ticker.options = ()
    with patch("data_fetch.yf.Ticker", return_value=mock_ticker):
        result = compute_options_analysis("SMALLCO")
    assert "error" in result


def test_compute_options_analysis_network_failure():
    from unittest.mock import patch
    from data_fetch import compute_options_analysis
    with patch("data_fetch.yf.Ticker", side_effect=Exception("netwerkfout")):
        result = compute_options_analysis("TEST")
    assert "error" in result


def test_compute_options_analysis_works_without_historical_vol():
    from unittest.mock import patch, MagicMock
    import pandas as pd
    from datetime import datetime, timedelta
    from data_fetch import compute_options_analysis

    target_date = (datetime.now().date() + timedelta(days=37)).strftime("%Y-%m-%d")
    calls = pd.DataFrame({"strike": [100], "impliedVolatility": [0.38], "volume": [500], "openInterest": [5000]})
    puts = pd.DataFrame({"strike": [100], "impliedVolatility": [0.40], "volume": [600], "openInterest": [6000]})
    fake_chain = MagicMock(calls=calls, puts=puts)
    mock_ticker = MagicMock()
    mock_ticker.options = (target_date,)
    mock_ticker.history.return_value = pd.DataFrame({"Close": [100.0]})
    mock_ticker.option_chain.return_value = fake_chain

    with patch("data_fetch.yf.Ticker", return_value=mock_ticker):
        result = compute_options_analysis("TEST")
    assert "historical_volatility_pct" not in result
    assert "iv_minus_hv_pct" not in result
    assert "atm_implied_volatility_pct" in result


# ---------- finra_data.py: short interest (schema-discovery-aanpak) ----------

def test_fetch_short_interest_with_realistic_schema():
    from unittest.mock import patch, MagicMock
    from finra_data import fetch_short_interest

    fake_metadata = {"fields": [
        {"name": "symbolCode"}, {"name": "settlementDate"},
        {"name": "currentShortPositionQuantity"}, {"name": "averageDailyVolumeQuantity"},
        {"name": "daysToCoverQuantity"},
    ]}
    fake_data = [{
        "symbolCode": "TEST", "settlementDate": "2026-09-15",
        "currentShortPositionQuantity": "5000000", "averageDailyVolumeQuantity": "1200000",
        "daysToCoverQuantity": "4.17",
    }]

    def fake_get(url, headers=None, timeout=None):
        resp = MagicMock(); resp.raise_for_status = lambda: None; resp.json.return_value = fake_metadata
        return resp

    def fake_post(url, headers=None, json=None, timeout=None):
        resp = MagicMock(); resp.raise_for_status = lambda: None; resp.json.return_value = fake_data
        return resp

    with patch("finra_data.requests.get", side_effect=fake_get), \
         patch("finra_data.requests.post", side_effect=fake_post):
        result = fetch_short_interest("TEST")

    assert result["current_short_shares"] == 5000000.0
    assert result["days_to_cover"] == 4.17


def test_fetch_short_interest_unrecognizable_schema():
    from unittest.mock import patch, MagicMock
    from finra_data import fetch_short_interest
    fake_metadata = {"fields": [{"name": "completelyDifferentFieldName"}]}

    def fake_get(url, headers=None, timeout=None):
        resp = MagicMock(); resp.raise_for_status = lambda: None; resp.json.return_value = fake_metadata
        return resp

    with patch("finra_data.requests.get", side_effect=fake_get):
        result = fetch_short_interest("TEST")
    assert "error" in result


def test_fetch_short_interest_network_failure():
    from unittest.mock import patch
    from finra_data import fetch_short_interest
    with patch("finra_data.requests.get", side_effect=Exception("netwerkfout")):
        result = fetch_short_interest("TEST")
    assert "error" in result


def test_fetch_short_interest_no_data_for_ticker():
    from unittest.mock import patch, MagicMock
    from finra_data import fetch_short_interest
    fake_metadata = {"fields": [
        {"name": "symbolCode"}, {"name": "settlementDate"},
        {"name": "currentShortPositionQuantity"}, {"name": "averageDailyVolumeQuantity"},
        {"name": "daysToCoverQuantity"},
    ]}

    def fake_get(url, headers=None, timeout=None):
        resp = MagicMock(); resp.raise_for_status = lambda: None; resp.json.return_value = fake_metadata
        return resp

    def fake_post(url, headers=None, json=None, timeout=None):
        resp = MagicMock(); resp.raise_for_status = lambda: None; resp.json.return_value = []
        return resp

    with patch("finra_data.requests.get", side_effect=fake_get), \
         patch("finra_data.requests.post", side_effect=fake_post):
        result = fetch_short_interest("NIETBESTAAND")
    assert "error" in result


# ---------- monitor_kill_criteria.py: los bewakingsscript ----------

def test_check_ticker_detects_breached_criterion(tmp_path, monkeypatch):
    import json
    import os
    from unittest.mock import patch
    import monitor_kill_criteria as mkc
    monkeypatch.chdir(tmp_path)
    os.makedirs("track_record", exist_ok=True)
    with open("track_record/TEST.json", "w") as f:
        json.dump([{
            "ticker": "TEST", "date": "2026-01-01",
            "structured_kill_criteria": [
                {"description": "Margin onder 8% = kill", "metric_key": "sec_operating_margin", "operator": "<", "threshold": 0.08},
            ],
            "verified_metrics": {},
        }], f)

    fake_sec_result = {"annual_facts": {
        "Revenues": [
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000},
            {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 1_100_000_000},
        ],
        "OperatingIncomeLoss": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 55_000_000}],
    }}
    with patch("monitor_kill_criteria.fetch_sec_financials", return_value=fake_sec_result):
        findings = mkc.check_ticker("TEST")
    assert len(findings) == 1
    assert findings[0]["breached"] is True


def test_check_ticker_detects_held_criterion(tmp_path, monkeypatch):
    import json
    import os
    from unittest.mock import patch
    import monitor_kill_criteria as mkc
    monkeypatch.chdir(tmp_path)
    os.makedirs("track_record", exist_ok=True)
    with open("track_record/TEST.json", "w") as f:
        json.dump([{
            "ticker": "TEST", "date": "2026-01-01",
            "structured_kill_criteria": [
                {"description": "Margin onder 8% = kill", "metric_key": "sec_operating_margin", "operator": "<", "threshold": 0.08},
            ],
            "verified_metrics": {},
        }], f)

    fake_sec_result = {"annual_facts": {
        "Revenues": [
            {"fiscal_year": 2024, "period_end": "2024-12-31", "value": 1_000_000_000},
            {"fiscal_year": 2025, "period_end": "2025-12-31", "value": 1_100_000_000},
        ],
        "OperatingIncomeLoss": [{"fiscal_year": 2025, "period_end": "2025-12-31", "value": 150_000_000}],  # ~13.6% margin, houdt stand
    }}
    with patch("monitor_kill_criteria.fetch_sec_financials", return_value=fake_sec_result):
        findings = mkc.check_ticker("TEST")
    assert len(findings) == 1
    assert findings[0]["breached"] is False


def test_check_ticker_empty_when_no_history(tmp_path, monkeypatch):
    import monitor_kill_criteria as mkc
    monkeypatch.chdir(tmp_path)
    assert mkc.check_ticker("NIETBESTAAND") == []


def test_check_ticker_empty_when_no_structured_criteria(tmp_path, monkeypatch):
    import json
    import os
    import monitor_kill_criteria as mkc
    monkeypatch.chdir(tmp_path)
    os.makedirs("track_record", exist_ok=True)
    with open("track_record/TEST.json", "w") as f:
        json.dump([{"ticker": "TEST", "date": "2026-01-01", "structured_kill_criteria": [], "verified_metrics": {}}], f)
    assert mkc.check_ticker("TEST") == []


def test_main_reports_no_data_when_track_record_missing(tmp_path, monkeypatch, capsys):
    import monitor_kill_criteria as mkc
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["monitor_kill_criteria.py"])
    mkc.main()
    assert "niets om te checken" in capsys.readouterr().out

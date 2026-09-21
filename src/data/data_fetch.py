"""
data_fetch.py
Haalt ruwe financiële data op voor een ticker via yfinance, zodat de agent
die als context kan meegeven aan het analyse-model. Dit is bewust een
"dumb" data-laag: geen interpretatie, alleen verzamelen.
"""

import json
import os
from datetime import datetime, timedelta

import requests
import yfinance as yf


def _compute_annualized_volatility(closing_prices: list[float]) -> float | None:
    """Berekent de geannualiseerde volatiliteit uit een reeks dagelijkse
    slotkoersen: eerst de dagelijkse procentuele rendementen, dan de
    standaarddeviatie daarvan, tot slot geannualiseerd met de vierkantswortel
    van het aantal handelsdagen per jaar (~252) -- de standaardmethode.
    Losstaand van yfinance zodat dit zonder netwerktoegang te testen is."""
    if not closing_prices or len(closing_prices) < 2:
        return None
    returns = [
        (closing_prices[i] - closing_prices[i - 1]) / closing_prices[i - 1]
        for i in range(1, len(closing_prices))
        if closing_prices[i - 1] != 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    daily_std = variance ** 0.5
    return daily_std * (252 ** 0.5)


def fetch_historical_volatility(ticker: str, period: str = "1y") -> dict:
    """Haalt dagelijkse slotkoersen op via yfinance en berekent de
    geannualiseerde volatiliteit -- een kwantitatieve risicomaatstaf i.p.v.
    dat het rapport op gevoel 'nogal volatiel' schrijft."""
    try:
        history = yf.Ticker(ticker).history(period=period)
        closes = history["Close"].tolist() if not history.empty else []
    except Exception:
        return {"error": f"kon historische koersdata niet ophalen voor {ticker}"}

    volatility = _compute_annualized_volatility(closes)
    if volatility is None:
        return {"error": f"onvoldoende historische koersdata voor {ticker} om volatiliteit te berekenen"}

    return {"annualized_volatility_pct": round(volatility * 100, 1), "period": period,
            "trading_days_used": len(closes), "closes": closes}


def compute_value_at_risk(current_price: float, annualized_volatility_pct: float) -> dict:
    """Value at Risk (VaR): het verwachte MAXIMALE verlies bij een gegeven
    betrouwbaarheidsniveau, over een gegeven periode. Parametrische
    (variantie-covariantie) methode -- standaard in de praktijk voor een
    snelle, op volatiliteit gebaseerde schatting: VaR = z-score x
    dagvolatiliteit x huidige koers, waarbij dagvolatiliteit = jaar-
    volatiliteit / wortel(252). Puur wiskunde, geen extra databron nodig --
    hergebruikt de al berekende historische volatiliteit."""
    if not current_price or not annualized_volatility_pct:
        return {"error": "koers of jaarvolatiliteit ontbreekt -- VaR niet berekenbaar"}

    daily_vol = (annualized_volatility_pct / 100) / (252 ** 0.5)
    monthly_vol = (annualized_volatility_pct / 100) / (12 ** 0.5)
    Z_95, Z_99 = 1.645, 2.326  # eenzijdige z-scores voor 95%/99% betrouwbaarheid

    return {
        "var_1day_95pct_pct": round(Z_95 * daily_vol * 100, 2),
        "var_1day_95pct_dollar": round(Z_95 * daily_vol * current_price, 2),
        "var_1day_99pct_pct": round(Z_99 * daily_vol * 100, 2),
        "var_1day_99pct_dollar": round(Z_99 * daily_vol * current_price, 2),
        "var_1month_95pct_pct": round(Z_95 * monthly_vol * 100, 2),
        "var_1month_95pct_dollar": round(Z_95 * monthly_vol * current_price, 2),
        "method": "parametrisch (variantie-covariantie), op basis van 1-jaars historische volatiliteit",
    }


def compute_sharpe_sortino(closes: list[float], risk_free_rate_pct: float) -> dict:
    """Sharpe-ratio: (rendement - risicovrije rente) / volatiliteit --
    hoeveel extra rendement krijg je per eenheid TOTAAL risico. Sortino-
    ratio: hetzelfde, maar gedeeld door alleen de NEERWAARTSE volatiliteit
    (downside deviation) -- straft opwaartse schommelingen niet af, vaak
    gezien als een eerlijkere risicomaatstaf. Beide op basis van dezelfde
    dagelijkse-rendementen-reeks als de bestaande volatiliteitsberekening."""
    if not closes or len(closes) < 30 or risk_free_rate_pct is None:
        return {"error": "onvoldoende koersdata of ontbrekende risicovrije rente -- Sharpe/Sortino niet berekenbaar"}

    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]
    if len(returns) < 30:
        return {"error": "onvoldoende dagelijkse rendementen om Sharpe/Sortino betrouwbaar te berekenen"}

    mean_daily_return = sum(returns) / len(returns)
    annualized_return = mean_daily_return * 252
    variance = sum((r - mean_daily_return) ** 2 for r in returns) / (len(returns) - 1)
    annualized_vol = (variance ** 0.5) * (252 ** 0.5)

    downside_returns = [r for r in returns if r < 0]
    if len(downside_returns) < 2:
        return {"error": "te weinig negatieve dagen in de periode om een downside deviation te berekenen"}
    downside_variance = sum(r ** 2 for r in downside_returns) / (len(downside_returns) - 1)
    downside_deviation = (downside_variance ** 0.5) * (252 ** 0.5)

    risk_free = risk_free_rate_pct / 100
    excess_return = annualized_return - risk_free

    return {
        "annualized_return_pct": round(annualized_return * 100, 1),
        "sharpe_ratio": round(excess_return / annualized_vol, 2) if annualized_vol else None,
        "sortino_ratio": round(excess_return / downside_deviation, 2) if downside_deviation else None,
        "risk_free_rate_pct_used": round(risk_free_rate_pct, 2),
    }


def compute_rolling_beta(ticker: str, period: str = "3y", window_days: int = 90, step_days: int = 21) -> dict:
    """Lopende (rolling) beta i.p.v. één statisch getal -- laat zien hoe de
    marktgevoeligheid van het aandeel is VERANDERD over tijd, in plaats van
    een enkele momentopname. Beta = covariantie(aandeel, markt) /
    variantie(markt), berekend in glijdende vensters van window_days
    handelsdagen, elke step_days dagen opnieuw, tegen de S&P 500 als
    marktbenchmark."""
    try:
        stock_hist = yf.Ticker(ticker).history(period=period)
        market_hist = yf.Ticker("^GSPC").history(period=period)
    except Exception:
        return {"error": f"kon historische koersdata niet ophalen voor {ticker} of de S&P 500-benchmark"}

    if stock_hist.empty or market_hist.empty:
        return {"error": "onvoldoende historische koersdata beschikbaar voor een lopende beta-berekening"}

    stock_hist.index = stock_hist.index.tz_localize(None)
    market_hist.index = market_hist.index.tz_localize(None)
    combined = stock_hist[["Close"]].join(
        market_hist[["Close"]], lsuffix="_stock", rsuffix="_market", how="inner"
    )
    if len(combined) < window_days + step_days:
        return {"error": "onvoldoende overlappende handelsdagen voor een lopende beta-reeks"}

    stock_returns = combined["Close_stock"].pct_change().dropna()
    market_returns = combined["Close_market"].pct_change().dropna()

    dates, betas = [], []
    for end_idx in range(window_days, len(stock_returns), step_days):
        window_stock = stock_returns.iloc[end_idx - window_days:end_idx]
        window_market = market_returns.iloc[end_idx - window_days:end_idx]
        market_var = window_market.var()
        if not market_var:
            continue
        beta = window_stock.cov(window_market) / market_var
        dates.append(str(stock_returns.index[end_idx - 1].date()))
        betas.append(round(float(beta), 2))

    if not betas:
        return {"error": "kon geen lopende beta-reeks berekenen uit de beschikbare data"}

    return {"dates": dates, "betas": betas, "window_days": window_days, "benchmark": "S&P 500 (^GSPC)"}


def compute_regime_detection(ticker: str, period: str = "3y", n_states: int = 2) -> dict:
    """Hidden Markov Model voor regimedetectie: past een Gaussian HMM toe op
    de EIGEN dagelijkse-rendementenreeks van het aandeel (dezelfde data als
    de lopende beta) om te schatten in welke onzichtbare 'toestand' -- bijv.
    kalm/laag-volatiel versus onrustig/hoog-volatiel -- het aandeel zich nu
    waarschijnlijk bevindt, en hoe lang die toestand al aanhoudt. Dit werkt
    op één los aandeel (in tegenstelling tot wat je misschien zou denken)
    -- vereist geen bredere index- of sectordata, al is het bewust van dat
    één-aandeel-schattingen gevoeliger zijn voor bedrijfsspecifieke ruis
    (een enkele gebeurtenis) dan bij een index.

    UPDATE: gebruikt nu simple_hmm.py (een eigen, compacte NumPy-implementatie)
    in plaats van het hmmlearn-package. Reden: hmmlearn heeft op een recente
    Python-versie (3.14) geen kant-en-klare, voorgecompileerde versie en
    probeert dan zelf te bouwen vanaf de broncode -- wat een C++-compiler
    vereist (Microsoft Visual C++ Build Tools) die DD niet had. NumPy zelf
    heeft die eis niet. Geverifieerd op hetzelfde synthetische scenario als
    eerder gebruikt om hmmlearn te testen: vergelijkbare nauwkeurigheid
    (99 van de 100 dagen correct geclassificeerd)."""
    try:
        import numpy as np
        from simple_hmm import fit_gaussian_hmm
    except ImportError as e:
        return {"error": f"kon numpy niet laden: {e}"}

    try:
        history = yf.Ticker(ticker).history(period=period)
        closes = history["Close"].tolist() if not history.empty else []
    except Exception:
        return {"error": f"kon historische koersdata niet ophalen voor {ticker}"}

    if len(closes) < 100:
        return {"error": f"onvoldoende historische koersdata voor {ticker} om een HMM betrouwbaar te schatten"}

    returns = np.array([
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
    ]).reshape(-1, 1)

    try:
        result = fit_gaussian_hmm(returns.flatten(), n_states=n_states, n_iter=200, random_state=42)
        hidden_states = result["hidden_states"]
    except Exception as e:
        return {"error": f"kon geen HMM fitten op de beschikbare data: {e}"}

    # Sorteer de toestanden op volatiliteit (laag naar hoog), zodat de
    # labels ("kalm"/"onrustig") altijd consistent zijn ongeacht welk
    # willekeurig toestand-nummer het model intern toekende.
    state_vols = [v ** 0.5 for v in result["variances"]]
    state_means = result["means"]
    order = sorted(range(n_states), key=lambda i: state_vols[i])
    if n_states == 2:
        labels_nl = ["kalm/laag-volatiel", "onrustig/hoog-volatiel"]
    elif n_states == 3:
        labels_nl = ["kalm/laag-volatiel", "gemiddeld-volatiel", "onrustig/hoog-volatiel"]
    else:
        labels_nl = [f"toestand {i+1} (van laag naar hoog volatiel)" for i in range(n_states)]
    state_to_label = {state_idx: labels_nl[rank] for rank, state_idx in enumerate(order)}

    current_state = int(hidden_states[-1])
    days_in_current_regime = 1
    for s in reversed(hidden_states[:-1]):
        if s == current_state:
            days_in_current_regime += 1
        else:
            break

    regime_history = [state_to_label[int(s)] for s in hidden_states]

    return {
        "current_regime": state_to_label[current_state],
        "days_in_current_regime": days_in_current_regime,
        "regime_stats": {
            state_to_label[i]: {
                "annualized_volatility_pct": round(state_vols[i] * (252 ** 0.5) * 100, 1),
                "annualized_return_pct": round(state_means[i] * 252 * 100, 1),
            }
            for i in range(n_states)
        },
        "n_observations": len(returns),
        "regime_history_tail": regime_history[-60:],  # laatste ~3 maanden, voor een tijdlijngrafiek
    }


def compute_event_price_reaction(ticker: str, event_date: str, window_days: int = 30) -> dict:
    """Meet de OBJECTIEVE koersreactie rond een specifieke, gedateerde
    gebeurtenis (bijv. een contractaankondiging, een regelgevingsbesluit) --
    zet 'dit leek belangrijk nieuws' om in verifieerbaar bewijs (de koers
    bewoog X% op de dag zelf, Y% over de eerstvolgende `window_days` dagen)
    i.p.v. Claude's eigen inschatting van materialiteit.

    event_date: "YYYY-MM-DD"."""
    try:
        event_dt = datetime.strptime(event_date, "%Y-%m-%d")
    except ValueError:
        return {"error": f"ongeldige datum '{event_date}', verwacht formaat YYYY-MM-DD"}

    try:
        start = (event_dt - timedelta(days=5)).strftime("%Y-%m-%d")
        end = (event_dt + timedelta(days=window_days + 5)).strftime("%Y-%m-%d")
        history = yf.Ticker(ticker).history(start=start, end=end)
    except Exception:
        return {"error": f"kon historische koersdata niet ophalen voor {ticker} rond {event_date}"}

    if history.empty:
        return {"error": f"geen koersdata beschikbaar voor {ticker} rond {event_date}"}

    history.index = history.index.tz_localize(None)
    trading_days = history.index[history.index >= event_dt]
    if len(trading_days) == 0:
        return {"error": f"geen handelsdagen gevonden op of na {event_date} -- ligt de datum te ver in de toekomst?"}

    event_trading_day = trading_days[0]
    prior_days = history.index[history.index < event_trading_day]
    if len(prior_days) == 0:
        return {"error": f"geen koersdata beschikbaar van vóór {event_date} om de reactie tegen af te zetten"}

    pre_event_close = history.loc[prior_days[-1], "Close"]
    event_day_close = history.loc[event_trading_day, "Close"]
    day_of_reaction_pct = (event_day_close - pre_event_close) / pre_event_close * 100

    window_days_available = history.index[history.index >= event_trading_day]
    window_close = history.loc[window_days_available[-1], "Close"]
    window_reaction_pct = (window_close - pre_event_close) / pre_event_close * 100
    actual_window_days = (window_days_available[-1] - event_trading_day).days

    return {
        "event_date": event_date,
        "day_of_reaction_pct": round(float(day_of_reaction_pct), 1),
        "window_reaction_pct": round(float(window_reaction_pct), 1),
        "window_days_requested": window_days,
        "window_days_actually_available": int(actual_window_days),
    }


def fetch_company_data(ticker: str) -> dict:
    """Haalt kerncijfers, balans, resultatenrekening en peer-info op."""
    t = yf.Ticker(ticker)
    info = t.info or {}

    data = {
        "ticker": ticker,
        "long_name": info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "business_summary": info.get("longBusinessSummary"),
        "market_cap": info.get("marketCap"),
        "currency": info.get("currency"),

        # Waardering
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "price_to_book": info.get("priceToBook"),
        "ev_to_ebitda": info.get("enterpriseToEbitda"),
        "peg_ratio": info.get("pegRatio"),

        # Winstgevendheid
        "gross_margins": info.get("grossMargins"),
        "operating_margins": info.get("operatingMargins"),
        "profit_margins": info.get("profitMargins"),
        "return_on_equity": info.get("returnOnEquity"),
        "return_on_assets": info.get("returnOnAssets"),

        # Groei
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),

        # Eigendomsstructuur (gratis via yfinance, nog niet eerder gebruikt)
        "held_percent_insiders": info.get("heldPercentInsiders"),
        "held_percent_institutions": info.get("heldPercentInstitutions"),

        # Schuld / liquiditeit
        "total_debt": info.get("totalDebt"),
        "total_cash": info.get("totalCash"),
        "debt_to_equity": info.get("debtToEquity"),
        "current_ratio": info.get("currentRatio"),
        "quick_ratio": info.get("quickRatio"),
        "free_cashflow": info.get("freeCashflow"),
        "operating_cashflow": info.get("operatingCashflow"),

        # Dividend
        "dividend_yield": info.get("dividendYield"),
        "payout_ratio": info.get("payoutRatio"),

        # Overig
        "beta": info.get("beta"),
        "52_week_high": info.get("fiftyTwoWeekHigh"),
        "52_week_low": info.get("fiftyTwoWeekLow"),
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "shares_outstanding": info.get("sharesOutstanding"),
        "employees": info.get("fullTimeEmployees"),
        "country": info.get("country"),
        "website": info.get("website"),
    }

    # Financiële statements (laatste jaren), voor debt-structuur en trends
    try:
        data["balance_sheet"] = json.loads(t.balance_sheet.to_json())
    except Exception:
        data["balance_sheet"] = None
    try:
        data["income_statement"] = json.loads(t.income_stmt.to_json())
    except Exception:
        data["income_statement"] = None
    try:
        data["cashflow_statement"] = json.loads(t.cashflow.to_json())
    except Exception:
        data["cashflow_statement"] = None

    return data


def fetch_peer_data(peer_tickers: list[str]) -> dict:
    """Lichte dataset voor concurrenten, puur voor ratio-vergelijking."""
    peers = {}
    for pt in peer_tickers:
        try:
            info = yf.Ticker(pt).info or {}
            peers[pt] = {
                "long_name": info.get("longName"),
                "trailing_pe": info.get("trailingPE"),
                "ev_to_ebitda": info.get("enterpriseToEbitda"),
                "profit_margins": info.get("profitMargins"),
                "return_on_equity": info.get("returnOnEquity"),
                "debt_to_equity": info.get("debtToEquity"),
                "revenue_growth": info.get("revenueGrowth"),
            }
        except Exception:
            peers[pt] = {"error": "kon data niet ophalen"}
    return peers


def fetch_recent_news(ticker: str, limit: int = 10) -> dict:
    """Haalt recent nieuws + sentiment op via Alpha Vantage (NEWS_SENTIMENT).

    Dit is bewust een losse functie, net als fetch_company_data en
    fetch_peer_data hierboven -- zelfde patroon (adres -> verzoek -> JSON
    terug), andere bron en ander doel (sectie 12: recente ontwikkelingen).

    BELANGRIJK voor tokenverbruik: Alpha Vantage geeft standaard tot 50
    artikelen terug, elk met velden die je nooit nodig hebt (banner_image,
    volledige author-lijst, topic-relevance-scores, sentiment voor ELKE
    ticker in het artikel i.p.v. alleen de jouwe). Dat hele pakket ging
    voorheen ongefilterd de messages-lijst in -- goed voor tienduizenden
    tokens per run. We beperken nu (1) het AANTAL artikelen via de
    `limit`-parameter van de API zelf, en (2) welke VELDEN we per artikel
    doorgeven.
    """
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not api_key:
        return {"error": "ALPHAVANTAGE_API_KEY niet gevonden in environment"}

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "apikey": api_key,
        "limit": limit,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        raw = response.json()
    except Exception:
        return {"error": f"kon geen nieuws ophalen voor {ticker}"}

    if "feed" not in raw:
        return raw  # geef foutmeldingen/rate-limit-berichten van Alpha Vantage ongewijzigd door

    trimmed_articles = []
    for article in raw["feed"]:
        # Zoek het sentiment-record voor DEZE ticker specifiek, niet voor de
        # andere tickers die het artikel toevallig ook noemt.
        ticker_sentiment = next(
            (t for t in article.get("ticker_sentiment", []) if t.get("ticker") == ticker),
            {},
        )
        summary = article.get("summary", "")
        trimmed_articles.append({
            "title": article.get("title"),
            "source": article.get("source"),
            "time_published": article.get("time_published"),
            "summary": summary[:400],  # samenvattingen kunnen lang zijn; 400 tekens is ruim genoeg voor context
            "overall_sentiment_label": article.get("overall_sentiment_label"),
            "relevance_to_ticker": ticker_sentiment.get("relevance_score"),
            "ticker_sentiment_label": ticker_sentiment.get("ticker_sentiment_label"),
        })

    return {"articles": trimmed_articles}


def compute_options_analysis(ticker: str, historical_volatility_pct: float | None = None) -> dict:
    """Analyseert de optieketen van het dichtstbijzijnde 'front-month'-
    contract (30-45 dagen tot expiratie -- de gebruikelijke conventie voor
    een representatieve, liquide IV-meting) om te zien hoe de OPTIEMARKT
    de toekomstige volatiliteit inprijst -- een fundamenteel ANDER,
    VOORUITKIJKEND risicosignaal dan onze eigen VaR/volatiliteit (die
    TERUGKIJKEND is, gebaseerd op historische koersbewegingen).

    Geeft terug: at-the-money implied volatility, put/call-volume- en
    open-interest-ratio's (klassieke sentiment-indicatoren), en de 'skew'
    (verschil tussen ongeveer 10%-out-of-the-money put- en call-IV --
    positief betekent dat de markt extra betaalt voor neerwaartse
    bescherming, een klassiek angst-signaal). Als historical_volatility_pct
    wordt meegegeven, voegt dit ook het verschil met de eigen (terugkijkende)
    volatiliteit toe -- dat verschil is zelf een informatief datapunt (IV
    fors hoger dan HV kan duiden op ingeprijsde onzekerheid, bijv. een
    aankomend earnings- of rechtszaak-moment).

    Niet elk bedrijf heeft verhandelde opties (vooral kleinere/net
    beursgenoteerde bedrijven niet) -- geeft dan netjes een foutmelding
    terug in plaats van te crashen. Gebruikt Yahoo's eigen, al-berekende
    implied volatility per contract -- we rekenen zelf geen Black-Scholes
    uit, dat zou een extra, onnodige bron van eigen rekenfouten zijn."""
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
    except Exception:
        return {"error": f"kon geen optiedata ophalen voor {ticker}"}

    if not expirations:
        return {"error": f"geen verhandelde opties gevonden voor {ticker} (vaak het geval bij kleinere of net beursgenoteerde bedrijven)"}

    try:
        current_price = float(tk.history(period="1d")["Close"].iloc[-1])
    except Exception:
        return {"error": f"kon geen actuele koers ophalen voor {ticker}"}

    # Kies de expiratie die het dichtst bij ~37 dagen ligt (midden van de
    # gebruikelijke 30-45-dagen 'front-month'-conventie) -- liquide genoeg
    # om betrouwbaar te zijn, niet zo kort dat één nieuwsgebeurtenis de
    # IV vertekent.
    today = datetime.now().date()
    target_days = 37

    def _days_to_exp(exp_str: str) -> int:
        return (datetime.strptime(exp_str, "%Y-%m-%d").date() - today).days

    valid_expirations = [(e, _days_to_exp(e)) for e in expirations if _days_to_exp(e) > 0]
    if not valid_expirations:
        return {"error": f"geen toekomstige optie-expiraties gevonden voor {ticker}"}
    chosen_expiration, days_out = min(valid_expirations, key=lambda pair: abs(pair[1] - target_days))

    try:
        chain = tk.option_chain(chosen_expiration)
        calls, puts = chain.calls, chain.puts
    except Exception:
        return {"error": f"kon de optieketen niet ophalen voor {ticker} (expiratie {chosen_expiration})"}

    if calls.empty and puts.empty:
        return {"error": f"lege optieketen voor {ticker} (expiratie {chosen_expiration})"}

    def _closest_strike_iv(df, target_strike: float) -> float | None:
        if df.empty:
            return None
        idx = (df["strike"] - target_strike).abs().idxmin()
        iv = df.loc[idx, "impliedVolatility"]
        return float(iv) if iv and iv > 0 else None

    atm_call_iv = _closest_strike_iv(calls, current_price)
    atm_put_iv = _closest_strike_iv(puts, current_price)
    atm_iv_values = [v for v in (atm_call_iv, atm_put_iv) if v is not None]
    if not atm_iv_values:
        return {"error": f"geen bruikbare implied volatility gevonden rond de huidige koers voor {ticker}"}
    atm_iv_pct = sum(atm_iv_values) / len(atm_iv_values) * 100

    # Skew: ongeveer 10% out-of-the-money in beide richtingen.
    otm_put_iv = _closest_strike_iv(puts, current_price * 0.90)
    otm_call_iv = _closest_strike_iv(calls, current_price * 1.10)
    skew_pct = round((otm_put_iv - otm_call_iv) * 100, 1) if otm_put_iv is not None and otm_call_iv is not None else None

    call_volume = float(calls["volume"].fillna(0).sum())
    put_volume = float(puts["volume"].fillna(0).sum())
    call_oi = float(calls["openInterest"].fillna(0).sum())
    put_oi = float(puts["openInterest"].fillna(0).sum())

    result = {
        "expiration_used": chosen_expiration,
        "days_to_expiration": days_out,
        "current_price": round(current_price, 2),
        "atm_implied_volatility_pct": round(atm_iv_pct, 1),
        "put_call_volume_ratio": round(put_volume / call_volume, 2) if call_volume > 0 else None,
        "put_call_open_interest_ratio": round(put_oi / call_oi, 2) if call_oi > 0 else None,
        "skew_otm_put_minus_call_iv_pct": skew_pct,
    }
    if historical_volatility_pct is not None:
        result["historical_volatility_pct"] = historical_volatility_pct
        result["iv_minus_hv_pct"] = round(atm_iv_pct - historical_volatility_pct, 1)
    return result



if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    d = fetch_company_data(ticker)
    print(json.dumps({k: v for k, v in d.items() if k not in
                       ("balance_sheet", "income_statement", "cashflow_statement")},
                      indent=2, default=str))

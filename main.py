import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query

app = FastAPI(
    title="FRS Data API",
    version="1.0.0",
    description="Normalized market data API for FRS."
)


def now_ms():
    return int(time.time() * 1000)


def iso_from_ms(ms: Optional[int]):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def status_from_age(age_seconds: Optional[float]):
    if age_seconds is None:
        return "UNAVAILABLE"
    if age_seconds <= 120:
        return "LIVE_VERIFIED"
    if age_seconds <= 900:
        return "STALE"
    return "STALE"


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "FRS Data API",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def get_finnhub_quote(symbol: str):
    api_key = os.getenv("FINNHUB_API_KEY")

    if not api_key:
        return {
            "symbol": symbol,
            "market": "US",
            "status": "API_ERROR",
            "error": "FINNHUB_API_KEY is not configured"
        }

    url = "https://finnhub.io/api/v1/quote"

    response = requests.get(
        url,
        params={
            "symbol": symbol.upper(),
            "token": api_key
        },
        timeout=10
    )

    if response.status_code != 200:
        return {
            "symbol": symbol,
            "market": "US",
            "status": "API_ERROR",
            "error": f"Finnhub HTTP {response.status_code}"
        }

    data = response.json()

    price = data.get("c")
    timestamp = data.get("t")

    if not price or not timestamp:
        return {
            "symbol": symbol.upper(),
            "market": "US",
            "status": "UNAVAILABLE",
            "source": "FINNHUB"
        }

    provider_ms = int(timestamp) * 1000
    age = max(0, (now_ms() - provider_ms) / 1000)

    return {
        "symbol": symbol.upper(),
        "market": "US",
        "price": price,
        "open": data.get("o"),
        "high": data.get("h"),
        "low": data.get("l"),
        "prev_close": data.get("pc"),
        "volume": None,
        "turnover": None,
        "vwap": None,
        "session": "REGULAR",
        "source": "FINNHUB",
        "provider_timestamp": iso_from_ms(provider_ms),
        "api_received_timestamp": datetime.now(timezone.utc).isoformat(),
        "data_age_seconds": round(age, 2),
        "status": status_from_age(age),
        "price_type": "LIVE"
    }


def get_fugle_quote(symbol: str):
    api_key = os.getenv("FUGLE_API_KEY")

    if not api_key:
        return {
            "symbol": symbol,
            "market": "TW",
            "status": "API_ERROR",
            "error": "FUGLE_API_KEY is not configured"
        }

    url = (
        "https://api.fugle.tw/"
        "marketdata/v1.0/stock/intraday/quote/"
        f"{symbol}"
    )

    response = requests.get(
        url,
        headers={
            "X-API-KEY": api_key
        },
        timeout=10
    )

    if response.status_code != 200:
        return {
            "symbol": symbol,
            "market": "TW",
            "status": "API_ERROR",
            "error": f"Fugle HTTP {response.status_code}"
        }

    data = response.json()

    # Fugle may return quote data inside different wrappers.
    quote = data.get("data", data)

    price = (
        quote.get("lastPrice")
        or quote.get("closePrice")
    )

    if price is None:
        return {
            "symbol": symbol,
            "market": "TW",
            "status": "UNAVAILABLE",
            "source": "FUGLE"
        }

    last_updated = quote.get("lastUpdated")

    age = None

    if last_updated:
        try:
            dt = datetime.fromisoformat(
                last_updated.replace("Z", "+00:00")
            )
            age = (
                datetime.now(timezone.utc) - dt
            ).total_seconds()
            age = max(0, age)
        except Exception:
            age = None

    return {
        "symbol": symbol,
        "market": "TW",
        "price": price,
        "open": quote.get("openPrice"),
        "high": quote.get("highPrice"),
        "low": quote.get("lowPrice"),
        "prev_close": quote.get("previousClose"),
        "volume": quote.get("tradeVolume"),
        "turnover": quote.get("tradeValue"),
        "vwap": quote.get("avgPrice"),
        "session": "REGULAR",
        "source": "FUGLE",
        "provider_timestamp": last_updated,
        "api_received_timestamp": datetime.now(timezone.utc).isoformat(),
        "data_age_seconds": round(age, 2) if age is not None else None,
        "status": status_from_age(age),
        "price_type": "LIVE"
    }


@app.get("/v1/quote")
def quote(
    symbol: str = Query(...),
    market: str = Query(...)
):
    market = market.upper()

    if market == "US":
        return get_finnhub_quote(symbol)

    if market == "TW":
        return get_fugle_quote(symbol)

    raise HTTPException(
        status_code=400,
        detail="market must be TW or US"
    )


@app.get("/v1/batch-quotes")
def batch_quotes(
    symbols: str = Query(...),
    market: str = Query(...)
):
    market = market.upper()

    symbol_list = [
        x.strip()
        for x in symbols.split(",")
        if x.strip()
    ]

    results = []

    for symbol in symbol_list:
        if market == "US":
            results.append(get_finnhub_quote(symbol))
        elif market == "TW":
            results.append(get_fugle_quote(symbol))
        else:
            raise HTTPException(
                status_code=400,
                detail="market must be TW or US"
            )

    return {
        "market": market,
        "count": len(results),
        "results": results
    }


@app.get("/v1/market-status")
def market_status(
    market: str = Query(...)
):
    market = market.upper()

    if market not in ["TW", "US"]:
        raise HTTPException(
            status_code=400,
            detail="market must be TW or US"
        )

    return {
        "market": market,
        "status": "UNKNOWN",
        "note": "Market-status provider integration will be added in the next FRS API version."
    }


@app.get("/v1/frs-context")
def frs_context(
    symbol: str = Query(...),
    market: str = Query(...)
):
    market = market.upper()

    if market == "US":
        quote_data = get_finnhub_quote(symbol)
    elif market == "TW":
        quote_data = get_fugle_quote(symbol)
    else:
        raise HTTPException(
            status_code=400,
            detail="market must be TW or US"
        )

    return {
        "frs_version": "FRS V31.1-T1-PRO-CANONICAL",
        "symbol": symbol.upper(),
        "market": market,
        "quote": quote_data,
        "t0_allowed": (
            quote_data.get("status") == "LIVE_VERIFIED"
        ),
        "hard_rule": (
            "STALE/API_ERROR/UNAVAILABLE cannot be used "
            "as FRS T0 live price."
        )
    }

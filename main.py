import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Query

app = FastAPI(
    title="FRS Data API",
    version="1.1.0",
    description="Normalized market data API for FRS V31.1."
)


# ---------------------------------------------------------
# Time helpers
# ---------------------------------------------------------

def now_utc():
    return datetime.now(timezone.utc)


def now_ms():
    return int(time.time() * 1000)


def parse_provider_timestamp(value):
    """
    Supports:
    - ISO 8601 string
    - Unix seconds
    - Unix milliseconds
    - Unix microseconds
    """
    if value is None:
        return None

    try:
        if isinstance(value, (int, float)):
            value = float(value)

            # seconds
            if value < 10_000_000_000:
                return datetime.fromtimestamp(
                    value,
                    tz=timezone.utc
                )

            # milliseconds
            if value < 10_000_000_000_000:
                return datetime.fromtimestamp(
                    value / 1000,
                    tz=timezone.utc
                )

            # microseconds
            return datetime.fromtimestamp(
                value / 1_000_000,
                tz=timezone.utc
            )

        if isinstance(value, str):
            text = value.strip()

            # numeric string
            try:
                numeric = float(text)

                if numeric < 10_000_000_000:
                    return datetime.fromtimestamp(
                        numeric,
                        tz=timezone.utc
                    )

                if numeric < 10_000_000_000_000:
                    return datetime.fromtimestamp(
                        numeric / 1000,
                        tz=timezone.utc
                    )

                return datetime.fromtimestamp(
                    numeric / 1_000_000,
                    tz=timezone.utc
                )

            except ValueError:
                pass

            # ISO timestamp
            dt = datetime.fromisoformat(
                text.replace("Z", "+00:00")
            )

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt.astimezone(timezone.utc)

    except Exception:
        return None

    return None


def timestamp_to_iso(value):
    dt = parse_provider_timestamp(value)

    if not dt:
        return None

    return dt.isoformat()


def calculate_age_seconds(value):
    dt = parse_provider_timestamp(value)

    if not dt:
        return None

    age = (
        now_utc() - dt
    ).total_seconds()

    return max(0, age)


# ---------------------------------------------------------
# FRS hard status rules
# ---------------------------------------------------------

def determine_status(age_seconds):
    """
    FRS hard rule:

    LIVE_VERIFIED:
        Fresh provider timestamp <= 120 seconds.

    STALE:
        Timestamp exists but is older than 120 seconds.

    UNAVAILABLE:
        No valid provider timestamp.

    API_ERROR:
        Provider request failed.
    """

    if age_seconds is None:
        return "UNAVAILABLE"

    if age_seconds <= 120:
        return "LIVE_VERIFIED"

    return "STALE"


def t0_allowed(status):
    return status == "LIVE_VERIFIED"


# ---------------------------------------------------------
# Health
# ---------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "FRS Data API",
        "version": "1.1.0",
        "timestamp": now_utc().isoformat()
    }


# ---------------------------------------------------------
# Finnhub - US
# ---------------------------------------------------------

def get_finnhub_quote(symbol: str):

    api_key = os.getenv("FINNHUB_API_KEY")

    if not api_key:
        return {
            "symbol": symbol.upper(),
            "market": "US",
            "status": "API_ERROR",
            "error": "FINNHUB_API_KEY is not configured"
        }

    try:
        response = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={
                "symbol": symbol.upper(),
                "token": api_key
            },
            timeout=10
        )

    except requests.RequestException as exc:
        return {
            "symbol": symbol.upper(),
            "market": "US",
            "status": "API_ERROR",
            "error": str(exc)
        }

    if response.status_code != 200:
        return {
            "symbol": symbol.upper(),
            "market": "US",
            "status": "API_ERROR",
            "error": f"Finnhub HTTP {response.status_code}"
        }

    data = response.json()

    price = data.get("c")
    provider_timestamp = data.get("t")

    if price is None:
        return {
            "symbol": symbol.upper(),
            "market": "US",
            "status": "UNAVAILABLE",
            "source": "FINNHUB"
        }

    age = calculate_age_seconds(
        provider_timestamp
    )

    status = determine_status(age)

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
        "provider_timestamp": timestamp_to_iso(
            provider_timestamp
        ),
        "api_received_timestamp": now_utc().isoformat(),
        "data_age_seconds": (
            round(age, 2)
            if age is not None
            else None
        ),
        "status": status,
        "price_type": "LIVE" if status == "LIVE_VERIFIED"
        else "HISTORICAL",
        "t0_allowed": t0_allowed(status)
    }


# ---------------------------------------------------------
# Fugle - Taiwan
# ---------------------------------------------------------

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

    try:
        response = requests.get(
            url,
            headers={
                "X-API-KEY": api_key
            },
            timeout=10
        )

    except requests.RequestException as exc:
        return {
            "symbol": symbol,
            "market": "TW",
            "status": "API_ERROR",
            "error": str(exc)
        }

    if response.status_code != 200:
        return {
            "symbol": symbol,
            "market": "TW",
            "status": "API_ERROR",
            "error": f"Fugle HTTP {response.status_code}"
        }

    data = response.json()

    quote = data.get("data", data)

    price = (
        quote.get("lastPrice")
        if quote.get("lastPrice") is not None
        else quote.get("closePrice")
    )

    if price is None:
        return {
            "symbol": symbol,
            "market": "TW",
            "status": "UNAVAILABLE",
            "source": "FUGLE"
        }

    provider_timestamp = quote.get(
        "lastUpdated"
    )

    age = calculate_age_seconds(
        provider_timestamp
    )

    status = determine_status(age)

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
        "provider_timestamp": timestamp_to_iso(
            provider_timestamp
        ),
        "api_received_timestamp": now_utc().isoformat(),
        "data_age_seconds": (
            round(age, 2)
            if age is not None
            else None
        ),
        "status": status,
        "price_type": "LIVE" if status == "LIVE_VERIFIED"
        else "HISTORICAL",
        "t0_allowed": t0_allowed(status)
    }


# ---------------------------------------------------------
# Quote
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Batch quotes
# ---------------------------------------------------------

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
            results.append(
                get_finnhub_quote(symbol)
            )

        elif market == "TW":
            results.append(
                get_fugle_quote(symbol)
            )

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


# ---------------------------------------------------------
# Market status
# ---------------------------------------------------------

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
        "note": "Market-status integration will be added in a later FRS API version."
    }


# ---------------------------------------------------------
# FRS Context
# ---------------------------------------------------------

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

    status = quote_data.get("status")

    return {
        "frs_version": "FRS V31.1-T1-PRO-CANONICAL",
        "symbol": symbol.upper(),
        "market": market,
        "quote": quote_data,
        "t0_allowed": (
            status == "LIVE_VERIFIED"
        ),
        "execution_gate": {
            "status": status,
            "executable_t0": (
                status == "LIVE_VERIFIED"
            ),
            "blocked_statuses": [
                "STALE",
                "API_ERROR",
                "UNAVAILABLE"
            ]
        },
        "hard_rule": (
            "Only LIVE_VERIFIED may be used "
            "as an executable FRS T0 live price."
        )
    }
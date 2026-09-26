# FRS Data API v1.5

Independent market-data API for FRS V31.1-T1-PRO-CANONICAL.

Render:
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health: `/health`

Providers: Taiwan Fugle; US Twelve Data primary with Finnhub fallback. T0 is permitted only for `LIVE_VERIFIED` quotes.

Environment variables: `FUGLE_API_KEY`, `TWELVE_DATA_API_KEY`, `FINNHUB_API_KEY`, `US_PRIMARY_PROVIDER`, `QUOTE_MAX_AGE_SECONDS`, `TW_HOLIDAYS`.

"""
Market Data Service — Multi-tier real-time data architecture.

Tier 1 (REAL-TIME, <50ms): Angel One SmartAPI WebSocket
  - Free with demat account (zero subscription)
  - Tick-by-tick LTP streaming
  - Configure credentials in .env

Tier 2 (NEAR-REAL-TIME, ~1-5s): yfinance
  - Free, no account needed
  - Good for historical data + backtesting
  - NOT suitable for live scalping (15-min delay possible)

Tier 3 (MOCK): Synthetic data
  - For development/testing when no API is available

Option Chain: NSE India official API (free, ~2s refresh)
"""

import logging
import math
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
from collections import defaultdict

import pandas as pd
import numpy as np

import config

logger = logging.getLogger(__name__)

# ── Lazy imports ──────────────────────────────────────────
try:
    import yfinance as yf
    YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

try:
    from SmartApi import SmartConnect
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
    SMARTAPI_OK = True
except ImportError:
    SMARTAPI_OK = False

try:
    import pyotp
    PYOTP_OK = True
except ImportError:
    PYOTP_OK = False

try:
    import requests as _requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False


# ── yfinance ticker mapping ──────────────────────────────
YFINANCE_TICKERS = {
    "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS", "INDIAVIX": "^INDIAVIX",
    "RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS",
    "HDFCBANK": "HDFCBANK.NS", "INFY": "INFY.NS",
    "ICICIBANK": "ICICIBANK.NS", "SBIN": "SBIN.NS",
    "BAJFINANCE": "BAJFINANCE.NS", "ITC": "ITC.NS",
    "TATAMOTORS": "TATAMOTORS.NS", "AXISBANK": "AXISBANK.NS",
}

# NSE headers for option chain scraping
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}

# Angel One SmartAPI instrument tokens (NSE indices)
SMARTAPI_TOKENS = {
    "NIFTY": "99926000",     # NSE NIFTY 50
    "BANKNIFTY": "99926009", # NSE BANK NIFTY
    "FINNIFTY": "99926037",  # NSE FINNIFTY
    "INDIAVIX": "99926004",  # INDIA VIX
}


class MarketDataService:
    """
    Multi-tier market data with automatic fallback.

    Priority:
    1. Angel One WebSocket (real-time, <50ms latency, FREE with account)
    2. yfinance (historical + near-real-time fallback)
    3. Mock data (development/testing)
    """

    def __init__(self):
        self._tier = "MOCK"  # Current active tier
        self._prices: dict[str, float] = {}
        self._price_timestamps: dict[str, float] = {}
        self._oc_cache: dict[str, pd.DataFrame] = {}
        self._oc_ts: dict[str, datetime] = {}
        self._hist_cache: dict[str, pd.DataFrame] = {}
        self._nse_session = None
        self._ws = None
        self._ws_connected = False
        self._lock = threading.Lock()

        # Rate limit tracking
        self._yf_last_call: float = 0
        self._yf_min_interval: float = 1.0  # Min 1 second between yfinance calls
        self._yf_backoff: float = 1.0  # Exponential backoff multiplier
        self._nse_last_call: float = 0
        self._nse_min_interval: float = 3.0  # NSE blocks rapid requests

        # Try connecting tiers in priority order
        self._init_tier1_smartapi()
        if self._tier != "WEBSOCKET":
            self._init_tier2_yfinance()

    @property
    def is_connected(self) -> bool:
        return self._tier != "MOCK"

    @property
    def data_tier(self) -> str:
        return self._tier

    @property
    def latency_estimate(self) -> str:
        if self._tier == "WEBSOCKET":
            return "<50ms (real-time WebSocket)"
        elif self._tier == "YFINANCE":
            return "1-15s (yfinance polling)"
        return "N/A (mock data)"

    # ═══════════════════════════════════════════════════════
    #  TIER 1: Angel One SmartAPI WebSocket (REAL-TIME)
    # ═══════════════════════════════════════════════════════

    def _init_tier1_smartapi(self):
        """Connect to Angel One WebSocket for real-time tick data."""
        if not SMARTAPI_OK:
            logger.info("SmartAPI not installed. Install: pip install smartapi-python pyotp")
            return
        if not PYOTP_OK:
            logger.info("pyotp not installed. Install: pip install pyotp")
            return

        api_key = config.ANGEL_API_KEY
        client_id = config.ANGEL_CLIENT_ID
        password = config.ANGEL_PASSWORD
        totp_secret = config.ANGEL_TOTP_SECRET

        if not all([api_key, client_id, password, totp_secret]):
            logger.info("Angel One credentials not configured in .env — skipping WebSocket.")
            return

        try:
            # Generate session
            smart_api = SmartConnect(api_key=api_key)
            totp = pyotp.TOTP(totp_secret).now()
            session_data = smart_api.generateSession(client_id, password, totp)

            if session_data.get("status"):
                feed_token = smart_api.getfeedToken()
                jwt_token = session_data["data"]["jwtToken"]

                # Start WebSocket in background thread
                self._start_websocket(jwt_token, api_key, client_id, feed_token)
                self._tier = "WEBSOCKET"
                logger.info("✅ TIER 1 ACTIVE: Angel One WebSocket (real-time, <50ms)")
            else:
                logger.warning("SmartAPI session failed: %s", session_data.get("message"))

        except Exception as e:
            logger.warning("SmartAPI init failed: %s", e)

    def _start_websocket(self, jwt_token, api_key, client_id, feed_token):
        """Start WebSocket streaming in background thread."""
        def _run():
            try:
                sws = SmartWebSocketV2(jwt_token, api_key, client_id, feed_token)

                def on_data(wsapp, message):
                    """Handle incoming tick data."""
                    try:
                        token = str(message.get("token", ""))
                        ltp = message.get("last_traded_price", 0)
                        if ltp:
                            ltp = ltp / 100  # SmartAPI sends price * 100
                            # Reverse-map token to symbol
                            for sym, tok in SMARTAPI_TOKENS.items():
                                if tok == token:
                                    with self._lock:
                                        self._prices[sym] = ltp
                                        self._price_timestamps[sym] = time.time()
                                    break
                    except Exception as e:
                        logger.debug("WS tick error: %s", e)

                def on_open(wsapp):
                    logger.info("WebSocket connected — subscribing to instruments...")
                    # Subscribe to NIFTY and BANKNIFTY LTP (mode 1 = LTP only)
                    tokens = [
                        {"exchangeType": 1, "tokens": [SMARTAPI_TOKENS["NIFTY"]]},
                        {"exchangeType": 1, "tokens": [SMARTAPI_TOKENS["BANKNIFTY"]]},
                    ]
                    sws.subscribe("nifty_bn", 1, tokens)
                    self._ws_connected = True

                def on_error(wsapp, error):
                    logger.error("WebSocket error: %s", error)
                    self._ws_connected = False

                def on_close(wsapp):
                    logger.warning("WebSocket disconnected. Reconnecting in 5s...")
                    self._ws_connected = False
                    time.sleep(5)
                    # Auto-reconnect
                    try:
                        sws.connect()
                    except Exception:
                        pass

                sws.on_data = on_data
                sws.on_open = on_open
                sws.on_error = on_error
                sws.on_close = on_close
                self._ws = sws
                sws.connect()

            except Exception as e:
                logger.error("WebSocket thread failed: %s", e)

        ws_thread = threading.Thread(target=_run, daemon=True, name="ws-feed")
        ws_thread.start()

    # ═══════════════════════════════════════════════════════
    #  TIER 2: yfinance (historical + fallback)
    # ═══════════════════════════════════════════════════════

    def _init_tier2_yfinance(self):
        """Test yfinance connection."""
        if not YFINANCE_OK:
            logger.warning("yfinance not installed — using mock mode.")
            return

        try:
            ticker = yf.Ticker("^NSEI")
            hist = ticker.history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                self._prices["NIFTY"] = price
                self._price_timestamps["NIFTY"] = time.time()
                self._tier = "YFINANCE"
                logger.info("✅ TIER 2 ACTIVE: yfinance (NIFTY: %.2f)", price)
                logger.warning("⚠️  yfinance has ~15min delay. For real-time, add Angel One credentials.")
            else:
                logger.warning("yfinance returned empty. Falling back to mock.")
        except Exception as e:
            logger.warning("yfinance test failed: %s. Using mock.", e)

    # ═══════════════════════════════════════════════════════
    #  PUBLIC API: get_ltp()
    # ═══════════════════════════════════════════════════════

    def get_ltp(self, symbol: str, seg: str = "") -> Optional[float]:
        """
        Get Last Traded Price with automatic tier fallback.
        WebSocket: instant from cache (<1ms)
        yfinance: rate-limited polling (~1-5s)
        """
        symbol = symbol.upper()

        # Tier 1: WebSocket cache (instant, <1ms)
        if self._tier == "WEBSOCKET":
            with self._lock:
                price = self._prices.get(symbol)
                ts = self._price_timestamps.get(symbol, 0)
            if price and (time.time() - ts) < 10:  # Fresh if < 10s old
                return price
            # If WebSocket data is stale, fall through to yfinance

        # Tier 2: yfinance with rate limiting
        if YFINANCE_OK:
            now = time.time()
            if now - self._yf_last_call >= self._yf_min_interval * self._yf_backoff:
                try:
                    ticker_sym = YFINANCE_TICKERS.get(symbol, f"{symbol}.NS")
                    ticker = yf.Ticker(ticker_sym)

                    # Try fast_info first
                    try:
                        price = ticker.fast_info.last_price
                        if price and price > 0:
                            self._prices[symbol] = float(price)
                            self._price_timestamps[symbol] = now
                            self._yf_last_call = now
                            self._yf_backoff = max(1.0, self._yf_backoff * 0.9)  # Reduce backoff
                            return float(price)
                    except Exception:
                        pass

                    # Fallback: latest history
                    hist = ticker.history(period="1d")
                    if not hist.empty:
                        price = float(hist["Close"].iloc[-1])
                        self._prices[symbol] = price
                        self._price_timestamps[symbol] = now
                        self._yf_last_call = now
                        self._yf_backoff = max(1.0, self._yf_backoff * 0.9)
                        return price

                except Exception as e:
                    if "rate" in str(e).lower() or "429" in str(e):
                        self._yf_backoff = min(60.0, self._yf_backoff * 2)
                        logger.warning("yfinance rate limited. Backoff: %.1fs", self._yf_backoff)
                    else:
                        logger.debug("yfinance LTP error for %s: %s", symbol, e)
                    self._yf_last_call = now

        # Return cached or mock
        cached = self._prices.get(symbol)
        if cached:
            return cached
        return self._mock_ltp(symbol)

    def get_india_vix(self) -> float:
        return self.get_ltp("INDIAVIX") or 13.5

    # ═══════════════════════════════════════════════════════
    #  PUBLIC API: get_option_chain()
    # ═══════════════════════════════════════════════════════

    def get_option_chain(self, symbol: str = "NIFTY", expiry: str = "") -> pd.DataFrame:
        """Get option chain. NSE India → Mock fallback."""
        key = f"{symbol}_{expiry}"
        ts = self._oc_ts.get(key)
        if ts and (datetime.now() - ts).total_seconds() < 5:
            return self._oc_cache.get(key, pd.DataFrame())

        # NSE India scraping (rate-limited to avoid blocks)
        if REQUESTS_OK:
            now = time.time()
            if now - self._nse_last_call >= self._nse_min_interval:
                try:
                    df = self._fetch_nse_option_chain(symbol)
                    if not df.empty:
                        self._oc_cache[key] = df
                        self._oc_ts[key] = datetime.now()
                        self._nse_last_call = now
                        return df
                except Exception as e:
                    logger.debug("NSE OC failed: %s", e)
                    self._nse_last_call = now

        # Fallback: mock chain
        df = self._mock_option_chain(symbol)
        self._oc_cache[key] = df
        self._oc_ts[key] = datetime.now()
        return df

    def _fetch_nse_option_chain(self, symbol: str) -> pd.DataFrame:
        """Scrape live option chain from NSE India (free)."""
        if self._nse_session is None:
            self._nse_session = _requests.Session()
            self._nse_session.headers.update(NSE_HEADERS)
            self._nse_session.get("https://www.nseindia.com", timeout=10)

        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol.upper()}"
        resp = self._nse_session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        records = data.get("records", {}).get("data", [])
        if not records:
            return pd.DataFrame()

        rows = []
        for rec in records:
            ce = rec.get("CE", {})
            pe = rec.get("PE", {})
            rows.append({
                "strike": rec.get("strikePrice", 0),
                "expiry": rec.get("expiryDate", ""),
                "ce_oi": ce.get("openInterest", 0),
                "ce_chg_oi": ce.get("changeinOpenInterest", 0),
                "ce_volume": ce.get("totalTradedVolume", 0),
                "ce_ltp": ce.get("lastPrice", 0),
                "ce_iv": ce.get("impliedVolatility", 0),
                "ce_delta": 0, "ce_gamma": 0, "ce_theta": 0, "ce_vega": 0,
                "pe_oi": pe.get("openInterest", 0),
                "pe_chg_oi": pe.get("changeinOpenInterest", 0),
                "pe_volume": pe.get("totalTradedVolume", 0),
                "pe_ltp": pe.get("lastPrice", 0),
                "pe_iv": pe.get("impliedVolatility", 0),
                "pe_delta": 0, "pe_gamma": 0, "pe_theta": 0, "pe_vega": 0,
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            nearest = df["expiry"].iloc[0]
            df = df[df["expiry"] == nearest]
        return df

    # ═══════════════════════════════════════════════════════
    #  PUBLIC API: get_historical()
    # ═══════════════════════════════════════════════════════

    def get_historical(self, symbol: str = "NIFTY", period: str = "59d",
                       interval: str = "5m") -> pd.DataFrame:
        """Get historical OHLCV via yfinance (FREE).
        
        Note: yfinance limits intraday data to 60 days.
        This method auto-clamps period for intraday intervals.
        """
        # Auto-clamp period for intraday intervals (yfinance 60-day limit)
        intraday_intervals = ("1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h")
        if interval in intraday_intervals and period in ("3mo", "6mo", "1y", "2y", "5y", "max"):
            period = "59d"
        cache_key = f"{symbol}_{period}_{interval}"
        cached = self._hist_cache.get(cache_key)
        cached_ts = self._hist_cache.get(f"{cache_key}_ts")
        if cached is not None and cached_ts and (datetime.now() - cached_ts).total_seconds() < 300:
            return cached

        if not YFINANCE_OK:
            return self._mock_historical()

        try:
            ticker_sym = YFINANCE_TICKERS.get(symbol.upper(), f"{symbol}.NS")
            df = yf.Ticker(ticker_sym).history(period=period, interval=interval)
            if df.empty:
                return self._mock_historical()

            df.columns = [c.lower() for c in df.columns]
            for col in ["adj close", "dividends", "stock splits", "capital gains"]:
                df = df.drop(columns=[col], errors="ignore")
            df.index.name = "timestamp"

            self._hist_cache[cache_key] = df
            self._hist_cache[f"{cache_key}_ts"] = datetime.now()
            logger.info("Historical data: %s | %d candles | %s", symbol, len(df), interval)
            return df

        except Exception as e:
            logger.warning("yfinance historical failed: %s. Using mock.", e)
            return self._mock_historical()

    # ═══════════════════════════════════════════════════════
    #  MOCK DATA (when no API available)
    # ═══════════════════════════════════════════════════════

    def _mock_ltp(self, symbol: str) -> float:
        base_prices = {
            "NIFTY": 22500, "13": 22500,
            "BANKNIFTY": 48000, "25": 48000,
            "FINNIFTY": 22000, "INDIAVIX": 13.5,
        }
        base = self._prices.get(symbol)
        if base is None:
            for key, val in base_prices.items():
                if key in str(symbol).upper():
                    base = val
                    break
            else:
                base = 22500

        target = base_prices.get(symbol.upper(), 22500)
        drift = (target - base) * 0.001
        noise = random.gauss(0, 8)
        new_price = round(base + drift + noise, 2)
        self._prices[symbol] = new_price
        return new_price

    def _mock_option_chain(self, symbol: str) -> pd.DataFrame:
        """Black-Scholes-consistent mock option chain."""
        bp = self.get_ltp(symbol) or 22500
        si = config.INDICES.get(symbol.upper(), {}).get("strike_interval", 50)

        atm = round(bp / si) * si
        strikes = [atm + i * si for i in range(-10, 11)]

        rows = []
        dte = config.DEFAULT_DAYS_TO_EXPIRY
        t = max(dte / 365, 0.001)
        r = config.RISK_FREE_RATE
        base_iv = 0.15 + random.uniform(-0.02, 0.02)

        for s in strikes:
            moneyness = (s - bp) / bp
            iv = base_iv + abs(moneyness) * 0.3 + random.uniform(-0.01, 0.01)
            iv = max(0.08, iv)

            d1_num = math.log(bp / s) + (r + 0.5 * iv**2) * t
            d1_den = iv * math.sqrt(t)
            d1 = d1_num / d1_den if d1_den > 0 else 0

            def _norm_cdf(x):
                x = max(-6, min(6, x))
                return 1 / (1 + math.exp(-1.7 * x - 0.73 * x**3))

            nd1, nd1_neg = _norm_cdf(d1), _norm_cdf(-d1)
            d2 = d1 - iv * math.sqrt(t)
            nd2, nd2_neg = _norm_cdf(d2), _norm_cdf(-d2)

            ce_p = max(config.MIN_OPTION_PREMIUM,
                       bp * nd1 - s * math.exp(-r * t) * nd2)
            pe_p = max(config.MIN_OPTION_PREMIUM,
                       s * math.exp(-r * t) * nd2_neg - bp * nd1_neg)

            ce_p = max(config.MIN_OPTION_PREMIUM,
                       round(ce_p * (1 + random.uniform(-0.03, 0.03)), 2))
            pe_p = max(config.MIN_OPTION_PREMIUM,
                       round(pe_p * (1 + random.uniform(-0.03, 0.03)), 2))

            atm_prox = max(0.1, 1 - abs(moneyness) * 5)
            ce_oi = int(random.uniform(50000, 500000) * atm_prox)
            pe_oi = int(random.uniform(50000, 500000) * atm_prox)

            rows.append({
                "strike": s, "expiry": "",
                "ce_oi": ce_oi,
                "ce_chg_oi": int(random.gauss(0, ce_oi * 0.05)),
                "ce_volume": int(ce_oi * random.uniform(0.05, 0.3)),
                "ce_ltp": ce_p,
                "ce_iv": round(iv * 100, 2),
                "ce_delta": round(max(0.01, min(0.99, nd1)), 4),
                "ce_gamma": round(max(0.0001, 0.01 * atm_prox), 5),
                "ce_theta": round(-ce_p * 0.05 * (1 + 2 * atm_prox), 2),
                "ce_vega": round(bp * math.sqrt(t) * 0.01 * atm_prox, 2),
                "pe_oi": pe_oi,
                "pe_chg_oi": int(random.gauss(0, pe_oi * 0.05)),
                "pe_volume": int(pe_oi * random.uniform(0.05, 0.3)),
                "pe_ltp": pe_p,
                "pe_iv": round(iv * 100, 2),
                "pe_delta": round(max(0.01, min(0.99, nd1)) - 1, 4),
                "pe_gamma": round(max(0.0001, 0.01 * atm_prox), 5),
                "pe_theta": round(-pe_p * 0.05 * (1 + 2 * atm_prox), 2),
                "pe_vega": round(bp * math.sqrt(t) * 0.01 * atm_prox, 2),
            })
        return pd.DataFrame(rows)

    def _mock_historical(self) -> pd.DataFrame:
        """Realistic mock OHLCV with market structure."""
        dates = pd.date_range(end=datetime.now(), periods=5000, freq="5min")
        market_dates = dates[
            (dates.time >= datetime.strptime("09:15", "%H:%M").time()) &
            (dates.time <= datetime.strptime("15:30", "%H:%M").time()) &
            (dates.weekday < 5)
        ]
        if len(market_dates) < 100:
            market_dates = dates[-2000:]

        price = 22500.0
        rows = []
        regime = "UP"
        regime_counter = 0
        regime_len = random.randint(100, 300)
        strength = random.uniform(0.3, 1.5)

        for i, ts in enumerate(market_dates):
            regime_counter += 1
            if regime_counter >= regime_len:
                regime = random.choice(["UP", "DOWN", "SIDE", "SIDE", "VOL"])
                regime_len = random.randint(80, 250)
                regime_counter = 0
                strength = random.uniform(0.3, 1.5)

            drift = {"UP": 0.0003, "DOWN": -0.0003, "VOL": 0, "SIDE": 0}.get(regime, 0) * strength
            vol = {"UP": 0.0008, "DOWN": 0.001, "VOL": 0.002, "SIDE": 0.0005}.get(regime, 0.0005)
            if regime in ("VOL", "SIDE"):
                drift = random.uniform(-0.0002, 0.0002) if regime == "VOL" else 0

            ret = random.gauss(drift, vol) + (22500 - price) / 22500 * 0.0001
            o = price
            c = price * (1 + ret)
            bv = abs(random.gauss(0, vol * 1.5))
            h = max(o, c) + abs(random.gauss(0, price * bv))
            l = min(o, c) - abs(random.gauss(0, price * bv))
            h, l = max(h, o, c), min(l, o, c)
            l = max(l, price * 0.95)

            hour = ts.hour
            base_v = {9: 300000, 15: 200000, 10: 120000, 14: 120000}.get(hour, 70000)
            v = max(5000, int(random.gauss(base_v, base_v * 0.3)))

            rows.append({"open": round(o, 2), "high": round(h, 2),
                         "low": round(l, 2), "close": round(c, 2), "volume": v})
            price = c

        df = pd.DataFrame(rows, index=market_dates[:len(rows)])
        df.index.name = "timestamp"
        return df

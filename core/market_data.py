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
from datetime import datetime, timedelta, timezone
from typing import Optional
from collections import defaultdict

import pandas as pd
import numpy as np

import config

# IST timezone (UTC+5:30) — critical for market hours detection
IST = timezone(timedelta(hours=5, minutes=30))

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
except Exception as _e:
    SMARTAPI_OK = False
    logger.warning("SmartAPI import failed: %s", _e)

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
        self._smart_api = None  # Store SmartAPI session for REST calls
        self._instrument_df = None  # Instrument master for option token lookup
        self._data_source_log = "NONE"  # Track where data actually came from
        # Previous day's closing OI per strike — loaded from DB at startup
        # Key format: "{strike}_{ce|pe}" -> OI value
        self._prev_day_close_oi = {}  # Previous trading day's final OI
        self._prev_day_oi_loaded = False  # Whether prev day OI was loaded
        self._day_open_oi = {}  # Fallback: first-seen OI if no DB data
        self._day_open_date = None  # Reset tracking on new day
        # Futures LTP + OI
        self._futures_token = None  # Angel One token for NIFTY FUT
        self._futures_ltp = 0.0
        self._futures_oi = 0  # Futures OI for 'Total OI' column (StockMojo)
        # Store credentials for re-authentication
        self._angel_credentials = {}
        self._last_reauth_attempt = 0  # Prevent rapid re-auth loops

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

                # Store SmartAPI object for REST calls (option chain)
                self._smart_api = smart_api

                # Store credentials for re-authentication on token expiry
                self._angel_credentials = {
                    "api_key": api_key,
                    "client_id": client_id,
                    "password": password,
                    "totp_secret": totp_secret,
                }

                # Download instrument master for option token lookups
                self._load_instrument_master()

                # Find NIFTY FUT token for futures LTP
                self._find_futures_token()

                # Start WebSocket in background thread
                self._start_websocket(jwt_token, api_key, client_id, feed_token)
                self._tier = "WEBSOCKET"
                logger.info("[OK] TIER 1 ACTIVE: Angel One WebSocket (real-time)")
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
                            # Check if it's the futures token
                            if self._futures_token and token == self._futures_token:
                                with self._lock:
                                    self._futures_ltp = ltp
                    except Exception as e:
                        logger.debug("WS tick error: %s", e)

                def on_open(wsapp):
                    logger.info("WebSocket connected - subscribing to instruments...")
                    # Subscribe to NIFTY and BANKNIFTY LTP (mode 1 = LTP only)
                    sub_tokens = [
                        {"exchangeType": 1, "tokens": [SMARTAPI_TOKENS["NIFTY"]]},
                        {"exchangeType": 1, "tokens": [SMARTAPI_TOKENS["BANKNIFTY"]]},
                    ]
                    # Also subscribe to NIFTY FUT if token found
                    if self._futures_token:
                        sub_tokens.append({"exchangeType": 2, "tokens": [self._futures_token]})
                        logger.info("Subscribing to NIFTY FUT token: %s", self._futures_token)
                    sws.subscribe("nifty_bn", 1, sub_tokens)
                    self._ws_connected = True

                def on_error(wsapp, error):
                    logger.error("WebSocket error: %s", error)
                    self._ws_connected = False

                def on_close(wsapp):
                    logger.warning("WebSocket disconnected. Attempting re-auth in 5s...")
                    self._ws_connected = False
                    time.sleep(5)
                    # Re-authenticate with fresh JWT before reconnecting
                    try:
                        if self._reauth_smartapi():
                            feed_token = self._smart_api.getfeedToken()
                            new_jwt = self._smart_api._jwtToken if hasattr(self._smart_api, '_jwtToken') else jwt_token
                            creds = self._angel_credentials
                            new_sws = SmartWebSocketV2(new_jwt, creds["api_key"], creds["client_id"], feed_token)
                            new_sws.on_data = on_data
                            new_sws.on_open = on_open
                            new_sws.on_error = on_error
                            new_sws.on_close = on_close
                            self._ws = new_sws
                            new_sws.connect()
                        else:
                            logger.error("WebSocket re-auth failed, will retry on next data cycle")
                    except Exception as re_e:
                        logger.error("WebSocket reconnect failed: %s", re_e)

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

    def _load_instrument_master(self):
        """Download Angel One instrument master for option token lookups."""
        try:
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            resp = _requests.get(url, timeout=30)
            data = resp.json()
            self._instrument_df = pd.DataFrame(data)
            logger.info("[OK] Instrument master loaded: %d instruments", len(self._instrument_df))
        except Exception as e:
            logger.warning("Instrument master download failed: %s", e)
            self._instrument_df = None

    def _find_futures_token(self):
        """Find nearest-month NIFTY FUT token from instrument master."""
        if self._instrument_df is None or self._instrument_df.empty:
            return
        try:
            df = self._instrument_df
            fut = df[(df["exch_seg"] == "NFO") & (df["name"] == "NIFTY") &
                     (df["instrumenttype"] == "FUTIDX")].copy()
            if fut.empty:
                logger.warning("No NIFTY FUT contracts found in instrument master")
                return
            fut["expiry_dt"] = pd.to_datetime(fut["expiry"], format="%d%b%Y", errors="coerce")
            future_only = fut[fut["expiry_dt"] >= datetime.now(IST).replace(tzinfo=None)]
            if future_only.empty:
                return
            nearest = future_only.loc[future_only["expiry_dt"].idxmin()]
            self._futures_token = str(nearest["token"])
            logger.info("[OK] NIFTY FUT token: %s (expiry: %s)",
                        self._futures_token, nearest["expiry"])
        except Exception as e:
            logger.warning("Failed to find NIFTY FUT token: %s", e)

    def get_futures_ltp(self, symbol: str = "NIFTY") -> float:
        """
        Get NIFTY Futures LTP.
        Priority: WebSocket cache -> REST API call -> spot price fallback.
        Also captures Futures OI for the 'Total OI' column (matches StockMojo).
        """
        # Check WebSocket cache first
        with self._lock:
            if self._futures_ltp > 0:
                return self._futures_ltp

        # Try REST API call — use FULL mode to get OI as well
        if self._smart_api and self._futures_token:
            try:
                result = self._smart_api.getMarketData(
                    mode="FULL",
                    exchangeTokens={"NFO": [self._futures_token]}
                )
                if result and result.get("data") and result["data"].get("fetched"):
                    item = result["data"]["fetched"][0]
                    ltp = float(item.get("ltp", 0))
                    oi = int(item.get("opnInterest", 0))
                    if ltp > 0:
                        with self._lock:
                            self._futures_ltp = ltp
                            if oi > 0:
                                self._futures_oi = oi
                        return ltp
                else:
                    # Check for token expiry — auto re-authenticate
                    if result and isinstance(result, dict):
                        msg = str(result.get("message", "")).lower()
                        err_code = str(result.get("errorCode", ""))
                        if "invalid token" in msg or err_code == "AG8001":
                            logger.warning("[TOKEN EXPIRED] Futures LTP — attempting re-auth")
                            if self._reauth_smartapi():
                                retry = self._smart_api.getMarketData(
                                    mode="FULL",
                                    exchangeTokens={"NFO": [self._futures_token]}
                                )
                                if retry and retry.get("data") and retry["data"].get("fetched"):
                                    item = retry["data"]["fetched"][0]
                                    ltp = float(item.get("ltp", 0))
                                    oi = int(item.get("opnInterest", 0))
                                    if ltp > 0:
                                        with self._lock:
                                            self._futures_ltp = ltp
                                            if oi > 0:
                                                self._futures_oi = oi
                                        return ltp
            except Exception as e:
                logger.debug("Futures LTP REST failed: %s", e)

        # Fallback to spot
        return self.get_ltp(symbol) or 0

    def get_futures_oi(self, symbol: str = "NIFTY") -> int:
        """Get NIFTY Futures OI (for 'Total OI' column matching StockMojo)."""
        with self._lock:
            return self._futures_oi

    def load_prev_day_oi_from_db(self, db_url: str):
        """
        Load previous trading day's closing OI from database.
        This is critical for accurate 'Chg Day' computation (matching StockMojo/NSE).
        """
        if not db_url:
            logger.warning("No DB URL — cannot load prev day OI, will use first-fetch baseline")
            return

        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()

            # Find the most recent trading day before today
            today = datetime.now(IST).strftime("%Y-%m-%d")
            cur.execute("""
                SELECT DISTINCT DATE(timestamp AT TIME ZONE 'Asia/Kolkata') as dt
                FROM oi_snapshots
                WHERE symbol = 'NIFTY'
                  AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') < %s
                ORDER BY dt DESC
                LIMIT 1
            """, (today,))
            row = cur.fetchone()
            if not row:
                logger.warning("No previous day OI data in DB — will use first-fetch baseline")
                cur.close()
                conn.close()
                return

            prev_date = row[0].strftime("%Y-%m-%d")

            # Get the LAST snapshot from that day (closing OI)
            cur.execute("""
                SELECT timestamp FROM oi_snapshots
                WHERE symbol = 'NIFTY'
                  AND DATE(timestamp AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY timestamp DESC
                LIMIT 1
            """, (prev_date,))
            last_ts_row = cur.fetchone()
            if not last_ts_row:
                cur.close()
                conn.close()
                return

            last_ts = last_ts_row[0]

            # Load all strike OI from that last timestamp
            cur.execute("""
                SELECT strike, ce_oi, pe_oi
                FROM oi_snapshots
                WHERE symbol = 'NIFTY'
                  AND timestamp = %s
            """, (last_ts,))

            count = 0
            for strike_row in cur.fetchall():
                strike = float(strike_row[0])
                ce_oi = int(strike_row[1] or 0)
                pe_oi = int(strike_row[2] or 0)
                self._prev_day_close_oi[f"{strike}_ce"] = ce_oi
                self._prev_day_close_oi[f"{strike}_pe"] = pe_oi
                count += 1

            self._prev_day_oi_loaded = True
            cur.close()
            conn.close()
            logger.info("[OK] Loaded prev day closing OI: %d strikes from %s", count, prev_date)

        except Exception as e:
            logger.error("Failed to load prev day OI: %s", e)
            self._prev_day_oi_loaded = False

    def _reauth_smartapi(self) -> bool:
        """
        Re-authenticate with Angel One when JWT token expires.
        Returns True on success, False on failure.
        """
        # Prevent rapid re-auth loops (max once per 60 seconds)
        now = time.time()
        if now - self._last_reauth_attempt < 60:
            return False
        self._last_reauth_attempt = now

        creds = self._angel_credentials
        if not creds:
            logger.error("Cannot re-auth: no stored credentials")
            return False

        try:
            logger.info("[REAUTH] Angel One token expired — re-authenticating...")
            smart_api = SmartConnect(api_key=creds["api_key"])
            totp = pyotp.TOTP(creds["totp_secret"]).now()
            session_data = smart_api.generateSession(
                creds["client_id"], creds["password"], totp
            )
            if session_data.get("status"):
                self._smart_api = smart_api
                logger.info("[REAUTH] Success — Angel One session restored")
                return True
            else:
                logger.error("[REAUTH] Failed: %s", session_data.get("message", "unknown"))
                return False
        except Exception as e:
            logger.error("[REAUTH] Exception: %s", e)
            return False

    def _get_option_tokens(self, symbol: str = "NIFTY", underlying_price: float = 0):
        """Get Angel One tokens for option contracts near ATM."""
        if self._instrument_df is None or self._instrument_df.empty:
            return []

        df = self._instrument_df

        # Filter for NIFTY options in NFO segment
        nfo = df[(df["exch_seg"] == "NFO") & (df["name"] == symbol.upper())]
        if nfo.empty:
            return []

        # Filter option type (CE/PE), exclude futures
        opts = nfo[nfo["instrumenttype"].isin(["OPTIDX"])].copy()
        if opts.empty:
            return []

        # Parse strike and get nearest expiry
        opts["strike_num"] = pd.to_numeric(opts["strike"], errors="coerce") / 100
        opts = opts.dropna(subset=["strike_num"])

        # Get nearest expiry
        opts["expiry_dt"] = pd.to_datetime(opts["expiry"], format="%d%b%Y", errors="coerce")
        future_expiries = opts[opts["expiry_dt"] >= datetime.now(IST).replace(tzinfo=None)]
        if future_expiries.empty:
            return []

        nearest_expiry = future_expiries["expiry_dt"].min()
        current = opts[opts["expiry_dt"] == nearest_expiry]

        # Filter strikes near ATM — use wide range (±1000 points)
        # to ensure enough data for any user-selected range filter (up to ±20)
        si = config.INDICES.get(symbol, {}).get("strike_interval", 50)
        if underlying_price > 0:
            # Use futures LTP for ATM if available (matches StockMojo)
            fut_ltp = self._futures_ltp if self._futures_ltp > 0 else underlying_price
            atm = round(fut_ltp / si) * si
            current = current[abs(current["strike_num"] - atm) <= si * 20]

        return current

    def _fetch_angel_option_chain(self, symbol: str = "NIFTY") -> pd.DataFrame:
        """
        Fetch live option chain using Angel One SmartAPI getMarketData(mode='FULL').
        FULL mode returns OI (opnInterest), volume, LTP, OHLC per instrument.
        Batches up to 50 tokens per call for efficiency.
        """
        if not self._smart_api or self._instrument_df is None:
            return pd.DataFrame()

        underlying = self.get_ltp(symbol) or 0
        if underlying <= 0:
            return pd.DataFrame()

        # Use futures LTP for ATM calculation (matches StockMojo)
        fut_ltp = self._futures_ltp if self._futures_ltp > 0 else underlying
        if fut_ltp <= 0:
            return pd.DataFrame()

        option_contracts = self._get_option_tokens(symbol, fut_ltp)
        if option_contracts is None or len(option_contracts) == 0:
            return pd.DataFrame()

        si = config.INDICES.get(symbol, {}).get("strike_interval", 50)
        atm = round(fut_ltp / si) * si

        # Build token list and mapping: token -> (strike, CE/PE)
        token_map = {}
        all_tokens = []
        for _, contract in option_contracts.iterrows():
            strike = float(contract["strike_num"])
            token = str(contract["token"])
            sym = str(contract["symbol"])
            opt_type = "CE" if "CE" in sym else "PE"
            token_map[token] = {"strike": strike, "type": opt_type, "symbol": sym,
                                "expiry": str(contract.get("expiry", ""))}
            all_tokens.append(token)

        # Batch fetch: getMarketData supports up to 50 tokens per call
        strikes_data = {}
        batch_size = 50

        for i in range(0, len(all_tokens), batch_size):
            batch = all_tokens[i:i + batch_size]
            try:
                result = self._smart_api.getMarketData(
                    mode="FULL",
                    exchangeTokens={"NFO": batch}
                )

                if not result or not result.get("data") or not result["data"].get("fetched"):
                    # Check for token expiry — auto re-authenticate
                    if result and isinstance(result, dict):
                        msg = str(result.get("message", "")).lower()
                        err_code = str(result.get("errorCode", ""))
                        if "invalid token" in msg or err_code == "AG8001":
                            logger.warning("[TOKEN EXPIRED] Detected Invalid Token — attempting re-auth")
                            if self._reauth_smartapi():
                                # Retry this batch after re-auth
                                try:
                                    result = self._smart_api.getMarketData(
                                        mode="FULL",
                                        exchangeTokens={"NFO": batch}
                                    )
                                    if result and result.get("data") and result["data"].get("fetched"):
                                        # Success! Process below
                                        pass
                                    else:
                                        logger.warning("getMarketData still failed after re-auth")
                                        continue
                                except Exception as re_e:
                                    logger.error("Retry after re-auth failed: %s", re_e)
                                    continue
                            else:
                                continue
                        else:
                            logger.warning("getMarketData returned no data for batch %d", i)
                            continue
                    else:
                        logger.warning("getMarketData returned no data for batch %d", i)
                        continue

                for item in result["data"]["fetched"]:
                    token = str(item.get("symbolToken", ""))
                    if token not in token_map:
                        continue

                    info = token_map[token]
                    strike = info["strike"]
                    opt_type = info["type"]
                    prefix = "ce" if opt_type == "CE" else "pe"

                    if strike not in strikes_data:
                        strikes_data[strike] = {
                            "strike": strike,
                            "expiry": info["expiry"],
                        }

                    strikes_data[strike][f"{prefix}_ltp"] = float(item.get("ltp", 0))
                    current_oi = int(item.get("opnInterest", 0))
                    strikes_data[strike][f"{prefix}_oi"] = current_oi
                    strikes_data[strike][f"{prefix}_volume"] = int(item.get("tradeVolume", 0))

                    # OI change: compute vs PREVIOUS DAY's closing OI (matches NSE/StockMojo)
                    oi_key = f"{strike}_{prefix}"

                    if self._prev_day_oi_loaded and oi_key in self._prev_day_close_oi:
                        # Best: use actual previous day close from DB
                        prev_close_oi = self._prev_day_close_oi[oi_key]
                    else:
                        # Fallback: use first-seen OI of the day
                        today = datetime.now(IST).date()
                        if self._day_open_date != today:
                            self._day_open_oi = {}
                            self._day_open_date = today
                        if oi_key not in self._day_open_oi:
                            self._day_open_oi[oi_key] = current_oi
                        prev_close_oi = self._day_open_oi[oi_key]

                    strikes_data[strike][f"{prefix}_chg_oi"] = current_oi - prev_close_oi
                    strikes_data[strike][f"{prefix}_iv"] = 0  # IV not in this endpoint

            except Exception as e:
                logger.warning("getMarketData batch %d failed: %s", i, e)
                time.sleep(0.5)
                continue

            time.sleep(0.1)  # Small delay between batches

        if not strikes_data:
            return pd.DataFrame()

        # Compute days to expiry for IV/Greeks calculation
        dte_years = 7 / 365  # Default: 7 days
        try:
            # Parse expiry from the first strike's data
            sample_expiry = list(strikes_data.values())[0].get("expiry", "")
            if sample_expiry:
                from datetime import datetime as _dt
                exp_dt = _dt.strptime(sample_expiry, "%d%b%Y")
                now_dt = datetime.now(IST).replace(tzinfo=None)
                days_left = max((exp_dt - now_dt).days, 1)
                dte_years = days_left / 365
        except Exception:
            pass  # Use default 7 days

        # Build DataFrame with IV and Greeks
        from core.black_scholes import compute_iv_and_greeks

        RISK_FREE_RATE = 0.07  # India 10Y government bond ~7%

        rows = []
        for strike, data in sorted(strikes_data.items()):
            ce_ltp = data.get("ce_ltp", 0)
            pe_ltp = data.get("pe_ltp", 0)
            strike_val = data["strike"]

            # Compute IV + Greeks for CE
            if ce_ltp > 0 and underlying > 0:
                ce_greeks = compute_iv_and_greeks(underlying, strike_val, dte_years, RISK_FREE_RATE, ce_ltp, "CE")
            else:
                ce_greeks = {"iv": 0, "delta": 0, "gamma": 0, "theta": 0, "vega": 0}

            # Compute IV + Greeks for PE
            if pe_ltp > 0 and underlying > 0:
                pe_greeks = compute_iv_and_greeks(underlying, strike_val, dte_years, RISK_FREE_RATE, pe_ltp, "PE")
            else:
                pe_greeks = {"iv": 0, "delta": 0, "gamma": 0, "theta": 0, "vega": 0}

            rows.append({
                "strike": strike_val,
                "expiry": data.get("expiry", ""),
                "ce_oi": data.get("ce_oi", 0),
                "ce_chg_oi": data.get("ce_chg_oi", 0),
                "ce_volume": data.get("ce_volume", 0),
                "ce_ltp": ce_ltp,
                "ce_iv": ce_greeks["iv"],
                "ce_delta": ce_greeks["delta"],
                "ce_gamma": ce_greeks["gamma"],
                "ce_theta": ce_greeks["theta"],
                "ce_vega": ce_greeks["vega"],
                "pe_oi": data.get("pe_oi", 0),
                "pe_chg_oi": data.get("pe_chg_oi", 0),
                "pe_volume": data.get("pe_volume", 0),
                "pe_ltp": pe_ltp,
                "pe_iv": pe_greeks["iv"],
                "pe_delta": pe_greeks["delta"],
                "pe_gamma": pe_greeks["gamma"],
                "pe_theta": pe_greeks["theta"],
                "pe_vega": pe_greeks["vega"],
            })

        df = pd.DataFrame(rows)
        self._data_source_log = "ANGEL_ONE_API"
        logger.info("[LIVE] Option chain from Angel One: %d strikes, IV+Greeks computed", len(df))
        return df

    # ═══════════════════════════════════════════════════════
    #  TIER 2: yfinance (historical + fallback)
    # ═══════════════════════════════════════════════════════

    def _init_tier2_yfinance(self):
        """Test yfinance connection."""
        if not YFINANCE_OK:
            logger.warning("yfinance not installed - using mock mode.")
            return

        try:
            ticker = yf.Ticker("^NSEI")
            hist = ticker.history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
                self._prices["NIFTY"] = price
                self._price_timestamps["NIFTY"] = time.time()
                self._tier = "YFINANCE"
                logger.info("[OK] TIER 2 ACTIVE: yfinance (NIFTY: %.2f)", price)
                logger.warning("[WARN] yfinance has ~15min delay. For real-time, add Angel One credentials.")
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
        """
        Get option chain — Priority:
        1. Angel One SmartAPI (real, live)
        2. NSE India scraping (real, may be geo-blocked)
        3. Mock (LAST RESORT — development only)
        """
        key = f"{symbol}_{expiry}"
        ts = self._oc_ts.get(key)
        if ts and (datetime.now(IST) - ts).total_seconds() < 30:
            return self._oc_cache.get(key, pd.DataFrame())

        # PRIORITY 1: Angel One SmartAPI (works everywhere)
        if self._smart_api:
            try:
                df = self._fetch_angel_option_chain(symbol)
                if not df.empty:
                    self._oc_cache[key] = df
                    self._oc_ts[key] = datetime.now(IST)
                    self._data_source_log = "ANGEL_ONE_API"
                    return df
            except Exception as e:
                logger.warning("Angel One OC failed: %s", e)

        # PRIORITY 2: NSE India scraping (works from India IPs)
        if REQUESTS_OK:
            now = time.time()
            if now - self._nse_last_call >= self._nse_min_interval:
                try:
                    df = self._fetch_nse_option_chain(symbol)
                    if not df.empty:
                        self._oc_cache[key] = df
                        self._oc_ts[key] = datetime.now(IST)
                        self._nse_last_call = now
                        self._data_source_log = "NSE_INDIA"
                        return df
                except Exception as e:
                    logger.debug("NSE OC failed: %s", e)
                    self._nse_last_call = now

        # FALLBACK: mock chain (DEV ONLY — logged as warning)
        logger.warning("[MOCK] Using synthetic data — no live source available!")
        df = self._mock_option_chain(symbol)
        self._oc_cache[key] = df
        self._oc_ts[key] = datetime.now(IST)
        self._data_source_log = "MOCK"
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
    #  EXPIRY INFO (for dashboard badge)
    # ═══════════════════════════════════════════════════════

    def get_nearest_expiry_info(self, symbol: str = "NIFTY") -> dict:
        """
        Get nearest expiry date and days-to-expiry for the dashboard badge.
        Returns: {"expiry": "05 May", "dte": 5, "label": "5 May (5d)", "expiry_date": "05MAY2026"}
        """
        if self._instrument_df is None or self._instrument_df.empty:
            return {"expiry": "--", "dte": 0, "label": "--", "expiry_date": ""}

        try:
            df = self._instrument_df
            opts = df[(df["exch_seg"] == "NFO") & (df["name"] == symbol.upper()) &
                      (df["instrumenttype"] == "OPTIDX")].copy()
            if opts.empty:
                return {"expiry": "--", "dte": 0, "label": "--", "expiry_date": ""}

            opts["expiry_dt"] = pd.to_datetime(opts["expiry"], format="%d%b%Y", errors="coerce")
            future_expiries = opts[opts["expiry_dt"] >= datetime.now(IST).replace(tzinfo=None)]
            if future_expiries.empty:
                return {"expiry": "--", "dte": 0, "label": "--", "expiry_date": ""}

            nearest_dt = future_expiries["expiry_dt"].min()
            dte = max((nearest_dt - datetime.now(IST).replace(tzinfo=None)).days, 0)
            expiry_str = nearest_dt.strftime("%d %b")
            raw_expiry = nearest_dt.strftime("%d%b%Y").upper()
            label = f"{nearest_dt.strftime('%-d %b')} ({dte}d)" if hasattr(nearest_dt, 'strftime') else f"{expiry_str} ({dte}d)"

            return {
                "expiry": expiry_str,
                "dte": dte,
                "label": f"{expiry_str.strip()} ({dte}d)",
                "expiry_date": raw_expiry,
            }
        except Exception as e:
            logger.debug("Expiry info failed: %s", e)
            return {"expiry": "--", "dte": 0, "label": "--", "expiry_date": ""}

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
        dte = 7  # Weekly expiry
        t = max(dte / 365, 0.001)
        r = 0.065  # Risk-free rate
        base_iv = 0.15 + random.uniform(-0.02, 0.02)
        MIN_PREM = 0.50

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

            ce_p = max(MIN_PREM, bp * nd1 - s * math.exp(-r * t) * nd2)
            pe_p = max(MIN_PREM, s * math.exp(-r * t) * nd2_neg - bp * nd1_neg)

            ce_p = max(MIN_PREM, round(ce_p * (1 + random.uniform(-0.03, 0.03)), 2))
            pe_p = max(MIN_PREM, round(pe_p * (1 + random.uniform(-0.03, 0.03)), 2))

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

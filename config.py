"""
Global configuration — Live Option Chain Data.
Loads from .env and provides constants.
"""

import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# ─── Paths ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = BASE_DIR / "logs"

for d in [DATA_DIR, CACHE_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Real-Time Data: Angel One SmartAPI ──────────────────
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_ID = os.getenv("ANGEL_CLIENT_ID", "")
ANGEL_PASSWORD = os.getenv("ANGEL_PASSWORD", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

# ─── Database ────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ─── Market Constants ────────────────────────────────────
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 30

# ─── Supported Indices ───────────────────────────────────
INDICES = {
    "NIFTY": {
        "symbol": "NIFTY 50",
        "exchange_segment": "NSE_FNO",
        "lot_size": 25,
        "tick_size": 0.05,
        "strike_interval": 50,
    },
    "BANKNIFTY": {
        "symbol": "NIFTY BANK",
        "exchange_segment": "NSE_FNO",
        "lot_size": 15,
        "tick_size": 0.05,
        "strike_interval": 100,
    },
    "FINNIFTY": {
        "symbol": "NIFTY FIN SERVICE",
        "exchange_segment": "NSE_FNO",
        "lot_size": 25,
        "tick_size": 0.05,
        "strike_interval": 50,
    },
    "MIDCPNIFTY": {
        "symbol": "NIFTY MID SELECT",
        "exchange_segment": "NSE_FNO",
        "lot_size": 50,
        "tick_size": 0.05,
        "strike_interval": 25,
    },
}

# ─── Data Refresh Intervals (seconds) ───────────────────
OPTION_CHAIN_REFRESH = 60
MARKET_DATA_REFRESH = 5

# ─── Logging ─────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

"""
REST API routes for the trading dashboard.
All endpoints return JSON data consumed by the frontend.
"""

import logging
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse
import json
import numpy as np

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Trading API"])


# ── Numpy-safe JSON encoder ──────────────────────────────
class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types."""
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _sanitize(obj):
    """Recursively convert numpy types to Python native types for JSON."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, (np.bool_,)):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


# These will be injected by run.py at startup
_engine_state = {
    "market_data": None,
    "option_chain_analyzer": None,
    "technical": None,
    "oi_analyzer": None,
    "iv_analyzer": None,
    "sentiment": None,
    "signal_aggregator": None,
    "strategy_selector": None,
    "paper_engine": None,
    "backtest_engine": None,
    "backtest_loader": None,
    "greeks_calc": None,
    "risk_manager": None,
    "order_manager": None,
    "portfolio_tracker": None,
    # Advanced
    "adaptive_weights": None,
    "mtf_engine": None,
    "global_corr": None,
    "feedback_loop": None,
    "uoa_detector": None,
    "order_flow": None,
    "vix_analyzer": None,
    "dynamic_hedger": None,
    "walk_forward": None,
}


def inject_engines(state: dict):
    _engine_state.update(state)


@router.get("/dashboard")
async def get_dashboard():
    """Main dashboard data."""
    pe = _engine_state["paper_engine"]
    if not pe:
        return {"status": "NOT_INITIALIZED"}

    portfolio = pe.get_portfolio_summary()
    market_open = _is_market_open()

    return _sanitize({
        "market_status": "OPEN" if market_open else "CLOSED",
        "timestamp": datetime.now().isoformat(),
        "portfolio": portfolio,
        "total_signals_generated": len(
            _engine_state["signal_aggregator"].history
        ) if _engine_state["signal_aggregator"] else 0,
    })


@router.get("/portfolio")
async def get_portfolio():
    """Current portfolio and positions."""
    pe = _engine_state["paper_engine"]
    if not pe:
        return {"error": "Engine not initialized"}
    return _sanitize(pe.get_portfolio_summary())


@router.get("/trades")
async def get_trades():
    """All closed trades."""
    pe = _engine_state["paper_engine"]
    if not pe:
        return {"trades": []}
    return _sanitize({"trades": pe.closed_trades[-100:]})


@router.get("/signals")
async def get_signals():
    """Recent signals."""
    sa = _engine_state["signal_aggregator"]
    if not sa:
        return {"signals": []}
    signals = []
    for s in sa.history[-50:]:
        signals.append({
            "timestamp": s.timestamp,
            "symbol": s.symbol,
            "direction": s.direction,
            "score": s.score,
            "confidence": s.confidence,
            "regime": s.regime,
            "technical_score": s.technical_score,
            "greeks_score": s.greeks_score,
            "oi_score": s.oi_score,
            "sentiment_score": s.sentiment_score,
            "suggested_strategy": s.suggested_strategy,
            "is_actionable": bool(s.is_actionable),
        })
    return {"signals": list(reversed(signals))}


@router.get("/option-chain")
async def get_option_chain():
    """Live option chain data."""
    oca = _engine_state["option_chain_analyzer"]
    md = _engine_state["market_data"]
    if not oca or not md:
        return {"chain": [], "analysis": {}}

    chain_df = md.get_option_chain("NIFTY")
    underlying = md.get_ltp("NIFTY") or 22500

    analysis = oca.analyze(chain_df, underlying)
    chain_data = chain_df.to_dict("records") if not chain_df.empty else []

    return _sanitize({
        "chain": chain_data[:40],
        "analysis": oca.get_trend_summary(),
        "underlying_price": underlying,
        "is_live": _is_market_open(),
        "data_freshness": "LIVE" if _is_market_open() else "LAST_CLOSE",
    })


@router.get("/greeks")
async def get_greeks():
    """Greeks analysis for current positions."""
    pe = _engine_state["paper_engine"]
    gc = _engine_state["greeks_calc"]
    if not pe or not gc:
        return {"portfolio_greeks": {}, "positions": []}

    return _sanitize({
        "portfolio_greeks": {"delta": 0, "gamma": 0, "theta": 0, "vega": 0},
        "positions": [p.to_dict() for p in pe.positions],
    })


@router.get("/sentiment")
async def get_sentiment():
    """Current sentiment analysis."""
    se = _engine_state["sentiment"]
    if not se:
        return {"aggregate_score": 0, "label": "NEUTRAL", "sources": []}
    result = se.analyze_all("NIFTY")
    momentum = se.get_momentum()
    result["momentum"] = momentum
    return _sanitize(result)


@router.get("/oi-analysis")
async def get_oi_analysis():
    """Open Interest analysis."""
    oia = _engine_state["oi_analyzer"]
    md = _engine_state["market_data"]
    if not oia or not md:
        return {"status": "NOT_AVAILABLE"}

    chain_df = md.get_option_chain("NIFTY")
    underlying = md.get_ltp("NIFTY") or 22500
    return _sanitize(oia.analyze(chain_df, underlying))


@router.get("/oi-buildup")
async def get_oi_buildup():
    """Short Covering / Long Unwinding / Buildup detection."""
    oia = _engine_state["oi_analyzer"]
    if not oia or not oia.history:
        return {
            "market_buildup": "NO_DATA",
            "short_covering": {"active": False},
            "long_unwinding": {"active": False},
        }
    latest = oia.history[-1]
    return _sanitize({
        "timestamp": latest.get("timestamp"),
        "underlying": latest.get("underlying"),
        "price_direction": latest.get("price_direction", "FLAT"),
        "price_change": latest.get("price_change", 0),
        "market_buildup": latest.get("market_buildup", "MIXED"),
        "buildup_strength": latest.get("buildup_strength", 0),
        "short_covering": latest.get("short_covering", {}),
        "long_unwinding": latest.get("long_unwinding", {}),
        "long_buildup": latest.get("long_buildup", {}),
        "short_buildup": latest.get("short_buildup", {}),
        "pattern_summary": latest.get("pattern_summary", {}),
        "total_ce_oi_change": latest.get("total_ce_oi_change", 0),
        "total_pe_oi_change": latest.get("total_pe_oi_change", 0),
        "oi_signal": latest.get("oi_signal", "NEUTRAL"),
    })


@router.get("/iv-analysis")
async def get_iv_analysis():
    """IV analysis."""
    iva = _engine_state["iv_analyzer"]
    md = _engine_state["market_data"]
    if not iva or not md:
        return {"status": "NOT_AVAILABLE"}

    chain_df = md.get_option_chain("NIFTY")
    underlying = md.get_ltp("NIFTY") or 22500
    return _sanitize(iva.analyze(chain_df, underlying))


@router.post("/backtest")
async def run_backtest():
    """Run a backtest on historical data."""
    be = _engine_state["backtest_engine"]
    loader = _engine_state["backtest_loader"]
    md = _engine_state["market_data"]
    if not be:
        return {"error": "Not initialized"}

    if loader:
        df = loader.load("NIFTY", days=59, source="auto")
    elif md:
        df = md.get_historical("NIFTY", period="59d", interval="5m")
        if df.empty:
            df = md._mock_historical()
    else:
        return {"error": "No data source available"}

    result = be.run(df, "MOMENTUM", "NIFTY")

    try:
        from backtest.report import BacktestReport
        result = BacktestReport.generate(result)
    except Exception:
        pass

    return _sanitize(result)


@router.get("/risk")
async def get_risk():
    """Portfolio risk assessment."""
    rm = _engine_state["risk_manager"]
    pe = _engine_state["paper_engine"]
    if not rm or not pe:
        return {"risk_level": "UNKNOWN", "warnings": []}
    return _sanitize(rm.assess_portfolio_risk(pe.positions))


@router.get("/orders")
async def get_orders():
    """Order history and execution stats."""
    om = _engine_state["order_manager"]
    if not om:
        return {"orders": [], "stats": {}}
    return _sanitize({
        "orders": om.get_order_history(50),
        "active": om.get_active_orders(),
        "stats": om.get_stats(),
    })


@router.get("/performance")
async def get_performance():
    """Detailed performance analytics."""
    pt = _engine_state["portfolio_tracker"]
    pe = _engine_state["paper_engine"]
    if not pt or not pe:
        return {"error": "Not initialized"}
    return _sanitize(pt.get_performance_metrics(pe.closed_trades))


@router.get("/market-status")
async def market_status():
    """Current market status."""
    return {
        "is_open": _is_market_open(),
        "timestamp": datetime.now().isoformat(),
        "next_event": "Market Close 15:30" if _is_market_open() else "Market Open 09:15",
    }


def _is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0)
    market_close = now.replace(hour=15, minute=30, second=0)
    return market_open <= now <= market_close


# ═══════════════════════════════════════════════════════════
#  ADVANCED MODULE ENDPOINTS
# ═══════════════════════════════════════════════════════════

@router.get("/adaptive-weights")
async def get_adaptive_weights():
    """Current adaptive signal weights and layer performance."""
    aw = _engine_state["adaptive_weights"]
    if not aw:
        return {"weights": {}, "performance": {}}
    return _sanitize({
        "weights": aw.get_weights(),
        "performance": aw.get_layer_performance(),
        "history": aw.get_adaptation_history(10),
    })


@router.get("/mtf")
async def get_multi_timeframe():
    """Multi-timeframe confluence analysis."""
    mtf = _engine_state["mtf_engine"]
    if not mtf or not mtf.history:
        return {"mtf_score": 0, "agreement": "NONE", "timeframes": {}}
    return _sanitize(mtf.history[-1] if mtf.history else {})


@router.get("/global-markets")
async def get_global_markets():
    """Global market correlation data."""
    gc = _engine_state["global_corr"]
    if not gc:
        return {"global_score": 0, "data": {}}
    return _sanitize(gc.analyze())


@router.get("/feedback")
async def get_feedback():
    """Trade feedback loop insights."""
    fb = _engine_state["feedback_loop"]
    if not fb:
        return {"status": "NOT_AVAILABLE"}
    return _sanitize(fb.get_insights())


@router.get("/uoa")
async def get_uoa():
    """Unusual options activity."""
    u = _engine_state["uoa_detector"]
    md = _engine_state["market_data"]
    if not u or not md:
        return {"alerts": [], "smart_money_signal": "NEUTRAL"}
    chain_df = md.get_option_chain("NIFTY")
    underlying = md.get_ltp("NIFTY") or 22500
    return _sanitize(u.detect(chain_df, underlying))


@router.get("/order-flow")
async def get_order_flow():
    """Order flow / market depth analysis."""
    of = _engine_state["order_flow"]
    md = _engine_state["market_data"]
    if not of:
        return {"order_flow_score": 0, "order_flow_direction": "NEUTRAL"}
    underlying = md.get_ltp("NIFTY") or 22500 if md else 22500
    return _sanitize(of.analyze_depth({}, underlying))


@router.get("/vix")
async def get_vix():
    """India VIX analysis."""
    v = _engine_state["vix_analyzer"]
    if not v:
        return {"vix": 0, "regime": "UNKNOWN"}
    v.update()
    return _sanitize(v.analyze())


@router.get("/hedging")
async def get_hedging():
    """Dynamic hedging recommendations."""
    h = _engine_state["dynamic_hedger"]
    pe = _engine_state["paper_engine"]
    md = _engine_state["market_data"]
    if not h or not pe:
        return {"hedge_needed": False, "recommendations": []}
    underlying = md.get_ltp("NIFTY") or 22500 if md else 22500
    greeks = h.calculate_portfolio_greeks(pe.positions, underlying)
    return _sanitize(h.check_hedge_needed(greeks))


@router.post("/walk-forward")
async def run_walk_forward():
    """Run walk-forward optimization."""
    wf = _engine_state["walk_forward"]
    loader = _engine_state["backtest_loader"]
    md = _engine_state["market_data"]
    if not wf:
        return {"error": "Not initialized"}
    if loader:
        df = loader.load("NIFTY", days=120, source="auto")
    elif md:
        df = md._mock_historical()
    else:
        return {"error": "No data"}
    return _sanitize(wf.run(df, "NIFTY"))

"""
Options Trading Engine — Main Entry Point.
Initializes all engines, starts background threads, launches FastAPI.
"""

import asyncio
import logging
import os
import sys
import threading
import time
from datetime import datetime

import uvicorn

import config
from core.market_data import MarketDataService
from core.option_chain import OptionChainAnalyzer
from core.historical_data import HistoricalDataManager
from core.instruments import InstrumentManager
from analysis.greeks import BlackScholes
from analysis.technical import TechnicalAnalysis
from analysis.oi_analysis import OIAnalyzer
from analysis.iv_analysis import IVAnalyzer
from analysis.sentiment import SentimentEngine
from analysis.signals import SignalAggregator
from strategy.strategies import StrategySelector
from trading.paper_engine import PaperTradingEngine
from trading.risk_manager import RiskManager
from trading.order_manager import OrderManager
from trading.portfolio import PortfolioTracker
from backtest.engine import BacktestEngine
from backtest.data_loader import BacktestDataLoader
from backtest.report import BacktestReport
from advanced.adaptive_weights import AdaptiveWeightOptimizer
from advanced.multi_timeframe import MultiTimeframeEngine
from advanced.global_correlation import GlobalCorrelationEngine
from advanced.feedback_loop import FeedbackLoop
from advanced.uoa_detector import UOADetector
from advanced.order_flow import OrderFlowAnalyzer
from advanced.vix_analyzer import VIXAnalyzer
from advanced.dynamic_hedging import DynamicHedger
from advanced.walk_forward import WalkForwardOptimizer
from web.app import app
from web.api_routes import router as api_router, inject_engines

# ─── Logging Setup ───────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_DIR / "trading.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("MAIN")


# ═══════════════════════════════════════════════════════════
#  ENGINE INITIALIZATION
# ═══════════════════════════════════════════════════════════

def init_engines():
    """Initialize all trading engine components."""
    logger.info("=" * 60)
    logger.info("  OPTIONS TRADING ENGINE v2.0 — INSTITUTIONAL GRADE")
    logger.info("  Paper Trading Capital: Rs.%.2f", config.PAPER_TRADING_CAPITAL)
    logger.info("  Advanced Modules: Adaptive Weights, MTF, Global, UOA, Hedging")
    logger.info("=" * 60)

    # Core
    market_data = MarketDataService()
    instruments = InstrumentManager()
    historical = HistoricalDataManager(market_data)

    # Analyzers
    option_chain = OptionChainAnalyzer("NIFTY")
    greeks = BlackScholes()
    technical = TechnicalAnalysis()
    oi_analyzer = OIAnalyzer()
    iv_analyzer = IVAnalyzer()
    sentiment = SentimentEngine()
    signal_agg = SignalAggregator()

    # Strategy
    strategy_selector = StrategySelector()

    # Trading
    paper_engine = PaperTradingEngine()
    risk_manager = RiskManager()
    order_manager = OrderManager(risk_manager, paper_engine)
    portfolio_tracker = PortfolioTracker()

    # Backtest
    backtest_engine = BacktestEngine()
    backtest_loader = BacktestDataLoader(market_data)

    # Advanced modules
    adaptive_weights = AdaptiveWeightOptimizer()
    mtf_engine = MultiTimeframeEngine()
    global_corr = GlobalCorrelationEngine()
    feedback_loop = FeedbackLoop()
    uoa_detector = UOADetector()
    order_flow = OrderFlowAnalyzer()
    vix_analyzer = VIXAnalyzer()
    dynamic_hedger = DynamicHedger()
    walk_forward = WalkForwardOptimizer()

    state = {
        "market_data": market_data,
        "instruments": instruments,
        "historical": historical,
        "option_chain_analyzer": option_chain,
        "greeks_calc": greeks,
        "technical": technical,
        "oi_analyzer": oi_analyzer,
        "iv_analyzer": iv_analyzer,
        "sentiment": sentiment,
        "signal_aggregator": signal_agg,
        "strategy_selector": strategy_selector,
        "paper_engine": paper_engine,
        "risk_manager": risk_manager,
        "order_manager": order_manager,
        "portfolio_tracker": portfolio_tracker,
        "backtest_engine": backtest_engine,
        "backtest_loader": backtest_loader,
        # Advanced
        "adaptive_weights": adaptive_weights,
        "mtf_engine": mtf_engine,
        "global_corr": global_corr,
        "feedback_loop": feedback_loop,
        "uoa_detector": uoa_detector,
        "order_flow": order_flow,
        "vix_analyzer": vix_analyzer,
        "dynamic_hedger": dynamic_hedger,
        "walk_forward": walk_forward,
    }

    logger.info("All engines initialized.")
    logger.info("Data Tier: %s | Latency: %s", market_data.data_tier, market_data.latency_estimate)
    if market_data.data_tier == "YFINANCE":
        logger.warning("⚠️  yfinance is 15-min delayed. For real-time, configure Angel One in .env")
    elif market_data.data_tier == "MOCK":
        logger.warning("⚠️  Using MOCK data. Configure Angel One or install yfinance for real data.")

    return state


# ═══════════════════════════════════════════════════════════
#  BACKGROUND TRADING LOOP
# ═══════════════════════════════════════════════════════════

def is_market_open():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    open_time = now.replace(hour=config.MARKET_OPEN_HOUR, minute=config.MARKET_OPEN_MINUTE, second=0)
    close_time = now.replace(hour=config.MARKET_CLOSE_HOUR, minute=config.MARKET_CLOSE_MINUTE, second=0)
    return open_time <= now <= close_time


def trading_loop(state):
    """
    Main trading loop — runs in a background thread.
    Continuously analyzes market + generates signals + executes paper trades.
    """
    md = state["market_data"]
    oca = state["option_chain_analyzer"]
    ta = state["technical"]
    oi = state["oi_analyzer"]
    iv = state["iv_analyzer"]
    sentiment = state["sentiment"]
    sig_agg = state["signal_aggregator"]
    strat_sel = state["strategy_selector"]
    paper = state["paper_engine"]
    risk_mgr = state["risk_manager"]
    order_mgr = state["order_manager"]
    portfolio = state["portfolio_tracker"]
    hist = state["historical"]
    # Advanced
    adaptive = state["adaptive_weights"]
    mtf = state["mtf_engine"]
    global_corr = state["global_corr"]
    feedback = state["feedback_loop"]
    uoa = state["uoa_detector"]
    order_flow = state["order_flow"]
    vix_eng = state["vix_analyzer"]
    hedger = state["dynamic_hedger"]

    logger.info("Trading loop started — ALL ADVANCED MODULES ACTIVE.")
    last_signal_time = 0
    last_sentiment_time = 0
    last_global_time = 0
    last_vix_time = 0
    last_hedge_check_time = 0

    # Cache historical data — don't regenerate every loop
    hist_df = md.get_historical("NIFTY", period="59d", interval="5m")
    last_hist_refresh = time.time()

    while True:
        try:
            now = time.time()

            # Reset daily counters at market open
            current_hour = datetime.now().hour
            current_min = datetime.now().minute
            if current_hour == config.MARKET_OPEN_HOUR and current_min == config.MARKET_OPEN_MINUTE:
                paper.reset_daily()
                risk_mgr.reset_daily()

            # ── 1. Fetch Market Data ──────────────────────
            underlying_price = md.get_ltp("NIFTY") or 22500
            chain_df = md.get_option_chain("NIFTY")

            # ── 2. Option Chain Analysis ──────────────────
            oca_result = oca.analyze(chain_df, underlying_price)

            # ── 3. OI Analysis ────────────────────────────
            oi_result = oi.analyze(chain_df, underlying_price)

            # ── 4. IV Analysis ────────────────────────────
            iv_result = iv.analyze(chain_df, underlying_price)

            # ── 5. Technical Analysis ─────────────────────
            # Refresh historical data every 5 minutes (not every loop)
            if now - last_hist_refresh > 300:
                hist_df = md.get_historical("NIFTY", period="59d", interval="5m")
                last_hist_refresh = now
            if not hist_df.empty:
                ta.set_data(hist_df)
                tech_result = ta.full_analysis()
            else:
                tech_result = {}

            # ── 6. Sentiment Analysis (every 5 min) ──────
            sent_result = {}
            if now - last_sentiment_time > config.SENTIMENT_REFRESH:
                sent_result = sentiment.analyze_all("NIFTY")
                last_sentiment_time = now

            # ══ ADVANCED: Multi-Timeframe Analysis ═══════
            mtf_result = {}
            if not hist_df.empty:
                timeframes = mtf.resample_to_timeframes(hist_df)
                mtf_result = mtf.analyze(timeframes)

            # ══ ADVANCED: Global Market Correlation ══════
            global_result = {}
            if now - last_global_time > 120:  # Every 2 min
                global_result = global_corr.analyze()
                last_global_time = now

            # ══ ADVANCED: VIX Analysis ═══════════════════
            vix_result = {}
            if now - last_vix_time > 60:  # Every 1 min
                vix_eng.update()
                vix_result = vix_eng.analyze()
                last_vix_time = now

            # ══ ADVANCED: Unusual Options Activity ═══════
            uoa_result = uoa.detect(chain_df, underlying_price)

            # ══ ADVANCED: Order Flow Analysis ════════════
            flow_result = order_flow.analyze_depth({}, underlying_price)

            # ── 7. Generate Signal (every 30s) ───────────
            if now - last_signal_time > config.SIGNAL_REFRESH:
                # Use adaptive weights
                current_weights = adaptive.get_weights()

                signal = sig_agg.generate(
                    "NIFTY",
                    technical=tech_result,
                    oi_data=oi_result,
                    iv_data=iv_result,
                    sentiment_data=sent_result if sent_result else None,
                    adaptive_weights=current_weights,
                )
                last_signal_time = now

                # ══ ADVANCED: MTF Gate ═══════════════════
                # Only trade if multi-timeframe agrees
                mtf_tradeable = mtf_result.get("tradeable", True)
                mtf_agreement = mtf_result.get("agreement", "MODERATE")

                # ══ ADVANCED: Feedback Gate ══════════════
                should_skip, skip_reason = feedback.should_skip_trade({
                    "regime": signal.regime,
                    "confidence": signal.confidence,
                })

                # ══ ADVANCED: VIX Gate ═══════════════════
                vix_regime = vix_result.get("regime", "NORMAL_VOL")
                vix_size_mult = vix_result.get("position_size_multiplier", 1.0)

                logger.info(
                    "Signal: %s | Score: %.4f | Conf: %.1f%% | Regime: %s | "
                    "MTF: %s | VIX: %s | UOA: %s | Global: %s",
                    signal.direction, signal.score, signal.confidence,
                    signal.regime, mtf_agreement, vix_regime,
                    uoa_result.get("smart_money_signal", "--"),
                    global_result.get("global_direction", "--"),
                )

                # ── 8. Execute Paper Trades ───────────────
                can_trade = (
                    signal.is_actionable
                    and is_market_open()
                    and mtf_tradeable
                    and not should_skip
                    and vix_regime != "PANIC"
                )

                if can_trade:
                    lot_size = config.INDICES["NIFTY"]["lot_size"]
                    strike_interval = config.INDICES["NIFTY"]["strike_interval"]

                    order = strat_sel.select_and_evaluate(
                        signal, chain_df, underlying_price,
                        symbol="NIFTY", lot_size=lot_size,
                        strike_interval=strike_interval,
                    )

                    if order:
                        order.__dict__["lot_size"] = lot_size
                        managed = order_mgr.submit_order(order)
                        if managed.positions:
                            logger.info("TRADE OPENED: %s | %d legs | MTF: %s",
                                       order.strategy_name, len(order.legs), mtf_agreement)
                elif should_skip:
                    logger.info("Trade SKIPPED (feedback): %s", skip_reason)
                elif not mtf_tradeable:
                    logger.info("Trade SKIPPED (MTF conflict): %s", mtf_agreement)

            # ── 9. Check Exit Conditions ──────────────────
            if paper.positions:
                price_map = {}
                for pos in paper.positions:
                    row = chain_df[chain_df["strike"] == pos.strike]
                    if not row.empty:
                        col = "ce_ltp" if pos.option_type == "CE" else "pe_ltp"
                        price_map[pos.strike] = float(row.iloc[0][col])
                    else:
                        price_map[pos.strike] = pos.current_price

                closed = paper.check_exits(price_map)
                for trade in closed:
                    risk_mgr.update_daily_pnl(trade["net_pnl"])
                    # ══ ADVANCED: Feedback + Adaptive learning ══
                    signal_data = trade.get("signal_data", {})
                    feedback.analyze_trade(trade, signal_data)
                    adaptive.record_trade_outcome(signal_data, trade["net_pnl"])
                    logger.info("TRADE CLOSED: #%d | P&L: Rs.%.2f | Reason: %s",
                               trade["trade_id"], trade["net_pnl"], trade["exit_reason"])

            # ══ ADVANCED: Dynamic Hedging Check ══════════
            if paper.positions and now - last_hedge_check_time > 60:
                port_greeks = hedger.calculate_portfolio_greeks(
                    paper.positions, underlying_price
                )
                hedge_check = hedger.check_hedge_needed(port_greeks)
                if hedge_check["hedge_needed"]:
                    logger.warning("HEDGE ALERT [%s]: %s",
                                  hedge_check["urgency"],
                                  "; ".join(r["reason"] for r in hedge_check["recommendations"]
                                           if r["urgency"] in ("HIGH", "MEDIUM")))
                last_hedge_check_time = now

            # ── Portfolio snapshot ────────────────────────
            portfolio.record_snapshot(
                equity=paper.equity,
                realized_pnl=paper.total_realized_pnl,
                unrealized_pnl=paper.total_unrealized_pnl,
                open_positions=len(paper.positions),
            )

            # ── 10. Auto Square-off ───────────────────────
            if (datetime.now().hour == config.AUTO_SQUAREOFF_HOUR and
                    datetime.now().minute >= config.AUTO_SQUAREOFF_MINUTE):
                for pos in list(paper.positions):
                    paper.close_position(pos, pos.current_price, "AUTO_SQUAREOFF")
                    logger.info("Auto square-off: #%d", pos.trade_id)

            # Sleep: fast with WebSocket, slower with yfinance to avoid rate limits
            loop_interval = 1 if md.data_tier == "WEBSOCKET" else config.MARKET_DATA_REFRESH
            time.sleep(loop_interval)

        except Exception as e:
            logger.error("Trading loop error: %s", e, exc_info=True)
            time.sleep(10)


# ═══════════════════════════════════════════════════════════
#  DATABASE INIT
# ═══════════════════════════════════════════════════════════

async def init_database():
    """Initialize database tables."""
    try:
        from database.connection import init_db
        await init_db()
    except Exception as e:
        logger.warning("Database init skipped: %s", e)


# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

def main():
    # Init database
    try:
        asyncio.run(init_database())
    except Exception as e:
        logger.warning("DB init failed (will continue without persistence): %s", e)

    # Init all engines
    state = init_engines()

    # Inject engines into API routes
    inject_engines(state)

    # Register API router
    app.include_router(api_router)

    # Start trading loop in background thread
    trading_thread = threading.Thread(target=trading_loop, args=(state,), daemon=True)
    trading_thread.start()
    logger.info("Background trading loop started.")

    port = int(os.environ.get("PORT", 7860))
    logger.info("Starting web server at http://0.0.0.0:%d", port)
    logger.info("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()

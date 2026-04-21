---
title: NIFTY OI Tracker
emoji: 📊
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: true
---

# NIFTY OI Tracker — Live Option Chain Data

Real-time NIFTY option chain data logger with StockMojo-style dashboard.

## Features
- Live option chain via Angel One SmartAPI (OI, LTP, Volume per strike)
- Every-minute data logging to NeonDB PostgreSQL
- PE-CE OI Difference table with color-coded signals
- Smart OI Charts (Candlestick + OI Lines + PCR)
- Price vs OI dual-axis charts with strike selector
- IST timezone-aware market hours detection

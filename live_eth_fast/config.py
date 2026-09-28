import os
from dotenv import load_dotenv

load_dotenv()

# This bot is SOL (replaces the earlier DOGE bot) - see SOL_RESEARCH.md for the full
# research behind this config.

PAIR = os.getenv("PAIR", "SOLUSD")
# Daily candles, matching this project's standing convention (Round 9): SMA(10,30) is a
# bar-count filter, validated only at daily resolution - don't lower this without
# re-validating at that resolution first, same caveat as every other coin in this project.
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "1440"))
STARTING_CASH = float(os.getenv("STARTING_CASH", "10000"))

# Entry: this project's baseline SMA-crossover+RSI signal (same as BTC's live entry,
# same function as experiments_wide_tp.py's baseline_signal_entry) - beat the full
# 6-family indicator toolkit (MACD/Stochastic/SuperTrend/ADX/VWAP/Ichimoku) for SOL
# specifically. See SOL_RESEARCH.md / experiments_sol_deepdive.py.
FAST_MA_PERIOD = int(os.getenv("FAST_MA_PERIOD", "10"))
SLOW_MA_PERIOD = int(os.getenv("SLOW_MA_PERIOD", "30"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "70"))
RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", "30"))

# Exit rule: sell automatically at +TAKE_PROFIT_PCT or -STOP_LOSS_PCT from entry,
# whichever hits first - plain fixed exit, no trailing stop. SOL's own trailing-stop-
# ratchet search (same Round 26/27 methodology as BTC/ETH) found nothing beats this fixed
# exit: every trail config either underperformed or overfit to a single outlier trade.
# SL=2%/TP=30% is the grid-search-validated combo (widened past the original narrow
# SL in {1,3,5,7}% grid to SL in 0.5%-20%, matching Round 13's density): 32.02% avg
# return / 14.23% avg drawdown, passing all 3 of SOL's real Kraken-native regime splits.
# See SOL_RESEARCH.md for the full grid and the split-by-split numbers.
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "30.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))

# Kraken taker fee, charged on both the buy and the sell (~0.26% each side is typical).
FEE_PCT = float(os.getenv("FEE_PCT", "0.26"))

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

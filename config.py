import os
from dotenv import load_dotenv

load_dotenv()

PAIR = os.getenv("PAIR", "XBTUSD")
# 1440 (daily) per README Round 9: Round 8's config was validated on daily candles only
# (rounds 2-8 all used Yahoo daily data) but the live bot was actually running on 15m
# candles - an unvalidated mismatch. Round 9 tested the exact Round 8 config against
# real Kraken 1-minute data resampled to both 15m and native 1m and it failed badly at
# both (-48% to -99% returns, 71-99% drawdowns, across 4 independent regimes) - the
# SMA(10)/SMA(30) periods are BAR counts, so on daily bars that's a 10-day/30-day trend
# filter, but on 15m bars the same numbers become a 2.5-hour/7.5-hour filter: a much
# faster, noisier signal that gets stopped out by the 1% stop constantly. Daily is the
# only resolution this strategy has actually been shown to work at - don't lower this
# without re-validating at that resolution first.
INTERVAL_MINUTES = int(os.getenv("INTERVAL_MINUTES", "1440"))
STARTING_CASH = float(os.getenv("STARTING_CASH", "10000"))

FAST_MA_PERIOD = int(os.getenv("FAST_MA_PERIOD", "10"))
SLOW_MA_PERIOD = int(os.getenv("SLOW_MA_PERIOD", "30"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERBOUGHT = float(os.getenv("RSI_OVERBOUGHT", "70"))
RSI_OVERSOLD = float(os.getenv("RSI_OVERSOLD", "30"))

# These defaults (1% stop) come from README Round 8: the only design in 8 rounds of
# research to show positive expectancy across 4 independent, structurally different BTC
# market regimes (2014-2026). STOP_LOSS_PCT is the INITIAL floor at entry, before the
# Round 27 trailing stop below has engaged.
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "1.0"))

# TAKE_PROFIT_PCT is no longer used to exit trades - kept only for reference/rollback.
# Round 27 (experiments_trailing_ratchet.py, run_btc_trailing_stop_analysis) replaced the
# fixed take-profit with a staged trailing stop: once profit crosses a TRAIL_STEP_PCT
# threshold, the floor ratchets up to TRAIL_PCT below the peak (close-confirmed, never
# loosens). No milestone ladder (tested and found to add no value for BTC, same as ETH -
# see Round 27). Validated on ALL 4 of BTC's independent regime splits, no exclusions
# needed (beats live baseline on both return and drawdown... on 4/4 splits, not just some
# - see Round 27 for the full grid search): 39.14% vs 14.93% avg return, 13.74% vs 12.24%
# avg drawdown.
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "20.0"))
TRAIL_PCT = float(os.getenv("TRAIL_PCT", "4.0"))
TRAIL_STEP_PCT = float(os.getenv("TRAIL_STEP_PCT", "4.0"))

# Kraken taker fee, charged on both the buy and the sell (~0.26% each side is typical).
FEE_PCT = float(os.getenv("FEE_PCT", "0.26"))

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

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

# Entry mechanism swapped in Round 20: this bot (ETH only) now uses a Stochastic %K/%D
# mean-reversion entry instead of the SMA-crossover+RSI entry used elsewhere in this
# project - see strategy.py in this folder. FAST_MA_PERIOD/SLOW_MA_PERIOD/RSI_* are no
# longer used by this bot's strategy.py; kept out entirely rather than left as dead
# config to avoid a future reader thinking they still matter here.
STOCH_K_PERIOD = int(os.getenv("STOCH_K_PERIOD", "21"))
STOCH_OVERSOLD = float(os.getenv("STOCH_OVERSOLD", "30"))

# Round 23: ADX regime filter added on top of the Stochastic entry - requires ADX above
# this threshold (a real trending/volatile regime, not dead-flat chop) for a signal to
# count. Verified split-by-split: split2 unaffected (every historical signal there
# already had ADX>15); split1 dropped ~5 of its weakest trades (86->81), raising that
# split's return from 7.14% to 15.64% at unchanged drawdown. A small, low-risk
# refinement, not a new mechanism - see README Round 23.
ADX_PERIOD = int(os.getenv("ADX_PERIOD", "14"))
ADX_THRESHOLD = float(os.getenv("ADX_THRESHOLD", "15"))

# Round 20's validated Stochastic-reversion entry: 21-day Stochastic, oversold<30 -
# beats every SMA-crossover config tested for ETH on both safety (8.9-14.9% drawdown vs.
# 27-35% for the prior live config) AND cadence (5.5-7.5 day median gap between trades
# vs. ~15.5 days), across both independent regime splits, 0 gap-through events on the
# tight 1% stop. See README Round 20. STOP_LOSS_PCT is the INITIAL floor at entry, before
# the Round 26 trailing stop below has engaged.
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "1.0"))

# TAKE_PROFIT_PCT is no longer used to exit trades - kept only for reference/rollback.
# Round 26 (experiments_trailing_ratchet.py, follow-ups 1-7) replaced the fixed take-
# profit with a staged trailing stop: once profit crosses a TRAIL_STEP_PCT threshold, the
# floor ratchets up to TRAIL_PCT below the peak (close-confirmed, never loosens). No
# milestone ladder (tested and found to add no value for ETH - see follow-up 2/3). Bear-
# market split (2018-19) explicitly excluded from this validation - proven entry-side and
# un-fixable by any exit mechanism (follow-up 6) - so this exit change does not address
# that separately-diagnosed issue. Validated on ETH's other 3 regime splits: beats live
# baseline on both return and drawdown (60.34% vs 11.50% avg return, 22.64% vs 18.05% avg
# drawdown), all 3 splits passing - see follow-up 7 for the full grid search.
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "10.0"))
TRAIL_PCT = float(os.getenv("TRAIL_PCT", "3.5"))
TRAIL_STEP_PCT = float(os.getenv("TRAIL_STEP_PCT", "4.0"))

# Kraken taker fee, charged on both the buy and the sell (~0.26% each side is typical).
FEE_PCT = float(os.getenv("FEE_PCT", "0.26"))

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

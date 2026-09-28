# AI Trading Bot

A crypto trading bot using an SMA-crossover + RSI strategy, paper-traded against real
Kraken market data with a locally simulated portfolio. **No exchange account or API
keys required** — Kraken's public market-data API needs no auth, and all "orders" are
just simulated cash/coin swaps tracked in `state.json`.

(We originally targeted Binance, but Binance.com — including its testnet — returns
HTTP 451 and blocks US-based access entirely, which is why this uses Kraken's public
data instead.)

## Current status (read this first if picking the project back up)

> **2026-09-24 - owner decision: only the ETH bot is live.** The BTC bot
> (`AiTradingBotPaperTrader`) and the SOL bot (`AiTradingBotPaperTrader_SOL`) were stopped and
> their scheduled tasks unregistered; both were flat (no positions, $10,000 each, no trades).
> Task definitions are backed up in `retired\*.task.xml`; code, logs and configs are untouched.
> To bring one back: `Register-ScheduledTask -TaskName <name> -Xml (Get-Content
> "retired\<name>.task.xml" -Raw)` then `Start-ScheduledTask -TaskName <name>`.
> The ETH bot (`live_eth/`, task `AiTradingBotPaperTrader_ETH`) is unaffected and still uses the
> root `venv` - do not delete the root folder's venv. The BTC/SOL descriptions below are history.
>
> **2026-09-24 - second ETH bot added (paper test, owner request for a more aggressive
> trading frequency):** `live_eth_fast/`, task `AiTradingBotPaperTrader_ETH_FAST` (runs hidden
> via pythonw). Round 18's fastest event entry for ETH: SMA(2,3) crossover + RSI<70, exit only
> at SL 2% / TP 50%, daily candles (same code as the retired SOL bot, matching
> experiments_fast_entry exactly). Round 18 backtest: +621.85% avg return but **47.62% max
> drawdown** (broke the 40% cap - never shipped before); expect much bigger swings than
> `live_eth/`. Own $10,000 paper balance and state.json; independent of `live_eth/`.

- Strategy in production: **Round 8 config, Round 9 timeframe fix** — `STOP_LOSS_PCT=1.0`,
  `TAKE_PROFIT_PCT=20.0`, BTC (`XBTUSD`), running on **daily candles**
  (`INTERVAL_MINUTES=1440`). Round 8 validated this stop/target pair with positive
  expectancy across 4 independent market regimes, but only at daily resolution; Round 9
  discovered the live bot was actually running on 15-minute candles (a leftover default,
  never itself validated) and found the same config fails catastrophically there
  (-48% to -99% returns) — see the Round 9 entry below. Daily is the only resolution this
  has been shown to work at. It also does **not** beat buy-and-hold in strong bull
  markets even on daily — see the Round 8 entry before trusting it further.
- **The bot now runs as a Windows Scheduled Task**, `AiTradingBotPaperTrader` (action:
  `venv\Scripts\python.exe main.py`, working directory `C:\Ai Trading Bot`, auto-restarts
  up to 5x on failure). This survives a closed terminal, unlike a plain background
  process. To check/control it:
  ```powershell
  Get-ScheduledTask -TaskName AiTradingBotPaperTrader | Select-Object TaskName, State
  Stop-ScheduledTask -TaskName AiTradingBotPaperTrader   # stop
  Start-ScheduledTask -TaskName AiTradingBotPaperTrader  # start
  ```
  Before restarting after a config change, always check for a stale duplicate process
  first — this exact bug (two copies running, corrupting shared `state.json`) has
  recurred multiple times:
  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, CommandLine
  ```
  One `python main.py` invocation legitimately shows as 2 PIDs (parent/child) on this
  machine — that's normal, not a duplicate; a duplicate is two invocations with
  *different* creation timestamps. If you find a real duplicate, or need to hard-reset,
  `Stop-ScheduledTask` first (not just `Stop-Process` — the task will otherwise
  auto-restart what you just killed).
- The `state.json` in this folder holds the live simulated portfolio (cash, coins, entry
  price, trade history) — it persists on disk across restarts, so restarting the bot
  resumes the existing paper position rather than starting over. Delete it deliberately
  (fresh start) whenever the pair, interval, or exit config changes, since old state
  doesn't correspond to the new strategy.
- This whole README is written to be readable cold — a new Claude Code session (or you, much
  later) can pick this up from the Experiments log below without any other context.
- **Two more live paper bots exist for ETH and SOL**, per Round 15's multi-coin
  validation, since updated by Rounds 17 and 20 — each fully self-contained in its own
  directory (`live_eth/`, `live_sol/`, each with its own copy of `main.py`/`config.py`/
  `paper_trader.py`/`strategy.py`/`data_fetcher.py`, its own `.env`, own `state.json`, own
  `logs/bot.log` — no shared state with the BTC bot or each other, by design, to avoid the
  duplicate-process/shared-state corruption bug noted above). Scheduled tasks
  `AiTradingBotPaperTrader_ETH` / `AiTradingBotPaperTrader_SOL` (XML at
  `live_eth\task.xml` / `live_sol\task.xml`, matching the original task's settings
  exactly) are registered and running.
  - **The third bot was swapped from DOGE to SOL** in a later session (see
    `SOL_RESEARCH.md`): the folder and scheduled task were renamed from `live_doge`/
    `AiTradingBotPaperTrader_DOGE` to `live_sol`/`AiTradingBotPaperTrader_SOL` (a clean
    rename, not a repurpose-in-place workaround — renaming the scheduled task turned out
    to work fine). It runs SOL's own grid-search-validated config: baseline SMA(10,30)+RSI
    entry, `STOP_LOSS_PCT=2.0`/`TAKE_PROFIT_PCT=30.0`, no trailing stop. No open position
    at the time of the swap, so no state was lost.
  - **ETH runs Round 20's Stochastic mean-reversion strategy + Round 23's ADX filter** —
    a genuinely different entry mechanism from every other bot in this project (21-day
    %K/%D, buy on an oversold crossover with ADX>15, `STOP_LOSS_PCT=1.0`/
    `TAKE_PROFIT_PCT=10.0`), implemented as real code in `live_eth/strategy.py`/
    `config.py`, not just an `.env` change. The fastest and safest validated ETH config
    found across the whole project (5.5-7.5 day median gap, 8.9-14.9% drawdown).
    Round 25 found a much-higher-return FVG alternative for ETH (285.15% vs. 26.11%) but
    it trades less often (13-17 days) and carries more risk (30.69% drawdown) - not
    shipped, a real tradeoff pending a decision.
  - **DOGE ran Round 25's bullish Fair Value Gap (FVG) strategy** — another genuinely
    different entry mechanism (a 3-candle price-gap mitigation+continuation pattern,
    simplified Smart Money Concepts), implemented as real code in what was then
    `live_doge/strategy.py`/`config.py`. `STOP_LOSS_PCT=3.0`/`TAKE_PROFIT_PCT=30.0`.
    Beat every prior DOGE config on every axis: return (245.63%), drawdown (30.08%),
    and cadence (13-16.5 day median gap) - a clean win, not a tradeoff. **No longer
    live** — this slot was swapped to SOL, see above.
  - All restarted clean (no prior open position, so no `state.json` reset needed) — see
    Rounds 17/19/20 for the validation behind each.
  - **Outage 2026-09-22 23:12 → 2026-09-23 22:37 (BTC and SOL only):** both tasks ended with
    0xC000013A (STATUS_CONTROL_C_EXIT, i.e. externally interrupted, e.g. a console closed) one
    minute after a restart; no error in their logs, both flat, no state lost. Restarted via
    `Start-ScheduledTask`; verified 3 bots = 3 parent/child pairs, no duplicates. ETH was
    unaffected (closed a trailing-stop trade 2026-09-23: +8.99%, equity $10,842). If it recurs,
    find what is closing the bots' console windows.

## Strategy

- Fast/slow SMA crossover generates the BUY entry signal.
- RSI filters out entries that would chase an overbought move.
- Exit is a fixed take-profit / stop-loss from the entry price (`TAKE_PROFIT_PCT` /
  `STOP_LOSS_PCT`, whichever hits first), not a reverse signal — see `paper_trader.py`.
- One position at a time (no pyramiding, no shorting). Trading fees (`FEE_PCT`) are
  deducted on both the buy and the sell, in both the backtest and the live paper loop.

Tune periods, pair, and starting balance in `.env` (copy from `.env.example`) — or just
run with defaults, no config needed.

**Honesty check, not a guarantee:** as of Round 8 (see Experiments log below), this
default (1% stop-loss, 20% take-profit, BTC) is the first config in this project to show
positive expectancy across 4 independent, structurally different market regimes
(2014-2026) — real evidence, not a coincidence. But it does **not** beat simply
buy-and-holding BTC in 3 of those 4 regimes (it only outright wins in bear/choppy
markets); in a strong bull market it's a bounded-loss, lower-return alternative to
holding. Read Round 8 before treating this as "found an edge" — it's a validated,
repeatable risk profile, not a proven way to beat the market. Still paper trading only;
not a config to trust with real money.

## Setup

Install dependencies into the existing venv:
```
venv\Scripts\pip install -r requirements.txt
```

## Backtest first

Before running the live loop, backtest the strategy against Kraken's historical data:

```
venv\Scripts\python backtest.py XBTUSD 240
```

Args are `[PAIR] [INTERVAL_MINUTES]`, both optional (defaults from `.env`/`config.py`).
Kraken only retains ~720 candles per pair/interval, so a larger interval covers more
history (15m ≈ 7.5 days, 240m/4h ≈ 120 days, 1440m/1d ≈ 2 years).

This prints total return, max drawdown, win rate, and saves an equity curve chart
(`backtest_equity.png`).

## Run the paper-trading bot

```
venv\Scripts\python main.py
```

The bot polls Kraken for a new candle every `POLL_INTERVAL_SECONDS`, computes the
signal, and simulates a trade when the signal flips. Portfolio state (cash, coins,
trade log) persists in `state.json` so a restart doesn't lose the simulated position.
Logs go to `logs/bot.log`.

## Going live

This project only does simulated paper trading right now. Going live would mean adding
a real exchange execution layer (Kraken's authenticated REST API, requires a funded
account and real API keys) in place of `paper_trader.py` — treat that as a separate,
deliberate step once you're happy with backtested/paper results, not a flag flip.

## Experiments log

`walk_forward.py` grid-searches the default SMA/RSI parameters on a train slice and
re-checks only the best candidates on an untouched test slice — a config only counts if
it also works on data it didn't see.

`experiments.py` goes further: it tries five genuinely different signal families
(RSI mean-reversion, Bollinger-band mean-reversion, volatility-filtered Donchian
breakout, trend-filtered SMA crossover, momentum/rate-of-change) across BTC and ETH at
4h and 1d intervals, each with the same train/test validation. **Result: none of them
beat buy-and-hold out-of-sample.** The test windows covered a strong BTC/ETH uptrend
(buy-and-hold return 10–27% across the windows tested), and every strategy that sat out
part of that move — which all of them did, since none stay invested 100% of the time —
underperformed just holding. That's a real, useful finding: in a strong trend, the bar
for an active strategy to add value over holding is very high.

**Round 2 — `experiments_regime.py` (multi-year Yahoo Finance data, split by actual
market regime instead of a blind chronological cut):** Kraken's public endpoint caps out
around 720 candles, which meant every prior test only ever covered one continuous
uptrend window — not a fair test of "does this add value across different kinds of
markets." `data_fetcher.fetch_yahoo_history()` pulls years of true daily OHLCV from
Yahoo Finance's public chart API (no auth needed; BTC-USD back to 2016 at 10y range,
ETH-USD back to its 2017 listing — **note:** `range="max"` silently downsamples crypto
tickers to ~monthly candles, use a bounded range like `"10y"` for real daily bars). Same
5 strategy families, same engine, but trained on one regime and tested on a structurally
different one (e.g. train on 2018–2021 chop/COVID-crash/recovery, test on the 2022 bear
+ 2023–25 recovery; train on pre-COVID 2017–2019, test on the COVID crash + 2021 bull).

**Result: 0 of 20 configs (5 families × 2 pairs × 2 regime splits) validated.** This
round wasn't close — buy-and-hold returned 84% to over 2,700% across these multi-year
windows, and every active strategy trailed it, most posting outright negative returns.
The mechanism is structural, not bad luck: a strategy built around a 1.5–3% take-profit
exits almost immediately after any entry, so by construction it can never capture a
multi-year, multi-hundred-percent trend — it's optimized to bank tiny gains (or eat
tiny/not-so-tiny losses) while the underlying asset compounds enormously without it. Two
rounds, 60 tested configs, two data sources, two exit styles, multiple regimes: the
consistent finding is that short-horizon technical-indicator strategies with a tight
TP/SL don't beat holding BTC/ETH. Re-run `experiments_regime.py` if you want to keep
checking as time passes, but don't hand-tune against a window that's already failed —
that manufactures a fake edge instead of finding a real one. A strategy meant to
outperform this kind of long-term compounding would need to stay invested through big
trends (wide/trailing exits, not a tight fixed TP), which is a different design than
what's implemented here.

**Round 3 — `experiments_trend.py` (genuine trend-following exits, same regime
validation):** Round 2's finding located a real design flaw, not just a data gap — every
setup used the tight ~2%/1.5% TP/SL, which caps every winner at ~2% and therefore can
never capture a multi-year trend *by construction*, regardless of how good the entry
signal is. This round removes that ceiling for the entry styles where it makes sense
(trend-filtered SMA crossover, volatility breakout, momentum/ROC), testing two honest
trend-following exits instead: a trailing stop (ride the trend, exit N% off the peak
since entry) and a reverse-signal exit (exit when the fast/slow MA crosses back down —
the bot's original pre-TP/SL design). Mean-reversion entries (RSI/Bollinger) were left on
the tight TP/SL, since that pairing already made sense and was already fairly tested.

**Result: 1 of 16 setup/regime combinations passed the bar, and it didn't replicate.**
`crossover+reverse_signal` on BTC beat buy-and-hold on one regime split (129.2% vs
118.1% buy-and-hold, test 2022–2025, 27 trades) — but the same setup, same pair, failed
the required sanity check on the other regime split (334.1% vs 380.7% buy-and-hold, test
2020–2021), and failed on ETH in both splits. A finding that only replicates 1 time in 8
tries for the same pair isn't a validated edge, per the same discipline as every prior
round — it's within noise. That said, this is a genuinely different outcome than round 2:
with the profit ceiling removed, `crossover+reverse_signal` came *close* to buy-and-hold
repeatedly (129% vs 118%, 334% vs 380%) instead of losing badly by construction (round
2's tight-TP version lost -5% to -12% against 84%+ buy-and-hold). Fixing the exit design
turned "guaranteed to lose" into "roughly tracks the market, sometimes ahead, sometimes
behind" — real progress, just not yet a provable edge. Trailing-stop setups and momentum/
breakout entries did not come close in either split.

Three rounds, ~100 tested configs, two data sources, three exit styles (fixed TP/SL,
trailing stop, reverse-signal), multiple regimes: no configuration has produced a
repeatable, out-of-sample edge over simply buying and holding BTC or ETH.

**Round 4 — `pairs_strategy.py`/`pairs_backtest.py`/`pairs_experiments.py` (relative-value
pairs trading, a fundamentally different mechanism than rounds 1–3):** instead of
predicting direction on one asset, this trades the BTC/ETH price *ratio* — short
whichever coin is relatively expensive, long whichever is relatively cheap, in equal
dollar size, betting the ratio reverts toward its recent average. In principle
market-neutral: profit doesn't require either coin to go up or down, only for the
ratio to mean-revert. This needed simulated short-selling, which didn't exist before
(`pairs_backtest.py` models both legs; **it does not model margin/borrow cost — real
short-selling has an ongoing cost this ignores, so treat any result as an optimistic
upper bound, not realistic P&L**).

**Before trusting the mean-reversion premise at all, it was sanity-checked** with an
ADF stationarity test (`pairs_strategy.mean_reversion_sanity_check`) on the BTC/ETH log
ratio. **It failed, everywhere tested:** p-values of 0.18–0.63 (need <0.05 to call the
ratio mean-reverting) across the full 2017–2026 history and every regime slice, and the
ratio only crosses its own rolling average every 100 to 1,000+ days instead of
oscillating — i.e. it trends for long stretches rather than reverting. Concretely: ETH
fell 19.76% against BTC's +87% gain in one test window alone. That's the ratio moving
directionally (ETH secularly under/outperforming BTC over multi-year stretches), not
noise around a stable mean — the core premise a pairs trade needs.

Ran the full regime-split validation anyway rather than stopping at the sanity check
(same discipline as every round: report the truth, don't skip the inconvenient part).
**Result: 0 of 10 top candidates across 2 regime splits validated** — even the best
training-set configs (+16.5% train) collapsed to losses on the untouched test regime
(-12% to -41%), with severe 30–70% max drawdowns. This matches the ADF finding exactly:
a pairs trade built on an assumption that fails a direct statistical test predictably
loses money when that assumption is wrong. No live pairs bot was built — nothing here
cleared the bar. The original single-asset paper bot (`main.py`) was never touched and
kept running throughout this round.

Four rounds, ~110 tested configs, two data sources, four strategy mechanisms
(single-asset directional, trend-following, mean-reversion, and now relative-value
pairs): none has produced a validated, out-of-sample edge. That's the honest state of
this project's strategy research as of this round.

**Round 5 — `experiments_indicators.py` (the standard TradingView-style toolkit: MACD
crossover, Stochastic oscillator, SuperTrend, ADX-filtered trend continuation, rolling-VWAP
mean-reversion, Ichimoku Cloud):** none of these were tried in rounds 1–4, which used
custom-built signals. Trend-following indicators (MACD, SuperTrend, ADX-filtered,
Ichimoku) use each indicator's own natural reverse-signal exit — same lesson as round 3,
pairing a trend entry with a tight fixed TP would just reproduce round 2's bug.
Mean-reversion indicators (Stochastic, rolling VWAP) use the tight TP/SL engine, the
correct pairing for a reversion trade. Note: "VWAP" here is a rolling volume-weighted
average on daily candles, not true session VWAP, which needs intraday data and resets
daily — a standard practical approximation, not the real thing.

**Result: 0 of 24 top candidates (6 setups × 2 pairs × 2 regime splits) validated.**
Same regime-split discipline as rounds 2–4: train on one multi-year window, only check
top candidates on a later untouched regime. One nuance worth stating plainly rather than
burying: on the ETH 2022–2025 test window, where buy-and-hold itself was *negative*
(-21.29%), several trend-following setups posted solidly positive returns that beat
buy-and-hold in raw terms — MACD +29.12%, Ichimoku +32.09%, SuperTrend +5.38%. All three
still failed the pass bar on drawdown alone (53–55% vs the 40% cap used for
trend-following strategies throughout this project), and none of them replicated that
result on the other regime split (BTC either split, or ETH's 2020–2021 split, where they
lagged buy-and-hold by hundreds to thousands of percentage points). The honest read: an
active trend-following exit tends to sit out part of a bear market and so loses less than
holding when the market is falling — an expected, coherent mechanism, not noise — but
that same mechanism causes it to miss enormous chunks of the runs that made buy-and-hold
so hard to beat everywhere else. It's a real, useful piece of insight for how these
strategies behave, but a single-regime, drawdown-disqualified result is not a validated
edge under the bar this project has used from round 1 onward.

Five rounds, ~135 tested configs, two data sources, five strategy mechanisms and six
additional standard indicators: the finding has been consistent throughout — no
technical-indicator strategy tested, custom or industry-standard, shows a repeatable,
out-of-sample edge over simply buying and holding BTC or ETH.

**Round 6 — `experiments_patient.py` ("just wait for the target": unconditional entry,
no stop-loss, ETH only):** a fundamentally different risk profile than every strategy
before it. Buy immediately whenever flat (no signal, no filter), hold with **no
stop-loss and no time limit**, sell only when unrealized profit reaches a target (tested
5% through 50%), immediately re-buy, repeat. Every prior round paired an entry with some
exit that could realize a loss; this one never closes a losing position — it just waits.

**Result: 0 of 8 targets passed on both regime splits.** The returns swing wildly by
regime — train-set returns of +209% to +364% on 2018–2021 that turned into test-set
*losses* of -17% to -29% on 2022–2025 (still underperforming even ETH's own -21.29%
buy-and-hold that period), versus train-set losses of -57% to -78% on 2017–2019 that
turned into test-set gains of +819% to +2,198% on the 2020–2021 window — but even that
best case still trailed simply buying and holding ETH (+2,715% that period), for the
same structural reason as rounds 1–3: taking profit and re-buying misses part of a
monster rally that holding captures in full.

**The headline number isn't the return, though — it's the underwater drawdown, which is
the entire point of testing a no-stop-loss design.** Every single target, on every split,
showed max underwater drawdown between **-57% and -94%** while a position was held before
recovering (or not). That's not a tail case; it's the typical case for this design over a
multi-year window that includes a real bear market. **And in every one of the 16
target/split combinations tested, the position was still open and underwater at the end
of the test window** (-6% to -38% unrealized) — a mechanical consequence of always
re-buying immediately after taking profit, which guarantees the strategy is mid-cycle,
holding something, whenever a test window (or a person's need for their money) happens to
end. A strategy that structurally has no exit from a loss, tested across real ETH
history, produced multiple 90%+ underwater stretches and consistently ended holding a
loss when the window closed. That is the honest answer to whether "just wait it out" is
safe: no, not even close, independent of the fact that the average-case return sometimes
looked good. Nothing shipped; the existing BTC bot (`main.py`) was left running
untouched.

Six rounds, ~150 tested configs: every mechanism tried — directional, trend-following,
mean-reversion, relative-value pairs, the standard indicator toolkit, and now
patient/no-stop-loss — has failed to produce a validated, repeatable edge over simply
buying and holding, and this round additionally surfaced a concrete, severe risk (90%+
underwater drawdown) in the one design that removed loss-realization entirely.

**Round 7 — `experiments_wide_tp.py` (tight 1% stop-loss, wide take-profit 3%-50%,
directly answering round 6):** round 6 showed removing the stop entirely produces 57-94%
underwater drawdowns. This keeps a genuinely tight stop (caps every loss near 1%) while
letting winners run much further than round 2's symmetric ~2%/1.5% design. Two entry
variants were tested to isolate what's actually doing the work: the project's baseline
SMA/RSI signal, versus buying unconditionally whenever flat.

**The entry signal turned out to matter enormously — unconditional entry is a disaster
under this exit design.** Across every take-profit level, both coins, both regimes,
unconditional entry produced -24% to -96% returns with 60-96% *realized* drawdowns and
near-zero-to-negative expectancy. The reason is direct: with no filter on when to buy,
ordinary daily noise triggers the 1% stop constantly (hundreds of trades per window, most
losers), and a stop that tight simply doesn't survive being entered at random times.

**With the baseline SMA/RSI entry, results are genuinely mixed rather than a flat fail —
and the honest picture depends on which question you ask.** Against this project's
primary bar (beat buy-and-hold): it did not clear that bar in either regime split for
either coin — it modestly beat ETH/BTC buy-and-hold during the down/choppy 2022-2025
window (buy-and-hold itself was negative or flat there), the same "loses less in a
downturn" pattern round 5 found, but lost badly to buy-and-hold in the 2020-2021 window
(BTC +543%, ETH +2,715%), for the same reason as every trend-limited exit before it: it
can't capture a monster rally in full.

Against a fairer, standalone bar (profitable, ≥5 trades, realized drawdown <40%, holds up
on *both* regime splits — round 4's style of bar for a strategy with a different risk
shape): **5 BTC configs passed** (take-profit 20%, 25%, 30%, 40%, 50%, all with the
baseline signal entry) — positive returns on both splits, realized drawdown a modest
9-13% (vs the 40% cap), 9-31 completed trades per window. Win rate on these was low
(8-13%) but **expectancy was positive** (+0.55% to +4.67% per trade) — the asymmetric bet
(many small ~1% losses, occasional large wins) genuinely paid for itself arithmetically in
this data, which is exactly the mechanism the user's design was betting on. **ETH — the
user's stated primary focus — did not produce a single passing config on both splits**;
split 2 (test 2020-2021) locked at an identical -12.83% loss across every take-profit
level from 7% up, meaning price never once ran up far enough after a signal entry before
getting stopped out, even during ETH's most explosive bull run on record.

**Stop-loss gap risk (does the 1% stop actually hold on daily candles?): yes, cleanly, for
the viable entry variant.** Across all 40 signal-entry runs, zero gap-through events — the
stop was never breached by an overnight gap. All 21 gap events (worst case: 0.28 extra
percentage points beyond the intended 1% loss) occurred in the already-disqualified
unconditional-entry runs. Underwater-while-holding also came back far more bounded than
round 6: -7.6% to -13.8% for signal-entry runs, vs. round 6's -57% to -94% — the tight
stop is doing exactly the job it was designed for.

**Nothing shipped to the live bot.** The BTC result is real cross-regime replication, not
noise dressed up — but it's thin evidence (samples of 9-31 trades per split, where a
couple of trades flipping outcome would materially move the win rate), it's BTC-only
where ETH was the ask, and even where it "passed," it's a bear/chop-market
loss-limiter that still loses hard to simply holding through a bull run — most of crypto's
history by return-weighted time. Relaxing this project's skepticism now, on the first
result that clears a loosened bar, after six rounds of deliberately not doing exactly
that, would defeat the point of the exercise. The existing BTC bot (`main.py`) continues
running untouched on its original strategy.

Seven rounds, ~230 tested configs: the clearest actionable insight yet is that a tight,
gap-resistant stop-loss reliably bounds risk (round 7) where no stop-loss catastrophically
does not (round 6) — but no configuration in any round, symmetric or asymmetric,
signal-driven or unconditional, single-asset or pairs, has produced a strategy that
reliably beats simply buying and holding BTC or ETH.

**Round 8 — `experiments_multisplit.py` (chasing round 7's most promising result with
more independent regimes, not just a wider parameter net):** round 7 found 1%
stop-loss / wide take-profit on BTC showed positive expectancy across its 2 regime
splits, flagged at the time as thin evidence. Testing 4 stop-losses (1/3/5/7%) x 13
take-profits (3%-100%) = 52 combos on those *same 2 splits* would let noise pass by pure
chance from casting a wider net, so this round adds 2 more genuinely independent splits
using BTC-USD history back to 2014-09 (the full available range): one early-history split
(train 2014-2017 chop/mania → test 2018-2019, a bear-dominated window, buy-and-hold
-47.33%) and one recent split (train 2019-2022 → test 2023-2026, buy-and-hold +356.72%),
alongside round 7's original two (test buy-and-hold +83.51% and +543.13%). Four
structurally different multi-year windows, same fixed baseline SMA/RSI entry throughout
(round 7 already ruled out unconditional entry).

**Result: 15 of 52 (stop-loss, take-profit) combos passed on all 4 independent splits** —
the strongest, most repeated evidence in this project's 8 rounds. Most notably, **every
one of the 1% stop-loss configs from 20% to 100% take-profit (8 combos) passed on every
single split**, with positive expectancy in all 32 (combo × split) results, ranging
+0.68% to +12.47% per trade, on healthy sample sizes (9-31 completed trades per split).
Win rates stayed low (5-25%) throughout — consistent with round 7's finding, not a
different mechanism — but the asymmetric payoff carried it to positive expectancy every
time. 3%/5% stop-loss variants also produced several all-4-splits passes (TP 15-40%) with
higher win rates (16-56%) and comparable expectancy.

**Gap risk was re-checked at every stop level, since round 7 only confirmed it at 1%:**
0 gap-through events at 1%, 5%, and 7% stops; the 3% stop had 11 occurrences across all
splits/take-profits, worst case only 0.20 extra percentage points beyond the intended
loss — trivial in magnitude either way. The 1% stop specifically remained gap-clean
across all 4 independent regimes, not just round 7's original 2.

**The crucial caveat, stated as plainly as the win:** this passes a fair, standalone bar
(profitable, ≥5 trades, <40% drawdown, repeatable across 4 independent regimes) — it does
**not** clear this project's primary bar of beating buy-and-hold. Checked against each
split's actual buy-and-hold return, the strategy only wins outright in the one
bear-dominated split (test 2018-2019, strategy +48% to +91% vs. buy-and-hold -47.33%) and
narrowly at the most extreme take-profit in one bull split (BTC 2022-2025: +224.6% at
TP=100% vs. buy-and-hold +83.51%). In the other two bull-dominated splits (+543.13% and
+356.72% buy-and-hold), every tested combo underperformed simply holding, sometimes
badly. So: a real, repeatable, bounded-loss, positive-expectancy trading style — and
also, still, not a way to beat the market in a bull run. Both things are true at once and
neither should get dropped from the summary.

**Shipped:** given this is the first result to survive 4 independent multi-year regimes
under a fair standalone bar (not just 2 splits, not a loosened bar in isolation), and this
remains 100% simulated paper trading with no real money at stake, this was implemented as
the new BTC default: `STOP_LOSS_PCT=1.0`, `TAKE_PROFIT_PCT=20.0` (the most conservative of
the 8 validated 1%-stop configs — chosen for having the most completed trades per split,
i.e. the most statistical confidence, not the highest backtested return, to avoid
smuggling a cherry-pick in under the validated label). `PAIR` was already `XBTUSD`
(BTC), so no pair change was needed. The live paper bot was restarted clean on this
config with a fresh `state.json`. Read the caveat above before reading anything into its
future performance beyond "bounded loss, positive expectancy, underperforms a bull
market" — that's the specific, real thing that's been validated, not "beats the market."

Eight rounds, ~280 tested configs: seven rounds of consistent negative results followed by
one genuinely repeatable positive one, precisely because round 8 applied *more* scrutiny
(more independent data, not a wider search on the same data) rather than less. That
sequencing is itself the reason this result is trustworthy where round 3's lone pass and
round 7's 2-split pass were correctly not trusted.

**Round 9 — `experiments_kraken_native.py` (does Round 8's validated config actually
work at the timeframe the live bot trades on?):** every round from 2 through 8 validated
on Yahoo Finance *daily* candles. The live bot, `main.py`, was actually trading Kraken
*15-minute* candles (`config.INTERVAL_MINUTES=15`, an unexamined leftover default) — a
real, unaddressed gap between what was proven and what was live. This round closes it
using Kraken's own official bulk historical data (`data/kraken_historical/extracted/
XBTUSD_1.csv`, genuine 1-minute OHLCV, Oct 2013–Jun 2026, ~4.9M rows — far richer than
any prior source), resampled to 15-minute (primary, matches the live bot) and tested
natively at 1-minute (secondary/exploratory), against the exact shipped Round 8 config
(1% stop, 20% target, baseline SMA(10)/SMA(30)+RSI entry), across 4 independent regime
splits spanning the full available history.

**A sharp point this round surfaces regardless of the numbers:** `FAST_MA_PERIOD=10` /
`SLOW_MA_PERIOD=30` are *bar counts*, not day counts. On daily bars (every prior round)
that's a 10-day/30-day trend filter. Applied unchanged to 15-minute bars (what the live
bot actually ran), the same numbers become a 2.5-hour/7.5-hour filter — a fundamentally
faster, noisier signal that only coincidentally shares the parameter values with what was
tested.

**Cross-check first, before the main result:** resampling the 1-minute data to daily and
comparing against Kraken's own native daily file matched *exactly* (0.0% difference) —
confirms the resampling logic is correct. Comparing against the Yahoo daily data used in
rounds 2–8: mean difference 0.31%, small and expected for two different exchanges: the
handful of larger outliers (up to 11.2%) cluster entirely in December 2017's bubble
mania, a well-documented period of genuine cross-exchange price divergence, not a data
error. Both data sources check out.

**Result: catastrophic failure at both resolutions, on every split.** 15-minute (the
live bot's actual timeframe): returns of **-48% to -90%**, drawdowns of **72-91%**, win
rates 5-7%, 295-458 completed trades per split — none of the 4 splits passed. 1-minute:
even worse, **-68% to -93%** returns, up to 99% drawdown. The mechanism matches the bar-
count/timeframe mismatch exactly: at 15-minute or 1-minute resolution the SMA(10)/
SMA(30) crossover fires constantly on short-term noise rather than real trend, and each
false signal gets stopped out at -1% (minus fees) before ever approaching the 20%
target — hundreds of small losses per split with almost nothing reaching the target to
offset them. This is the opposite of round 8's mechanism (few trades, low win rate, but
asymmetric payoff): here there are many more trades and the payoff asymmetry no longer
has room to work, because the strategy essentially never survives long enough in a
position to reach the target.

**The live bot had been running on this exact broken configuration** (15-minute
interval, Round 8's stop/target) since Round 8 shipped — validated at one resolution,
silently running at another, unvalidated one. Per the standing rule (switch to what's
actually proven when the live one doesn't validate): `INTERVAL_MINUTES` changed from 15
to **1440 (daily)** — the only resolution this configuration has been shown to work at.
Stop-loss/take-profit/pair unchanged (still `STOP_LOSS_PCT=1.0`, `TAKE_PROFIT_PCT=20.0`,
`XBTUSD`). Checked for stale processes, stopped the `AiTradingBotPaperTrader` scheduled
task (confirmed real and running — set up externally since round 8, not by this round),
reset `state.json`, restarted clean on the new interval.

**Housekeeping:** the ~18GB of intermediate zip/part files in `data/kraken_historical/`
were deleted after confirming the extracted CSVs were complete and correct (the exact
cross-check above) — only the 278MB `extracted/` folder and the checksum file remain.

Nine rounds: round 8 was this project's first genuinely validated finding, and round 9
is a reminder that validating a strategy is not the same as validating its deployment —
a config can be real and correct and still be running on the wrong timeframe. Everything
in this project is still paper-only; no real money, no real exchange account, ever.

**Round 10 — `experiments_multisplit_kraken.py` (Round 8's grid, extended along the
stop-loss axis, on better data):** Round 8 only ever tested a 1% stop-loss; this expands
to 1%/3%/5%/7%, same 13-value take-profit grid (3%-100%), same entry, same "must pass
all 4 independent regime splits" bar. Also swaps data source: Round 8/9 used Yahoo
Finance; this uses Kraken's own native daily OHLCV
(`data/kraken_historical/extracted/XBTUSD_1440.csv`, exchange-native, no resampling,
same file Round 9 verified) as primary, extending the regime splits to the full
available history (train starting 2013-10-06, the earliest Kraken has, and test capped
at 2026-06-30, the latest).

**Result: confirms Round 8 almost exactly, and directly answers the new question -
wider stops don't help; they get strictly worse.** SL=1% still passes all 4 splits on 8
take-profit levels (20% through 100%) — the exact same set Round 8 found on Yahoo data,
with nearly identical numbers per split (e.g. SL=1%/TP=100%, split 1: 224.6% return,
13.3% win rate, 15 trades — identical to Round 8's Yahoo-based figures for the same
window, a reassuring cross-data-source consistency check). SL=3% passes on 5 take-profit
levels (15%-40%) — fewer than 1%, but still a real, repeatable set. **SL=5% and SL=7%
passed ZERO combinations across all 4 splits** — every test at those stop levels either
failed outright or, at best, passed 3 of 4 splits with the 4th failing on drawdown (5%
stop specifically kept failing split 3, the 2013-2017-trained/2018-bear-tested window,
where BTC's daily swings are large enough that even a 5% stop gets run over before
recovering). The honest answer, stated plainly per the brief: **1% is not just adequate,
it's the best of the four levels tested, and wider stops are strictly worse** by this
project's validation bar — not a case of "they're roughly equivalent so pick any."

**Gap risk at all 4 stop levels: zero occurrences** — a change from Round 8's Yahoo-based
check, which found 11 gap-through events at the 3% stop (trivial in magnitude, 0.20pp
worst case, but nonzero). Kraken's own native daily candles show no gap-throughs at any
tested stop level in this data. Minor cross-source difference, doesn't change any
conclusion (Round 8's Yahoo-based gap risk was already negligible), but worth noting
plainly rather than silently picking whichever number looked cleaner.

**No config change.** Per the brief's own instruction for this exact outcome: when the
current default remains the best/only validated choice, leave it alone rather than
touching it just to have done something. Live bot config is unchanged —
`STOP_LOSS_PCT=1.0`, `TAKE_PROFIT_PCT=20.0`, `INTERVAL_MINUTES=1440`, `XBTUSD` — and was
already correct from Round 9, so nothing was restarted this round.

Ten rounds: Round 10 is the first round whose main effect is confirmation rather than a
change — it re-derived Round 8's finding on an independent, higher-quality data source
and used the wider grid to settle a real open question (does a wider stop help?) with a
clear no, rather than leaving it untested.

**Round 11 — `experiments_entry_style.py` (event-based vs. state-based entry, same
already-validated exit):** the live entry rule only buys on the specific day the fast SMA
crosses above the slow SMA (an *event*). This round tested a state-based alternative:
buy whenever flat and fast > slow (RSI filter unchanged), regardless of whether today is
a fresh cross — day 1 of an uptrend or day 47, same signal. Not round 7's "unconditional
entry" (no signal at all, already a disaster) — this still requires the real
trend-confirmation condition, just not the specific crossing moment. Same exit
throughout (1% SL / 20% TP), same 4 regime splits, same Kraken native daily data as
Round 10 — an apples-to-apples entry-rule swap, not a new grid search.

**Result: state-based entry failed on all 4 splits, decisively, and the failure mode is
exactly what the brief predicted.** Event-based: 80 total trades across the 4 splits, 0
immediate re-entries, profitable and passing on every split (as established since Round
8). State-based: **472 total trades — nearly 6x more — of which 364 (77%) were immediate
re-entries** (a new position opened within 1 day of the previous exit). Returns were
sharply negative in 3 of 4 splits (-7.5% to -57.0%) and barely positive nowhere; drawdowns
ballooned to 41-58% (vs. event-based's 10-14%). The mechanism is exactly the whipsaw risk
flagged going in: once the bot exits (TP or, far more often here, the 1% SL), if the
trend state is *still* technically bullish (fast still > slow, which is common right after
a stop-out during a choppy pullback within a broader uptrend), state-based entry re-buys
immediately — average 3.7 to 5.4 days between trades, vs. event-based's 42-50 days.
Each of those rapid re-entries pays the round-trip fee and risks another 1% stop, and in
choppy conditions they compound into large realized losses rather than one bounded one.

**No config change — exactly the "say so plainly, don't adopt it" case the brief
described.** State-based entry is worse on every axis tested (return, drawdown, pass
rate) and introduces a real new failure mode (fee-and-stop-loss-bleeding whipsaw) rather
than just being a wash. `strategy.py`'s event-based logic remains the only entry rule in
production; `state_based_entry` stays in `experiments_entry_style.py` as a tested,
documented dead end, not wired into the live bot. No processes touched, no `state.json`
reset — nothing about the live deployment changed this round.

Eleven rounds: this is the second round (after Round 10) whose result is "the current
config is already right, and here's why the alternative isn't" — worth exactly as much as
a round that changes something, since a validated *rejection* of a strategy this
plausible-sounding on paper prevents an actual future mistake.

**Round 12 — `experiments_exit_mechanisms.py` (two flexible take-profit mechanisms vs.
the fixed-20% baseline):** same event-based SMA/RSI entry and 1% stop-loss throughout
(unchanged — Round 11 settled the entry question); only the exit mechanism varies. Both
mechanisms were built as real engines, not approximations, and verified against
hand-computed synthetic-data cases before the full run (documented in the file).

**Mechanism 1, trailing profit lock:** once unrealized profit reaches 3%, stop using a
fixed target and trail behind the peak price since activation, exiting on a pullback of
2%/3%/5%/8% (tested as a grid) from that peak; a 50% hard ceiling exits immediately
regardless of trailing, and the original 1% stop-loss from entry stays active as the
downside floor at all times, before and after activation. **Result: never validates
robustly at any distance.** Trail=8% passed 3 of 4 splits (strong when it worked:
+38.8%, +15.4%, +27.5%) but failed split 2 outright; trail=2%/3% passed roughly half the
splits each with small losses on the others; trail=5% failed 3 of 4, including a -21.5%
split. No single trail distance passed all 4 splits — the mechanism's performance swings
too much with the specific distance chosen and the regime it's tested on to trust any one
setting.

**Mechanism 2, tiered partial exits:** sell fixed fractions of the original position as
profit milestones are hit (3%/10%/20%/50%), with the 1% stop-loss remaining active on
whatever fraction is still open. Two schedules tested: even (25% at each tier) and
front-loaded (40/30/20/10, banking more early). **Front-loaded failed on 2 of 4 splits**
(worse than baseline throughout). **Tiered-even validated on all 4 splits** — matching
baseline's own record — with average return and drawdown across splits both marginally
better than baseline (+15.08% vs. +14.93% avg return, 11.83% vs. 12.24% avg drawdown).
But split-by-split, it's a genuine mixed bag, not a clean win: tiered-even beats baseline
on 3 of 4 splits (notably split 2, +10.35% vs. +4.06%) but loses clearly on split 3
(+33.09% vs. baseline's +46.05%, a 13-point gap the other way) — a case of locking in
early profit at the cost of missing more of that split's bigger continuation. Fewer
completed episodes overall too (68 vs. 80), since a multi-tier position occupies the
"only one position at a time" slot for longer, on average, than a single-exit trade.

**No config change.** Trailing-lock is a clear no across every distance tested — not
close to the bar. Tiered-even is the harder call: it does validate on the same rigorous
"all 4 splits" bar as the current baseline, so it's not a rejection on the merits the way
state-based entry (Round 11) or wide stop-losses (Round 10) were — but the aggregate
edge is small (roughly a coin flip depending on which split is weighted more), and per
the brief's own tie-breaker ("if results are close, prefer the simpler mechanism unless
one is clearly better"): a single fixed take-profit trigger is simpler to reason about
and trust than a 4-tier partial-position engine for a margin this thin. Baseline stays
live. `simulate_trailing_lock` and `simulate_tiered_exit` remain in
`experiments_exit_mechanisms.py`, tested and documented, not wired into `paper_trader.py`
or `backtest.py`. No processes touched, no `state.json` reset.

Twelve rounds: the project's real edge at this point isn't any single mechanism, it's the
discipline of only ever adopting what clearly clears the bar — Round 12 found something
that's genuinely close to good enough and still correctly held it to the same standard as
everything that came before, rather than lowering the bar because a well-engineered
answer happened to arrive this time.

**Round 13 — `experiments_fast_entry.py` (does shrinking the SMA periods, combined with
a re-tuned stop-loss, get the bot toward daily action at 2-5%/day, and does the target
itself even make sense?):** the user asked to configure the strategy for daily trading,
first targeting 5-10% profit *per day*, then revised to 2-5%/day with a request to sweep
"as many stop loss percentages as possible" alongside it. Addressed the target's math
first, before touching any config: even 2%/day compounded is `(1.02)^252 ≈ 151x` per
year — not a tuning problem, a return no real backtestable strategy sustains. That part
of the ask isn't solvable by parameter search and wasn't treated as one. What IS
genuinely testable: whether a faster entry (shorter SMA periods, so crossovers fire more
often on daily bars) crossed with a wide stop-loss grid gets meaningfully closer to daily
action or to a 2-5%/day pace, while still clearing this project's standard bar. Grid: 5
SMA pairs (10/30 live baseline down to 2/5) x 12 stop-losses (0.25% to 20%) x the same 4
independent regime splits and Kraken native daily data as Round 10, TP held fixed at the
live 20% default throughout (Round 10 already exhaustively ruled out widening TP, so
re-testing it here would just repeat that result under a different entry speed without
adding information).

**Result: 60 combinations tested, only 2 pass all 4 splits, and both are the live
baseline's entry speed — no faster entry validates at any stop-loss level.**
`SMA(10,30) SL=1%` (the current live config) and `SMA(10,30) SL=3%` both pass all 4
splits (14.93% and 11.90% average test return) — consistent with Round 10's own finding
that 1% and 3% stops were the only levels that ever passed. Every faster entry (5/15,
5/10, 3/8, 2/5) failed all 4 splits at every one of the 12 stop-loss levels tested — 48
straight failures. Trade frequency does climb as SMA periods shrink (avg gap between
trades: 43.8 days at baseline down to as low as 7.9 days at SMA(2,5)/SL=0.25%) but even
that fastest setting is still ~8x slower than literal daily action, and it comes with
deeply negative average returns (-31.96%) — tightening the stop to compensate for a
noisier entry doesn't fix it, it mostly just adds more losing trades faster.

**The 2-5%/day target specifically: nowhere close, at any tested setting.** Computed an
implied daily compounding rate from each combo's actual per-trade expectancy and trade
cadence (a concrete number, not a proxy) — the highest implied rate found anywhere in the
entire 60-combo grid was **+0.075%/day** (`SMA(2,5) SL=3%`, which also fails validation
outright), about 27-67x short of the 2-5%/day ask. The two combos that actually pass
validation imply +0.035%/day each. For context, the single highest raw average-return
combo with no validation filter applied at all (`SMA(5,10) SL=20%`, 28.11% avg return,
fails 3 of 4 splits) still doesn't change this picture - it's a curve-fit average, not a
result that held up split-by-split, and even taking it at face value it's an annual-scale
number, not a daily one.

**No config change.** Neither the literal ask (daily action at 2-5%/day) nor a materially
softened version of it (faster entry + any of 12 stop-loss levels) survives contact with
this project's validation bar, and the achievable implied returns are 1-2 orders of
magnitude short of the target regardless of validation. This is now the third round
(after 9 and 11) to show that increasing this strategy's trade frequency, by any
mechanism tried so far (faster timeframe, looser entry filter, faster lookback window),
degrades it rather than improves it. `main.py` and the live `AiTradingBotPaperTrader`
task were not touched; still `STOP_LOSS_PCT=1.0`, `TAKE_PROFIT_PCT=20.0`,
`INTERVAL_MINUTES=1440`, `FAST_MA_PERIOD=10`, `SLOW_MA_PERIOD=30`.

Thirteen rounds: the honest answer to "make it trade daily for 2-5%/day" is that the
target itself is unachievable by any real strategy, and the closest achievable version of
the underlying ask (trade more often, at any of 60 tested entry-speed/stop-loss
combinations) not only fails this project's validation bar but implies real-world returns
1-2 orders of magnitude below what was asked for even before applying that bar.

**Round 14 — `experiments_scalp_exit.py` (ultra-tight take-profit, closing as soon as a
trade nets +0.05% AFTER fees, paired with a relaxed/wide stop-loss "for wiggle room,"
tested across BTC/ETH/SOL):** fee-adjusted math first: this bot's fee model charges
`FEE_PCT` (0.26%) on both legs, so netting +0.05% after fees actually requires a
**+0.5723% gross** price move — fees eat the large majority of the target by
construction. Tested that exact exit (gross TP≈0.57%) against a stop-loss grid from 3% to
30%, with both the baseline entry and Round 13's fastest entry (SMA(2,5)), across BTC's 4
splits, ETH's 2 splits, and SOL's 1 available split.

**Result: trades resolve in a median of 1 day exactly as designed, but the design is
tight-target/wide-stop — the inverse of Round 8's validated tight-stop/wide-target
asymmetry — and it shows.** BTC failed every stop-loss level on both entries (-7.5% to
-74%): win rates were high (62-98%) but never reached 100%, so every single BTC config
eventually ate one large stop-loss that erased dozens of tiny 0.05% wins at once. ETH and
SOL "passed" at wide stops (15-30% / 10-30% respectively) only because their specific test
windows happened to never trigger the stop even once (100% win rate) — and even then,
total return over the *entire multi-year window* was **+0.80% to +0.90%**, not per day.
Pairing the tight exit with the faster SMA(2,5) entry was catastrophic everywhere
(-26% to -74%) regardless of coin. A tight exit also does not, by itself, make the
event-based entry re-fire more often — median gap between trades stayed at the entry
signal's normal cadence (44-55 days for baseline, 8-9.5 days for the fast entry)
regardless of how fast each individual trade resolved.

**No config change.** The ETH/SOL "passes" are read as survivorship of one lucky historical
slice, not a validated edge — the honest mental model is "picking up pennies in front of a
steamroller": many tiny wins, one rare large loss erases the lot. Even taking the lucky
cases at face value, 0.8-0.9% total return across years is roughly 2-3 orders of magnitude
short of a 0.5%/day target. `main.py` untouched; still `STOP_LOSS_PCT=1.0`,
`TAKE_PROFIT_PCT=20.0`, `INTERVAL_MINUTES=1440`.

Fourteen rounds: two independent exit/entry redesigns aimed at the same "daily action,
higher return" goal (Round 13's faster entries, Round 14's tight-exit/wide-stop) have now
both failed, for different but equally structural reasons — increasing this strategy's
trading frequency keeps costing more in fees and tail risk than it gains in opportunity.

**Round 15 — `experiments_recent_window.py` + `experiments_full_history_wide.py`
(EXPLORATORY, methodology change per explicit user instruction — not validated under this
project's own bar):** the user asked to abandon multi-year regime testing going forward
("no reason to test the strat against the past") and instead run a very wide grid
(stop-loss 1%-50% x take-profit 1%-100%, 180 combos, x2 entry styles, x9 non-BTC coins —
ETH, SOL, XRP, ADA, LTC, DOGE, LINK, DOT, AVAX — 3,240 backtests total) against only the
**last 2 weeks** of live Kraken hourly data. This is flagged plainly, once, before
reporting results: a search this wide against a window this short (~336 hourly candles,
no train/test split, no independent regime) will surface some combo with a great-looking
number from the search size alone — Rounds 1-14's entire discipline exists specifically to
prevent mistaking that for a real edge (see Round 2). Results below are reported as "what
happened to work in these specific 14 days," not a forecast, and no live config changed
because of them.

**Result: only 172 of 3,240 combos (5.3%) even reached the literal "trades every day"
bar (>=1 completed trade/day), and of those, only 6 were profitable — all 6 on a single
coin (DOT), which happened to post a +16.54% move in this exact window.** For the two
coins the user specifically asked about, **every single combo that traded at daily
frequency lost money**: ETH's best daily-frequency result was -5.22% over 2 weeks, SOL's
was -12.62% — across the full 1-50%/1-100% grid, on both entry styles, there is no
(stop-loss, take-profit) pair for ETH or SOL that both trades daily and is profitable in
this window. The tightest, highest-frequency combos (SL=TP=1%, 4.5-7.5 trades/day) were
uniformly catastrophic (-29% to -49%) everywhere they were tested — the same fee/whipsaw
mechanism as every prior frequency experiment, just faster. The DOT "wins" that do exist
have win rates of 3-11% (one or two lucky large moves carrying many losses) — a coin-
specific, single-window coincidence, not a generalizable finding; it did not appear on any
other coin.

**Part B — the identical grid re-run against each coin's maximum available multi-year
history, using this project's normal regime-split discipline (BTC's 4 splits from Round
10, ETH/XRP/ADA/LTC/DOGE/LINK's 2 splits from Round 2 onward, SOL/DOT/AVAX's 1 split each
given their ~5-6 year history), specifically to give a side-by-side against Part A's
2-week search on the exact same grid (6,840 backtests: 10 coins x 2 entry styles x up to
4 splits x 180 combos):**

**Result: 237 combos passed on every split available for their coin — and every single
one of them used the baseline signal entry, zero used unconditional/always-buy entry
(0 of 3,420 unconditional backtests passed).** That's the same finding as Round 7
(unconditional entry is a disaster) now confirmed independently across all 10 coins, not
just BTC/ETH. 9 of the 10 coins had at least one genuinely validated combo (ADA, AVAX,
BTC, DOGE, ETH, LINK, LTC, SOL, XRP) — real, repeatable, infrequent-trading setups exist
for most of these coins, just not fast or frequent ones. The live BTC config
(SL=1%/TP=20%) is among the 237, with per-split numbers matching Round 8/10's Yahoo/Kraken
results almost exactly (9.30%/4.06%/46.05%/0.33% return across the 4 splits) - a useful
cross-check that this round's engine is computing correctly, not just a new source of
noise.

**The sharpest finding is which coin has ZERO validated combos: DOT — the exact coin that
produced Part A's only "profitable, daily-trading" results.** Every one of Part A's 6
profitable daily-frequency combos was DOT riding one lucky 2-week pump; tested against
DOT's actual multi-year history (2020-2026), not one single (stop-loss, take-profit)
combination out of 180 held up. This is Rounds 1-14's whole discipline demonstrated in one
side-by-side, on data from this exact conversation: the 2-week search's "winner" is
precisely the coin real validation rejects outright, while the coins that validate for
real (BTC, ETH, SOL, etc.) showed nothing special in the 2-week window.

Fifteen rounds: Part A and Part B together are the clearest demonstration yet, inside this
project's own data, of exactly the failure mode Round 2 first named — a wide enough search
against a short enough window turns up a "winner" by chance, and that "winner" (DOT) is
the one coin that fails outright once tested against its real multi-year history, while
coins with a genuine repeatable pattern (still infrequent-trading, still not daily action)
showed nothing notable in the short window at all.

**Round 16 — `experiments_pyramiding.py` (pyramiding: allowing multiple concurrent
positions in the same coin, instead of this project's standing "one position at a time"
rule):** motivated by the live bots' infrequent trading (BTC ~6.5-10 weeks between trades,
ETH/DOGE 11-21 weeks on their wider live configs) — does letting the bot hold 2/3/5
positions at once, instead of dropping a BUY signal that arrives while already in a
trade, help? Tested against each coin's actual LIVE config (BTC SL=1%/TP=20%, ETH
SL=10%/TP=60%, DOGE SL=15%/TP=30%), same regime splits as always, capital split equally
across open slots.

**Diagnostic first: how often is a signal actually being dropped today?** BTC: 0% (its
tight exit resolves before the next signal ever arrives - pyramiding literally cannot
help BTC's current config). ETH: 17-33% of signals dropped. DOGE: 9-25% dropped. So the
premise only applies to ETH/DOGE.

**Result: return drops sharply as concurrency increases, for every coin, despite
capturing more of the dropped signals.** BTC 14.93%→7.91%→5.37%→3.27% (1→2→3→5
positions); ETH 174.10%→116.62%→82.85%→47.94%; DOGE 514.75%→249.14%→136.37%→69.74%. All
still technically pass the standalone bar at every concurrency level, but every one is
worse than single-position on the metric that matters. Mechanism: this strategy's edge is
a low win rate with rare, large asymmetric wins (validated since Round 8); splitting
capital N ways means each big win only compounds a 1/N slice of the account, diluting the
very thing the edge depends on, faster than the extra captured trades can compensate.
Risk-adjusted (return/drawdown) is roughly flat for BTC, mixed for ETH, and gets strictly
worse for DOGE as concurrency rises - not a real risk improvement, just smaller stakes.

**Follow-up (same round): re-target at a 5% take-profit instead, per user request.**
Checked the premise before running anything: **no stop-loss level (of the full 1%-50%
grid) validates a 5% target for BTC at all** - it fails outright everywhere, so BTC is
excluded from this variant entirely, not substituted with an unvalidated config. ETH only
validates a 5% target paired with a very wide 40-50% stop, and even then with a 100% win
rate in both historical splits (the stop never once triggered) - the same
survivorship-flavored pattern flagged in Round 14, thin evidence dressed as a clean
result. DOGE validates a 5% target against a 15% stop with a more genuine track record
(88-90% win rate, i.e. real losses did occur and it still held up).

**Same conclusion holds at the tighter target: concurrency still hurts, not helps.** ETH
(SL=40/TP=5): 30.59% (1 position) → -2.67% (2, actually FAILS validation) → 4.61% (3) →
5.24% (5) - fragile and inconsistent across concurrency levels, on top of already-thin
single-position evidence. DOGE (SL=15/TP=5): 47.60% → 13.20% → 10.58% → 6.79%, the same
smooth decline as every other config tested. Retargeting the take-profit doesn't change
the underlying mechanism (diluting rare big wins) - it just changes which coins/stops
happen to validate at all.

**Also caught and fixed a real bug in the new pyramiding engine during this follow-up:**
initial implementation checked entry-signal capacity using the position count *after*
processing that same bar's exits, which let a position that exited today be immediately
replaced same-day - an extra trade `simulate_wide_tp` (the existing, trusted single-
position engine) would never produce. Caught via a direct A/B mismatch on ETH SL=40/TP=5
(12 trades/-14% vs. the correct 11 trades/+44%) when a fresh config finally exercised the
edge case; the three original live configs' backtests happened not to trigger it and were
unaffected (re-verified split-by-split against `simulate_wide_tp` after the fix - exact
match on all 10 split/config combinations). Fixed by gating each bar's entry on capacity
as of the *start* of that bar, before its own exits are applied.

**No config change** in either part - all three live bots remain single-position, exactly
as before.

Sixteen rounds: a plausible-sounding fix for "the bot doesn't trade often enough"
(pyramiding) failed under two different take-profit targets, for a coherent, consistent
reason both times - and separately, this round is a reminder to A/B new backtest engines
against the trusted one on more than just the configs already in hand, since a bug can
hide behind coincidentally-unaffected inputs.

**Round 17 — `experiments_eth_doge_full_search.py` (exhaustive search: entry speed x
full stop-loss/take-profit grid, ETH and DOGE only, per explicit request to find the best
"frequent" and "longer-term" configs for each):** 5 SMA period pairs (10/30 down to 2/5)
x 12 stop-losses (1%-50%) x 15 take-profits (1%-100%) = 900 combos per coin, x2
independent regime splits each = 3,600 backtests total. Same validation bar as always
(profitable, >=5 trades, <40% drawdown, passes BOTH splits).

**Result: 31 combos validated for ETH, 61 for DOGE - and the two coins landed on
opposite answers to "does a faster entry cost you return."** For **ETH**, the fastest
validated config (`SMA(5,10) SL=2%/TP=50%`) beat the previous live config
(`SMA(10,30) SL=10%/TP=60%`) on BOTH axes at once: avg return 286.30% vs. 174.10%, AND
~15.5-day trade cadence vs. ~91 days (~6x more frequent) - split-checked individually
(split1: +327% return/60 trades/35.4% dd; split2: +245% return/35 trades/27.1% dd) and
confirmed not a fluke, with negligible gap-through risk (1 occurrence, +0.10pp). For
**DOGE**, faster genuinely costs return: the fastest validated config
(`SMA(5,10) SL=2%/TP=100%`, ~18.8-day cadence, 0 gap-through events) returns 183.53% vs.
the patient `SMA(10,30) SL=15%/TP=30%` config's 514.75% - a real tradeoff, not a search
artifact (both individually verified: frequent split1 +80.4%/61 trades/39.7% dd, split2
+286.7%/31 trades/38.1% dd). Same established caveat holds for every new combo found:
beats buy&hold in the 2022-25 chop, loses to it in the 2020-21 monster bull (e.g. ETH's
new pick: +245% vs. buy&hold's +2,715% in that split).

**Shipped to the live bots, per explicit instruction:** ETH updated to the new best-
overall config (`FAST_MA_PERIOD=5`, `SLOW_MA_PERIOD=10`, `STOP_LOSS_PCT=2.0`,
`TAKE_PROFIT_PCT=50.0`) since it's strictly better on every axis measured. DOGE updated
to the frequent-trade config (`FAST_MA_PERIOD=5`, `SLOW_MA_PERIOD=10`,
`STOP_LOSS_PCT=2.0`, `TAKE_PROFIT_PCT=100.0`) per explicit request for frequency over
raw return, with the return tradeoff stated plainly rather than glossed over. Both bots
were flat with no open position at the time, so both restarted clean - no `state.json`
reset was needed (none existed yet). `main.py`'s BTC config is untouched.

Seventeen rounds: this is the first round where "trade faster" wins outright rather than
losing, for one specific coin (ETH) under one specific entry speed - a reminder that
Round 9/11/13's "faster trading always hurts" finding was about *this strategy's specific
mechanism on BTC*, not a universal law; a different, more volatile asset can have real
structure at a different, faster lookback. DOGE, tested the same way, confirms the
original finding still holds there - the lesson generalizes as "check per-asset," not
"faster never works" or "faster always works."

**Round 18 — `experiments_daily_push.py` (how much further can ETH/DOGE push toward
literal daily trading before it stops being safe?):** tested three ways to push past
Round 17's SMA(5,10) picks, exit held fixed at each coin's live config (ETH SL=2%/TP=50%,
DOGE SL=2%/TP=100%): even faster event-based entries (down to SMA(2,3)), state-based
entry (Round 11's mechanism, re-tested here since Round 17 showed BTC's rules don't
automatically transfer), and unconditional entry (Round 7's mechanism).

**Result: every single push toward "more frequent" broke the 40% drawdown cap, for both
coins, despite several posting bigger average returns than the current live config.**
ETH: SMA(3,5) 54.39% dd, SMA(2,3) 621.85% return but 47.62% dd, unconditional entry
64.16% dd. DOGE: SMA(2,4) showed a 904.17% average return but 62.78% drawdown. State-
based entry failed both coins too (43-70% dd), confirming Round 11's finding generalizes
here even though pure entry-speed didn't. Unconditional entry got closest to daily
(~2.0-day median gap) but at 64-79% drawdown - a real, historically-demonstrated risk of
losing well over half the account. **No config change** - Round 17's SMA(5,10) configs
remain the fastest-trading setups found that are actually safe for either coin.

**Round 19 — `experiments_daily_small_wins.py` (a genuinely new corner: small stop PAIRED
with small target, instead of the wide-target-always designs used everywhere else, per
explicit request that "wins don't need to be huge"):** every prior frequent-trading
attempt paired a fast entry with either the same wide target as the patient config
(Round 18) or a tiny target against a WIDE stop (Round 14, the failed "steamroller"
pattern). Nobody had tested small-stop + small-target together. Grid: 6 entry speeds x
6 stop-losses (0.5%-5%) x 7 take-profits (1%-10%).

**Result: found safer alternatives for both coins, trading off return for materially
lower drawdown at similar-or-same cadence.** ETH: `SMA(5,8) SL=2%/TP=10%` - 13.0-day
cadence (vs. Round 17's 15.5 days), 75.31% return (vs. 286.30%), but only **23.16%
drawdown** (vs. 31.25%) - a real safety improvement, not just a smaller number.
DOGE needed a wider small-grid (its volatility is much larger than ETH's) to find
anything: `SMA(5,10) SL=2%/TP=20%` - same 18.8-day cadence as the live config, 31.61%
return (vs. 183.53%), but **30.87% drawdown** (vs. 38.92%). Both split-verified, 0
gap-through events. **Not shipped to either live bot this round** - superseded by
Round 20's search before a decision was made; DOGE in particular is still worth
revisiting since Round 20 didn't find anything better for it (see below).

**Round 20 — `experiments_every_angle.py` (the full indicator toolkit from Round 5 -
MACD, Stochastic, SuperTrend, ADX-filtered, rolling-VWAP, Ichimoku - re-evaluated for
cadence and safety on ETH/DOGE specifically, per explicit "search every angle" request):**
trend-family indicators keep their natural reverse-signal exit (forcing a tight TP/SL onto
a trend-following entry was the bug that sank Round 2, not repeated here); mean-reversion
indicators (Stochastic, VWAP) pair with a small TP/SL grid, their natural pairing.

**Result: every trend-following indicator failed safety despite the biggest headline
numbers this project has ever produced** - SuperTrend on DOGE averaged a 5,215% return,
Ichimoku 3,553%, both with 66-82% drawdown. Named here explicitly as the strongest
example yet of "big number, not a real result." Rolling-VWAP reversion failed outright
for both coins. **Stochastic mean-reversion was a genuine find for ETH, not for DOGE:**
ETH `stochastic_reversion(k=21, oversold=30)` + `TP=10%/SL=1%` beats every SMA-crossover
config tested on BOTH axes - 5.5-7.5 day median gap (fastest safe ETH config found,
period) and 8.9-14.9% drawdown (safest ETH config found, period), 21.86% avg return.
Split-verified (split1: +7.1%/19.7% dd/86 trades; split2: +36.6%/10.1% dd/21 trades) with
**0 gap-through events** on the tight 1% stop. DOGE's only validated Stochastic combo
(`k=21, oversold=20`, `TP=10%/SL=5%`, 22.5-day cadence, 32.5% dd, 14.21% return) does
**not** beat Round 19's `SMA(5,10) SL=2%/TP=20%` pick on any axis, so DOGE's answer from
this round is "no improvement found."

**Shipped:** ETH switched to the Stochastic strategy - this required actual code changes,
not just `.env` values, since the live bot's `strategy.py` only implemented SMA-crossover.
`live_eth/strategy.py` was rewritten with a new `generate_signal` computing 21-day %K/%D
and entering on an oversold crossover; `live_eth/config.py` gained `STOCH_K_PERIOD`/
`STOCH_OVERSOLD` and dropped the now-unused `FAST_MA_PERIOD`/`SLOW_MA_PERIOD`/`RSI_*`
fields entirely rather than leaving them as dead config. `live_eth/.env` set to
`STOCH_K_PERIOD=21`, `STOCH_OVERSOLD=30`, `STOP_LOSS_PCT=1.0`, `TAKE_PROFIT_PCT=10.0`.
Smoke-tested against live Kraken data before restarting (`generate_signal` ran cleanly,
returned a real signal) - bot was flat with no open position, so the restart was clean.
DOGE's live config is **unchanged** (still Round 17's `SMA(5,10) SL=2%/TP=100%`) - Round
19's safer alternative was found but not yet shipped, pending a decision.

**Round 21 — `experiments_every_angle_btc.py` (the same indicator-toolkit search, applied
to BTC on its proper 4-independent-split Kraken-native data, this project's most
rigorous validation):** same trend-family/mean-reversion split, same small TP/SL grid,
same cadence tracking.

**Result: BTC gets a decisively different answer than ETH from the identical search.**
Every trend-following indicator failed the 4-split bar (drawdowns 33-53%, again despite
returns up to 169%). Only one combo validated across all 4 splits at all:
`stochastic_reversion(k=21, oversold=20)` + `TP=10%/SL=1%` - 10.94% avg return, 13.62%
drawdown (genuinely safe), but **61.5-day median gap** - slower than BTC's current live
config (~44-73 days per split), not faster, and lower return. **No config change** - the
same search that found a real improvement for ETH found nothing for BTC; `main.py`'s
config remains `STOP_LOSS_PCT=1.0`, `TAKE_PROFIT_PCT=20.0`, SMA(10,30) crossover entry.

Twenty-one rounds: the "every angle" search now has three different answers across three
coins tested the identical way - a genuine improvement for ETH, no improvement for BTC,
and an as-yet-undecided safer-but-lower-return alternative for DOGE - which is itself the
finding: there is no single "does X help" answer independent of which asset X is applied
to, and the only way to know is to actually run the same rigorous test per asset rather
than assume one coin's result transfers.

**Round 22 — `experiments_atr_exit.py` (ATR-based/volatility-scaled exits instead of a
fixed percentage, sourced from web research into open-source trading-bot repos that pair
ATR exits with MA-crossover entries):** every exit in this project's prior 21 rounds has
been a fixed percentage regardless of how volatile the coin is at the moment of entry.
Tested each coin's live entry unchanged (BTC SMA(10,30)+RSI, ETH Stochastic(21,30), DOGE
SMA(5,10)+RSI) with the exit replaced by `entry ± multiplier x ATR`, sweeping ATR period
and SL/TP multipliers.

**Result: no real win anywhere, and one result that looked good until checked closely.**
BTC: only one combo passed (12.45% return, 33.78% drawdown) - worse than the live
config's 12.24% drawdown for similar return, not an improvement. DOGE: zero combos
validated at all. ETH: two combos technically passed, but the better one
(`ATR(14) SL=0.5x/TP=7x`, 292.59% avg return) had a **39.87% drawdown on split1 - 0.13
percentage points from failing the 40% cap outright**. That's a coincidental near-miss,
not a robust result, and is called out here specifically as an example of a "pass" that
shouldn't be trusted despite clearing the bar. **No config change.**

**Round 23 — `experiments_adx_filter.py` (require ADX above a threshold as an
additional gate on top of each coin's already-validated entry+exit, sitting out
non-trending periods entirely - not a new entry signal, which was already tried and
failed in Round 5/20):** tested ADX thresholds 15/20/25/30 against each coin's live
config, unchanged otherwise.

**Result: helps ETH, hurts BTC, mixed-to-worse for DOGE - a real per-asset difference,
not a universal answer, consistent with Round 17-21's pattern.** BTC: every threshold
made things worse (return dropped from 14.93% to as low as -9.39%). DOGE: most
thresholds hurt (ADX>15 raised drawdown from 38.92% to 47.21%); only the most
restrictive (ADX>30) passed, and only by giving up more than half the trades and 47% of
the return (96.31% vs. 183.53%) - not a good trade given DOGE's stated profit+frequency
priority. **ETH got a genuine, low-risk improvement:** `ADX>15` raised average return
from 21.86% to 26.11% at the *same* 14.87% drawdown, trimming only ~2.5 trades per split
on average. Split-checked: split2 was untouched entirely (every Stochastic signal there
already had ADX>15); split1 simply dropped ~5 of its weakest trades (86 -> 81), pushing
return from 7.14% to 15.64% at unchanged drawdown. A small, verified, low-cost
refinement - not a big claim, but a real one.

**Round 24 — `experiments_walk_forward_v2.py` (proper walk-forward optimization -
train on a rolling 2-year window, test on the following untouched 6 months, slide
forward 6 months, repeat across each coin's full history - a stronger defense against
overfitting than this project's usual 2-4 fixed splits, per the same web research that
surfaced the ATR-exit idea):** for each coin, compared continuously re-optimizing
SL/TP per fold against simply running the already-validated static live config across
the identical test folds, with no re-tuning at all.

**Result: re-optimizing loses to the static config, for all three coins, decisively -
one of the most reassuring findings in this project's history.** BTC: static config
4.53% avg drawdown/fold vs. re-optimized 16.36%, profitable in 33% of folds vs. 29%.
ETH: static 7.88% drawdown vs. re-optimized 17.84%, profitable in **69%** of folds vs.
38%. DOGE: static 23.25% drawdown vs. re-optimized 26.78%, profitable in 54% of folds
vs. 46%. Continuously chasing "the best recent parameters" reliably made every coin's
drawdown worse and its fold-level reliability lower - a textbook overfitting-to-recent-
data trap. The practical meaning: this project's already-validated fixed configs are not
fragile artifacts of the specific 2-4 splits they were tested on - they hold up better
under a materially more rigorous rolling-window methodology than a strategy that keeps
re-tuning itself would. **No config change** - this round is a validation of the
existing approach, not a new one. (Side note, not a concern: none of the three coins'
static configs are profitable in a strict majority of individual 6-month windows except
ETH - expected given how infrequently these strategies trade; a short window often
contains 0-3 trades and is dominated by variance, which is exactly why this project has
always evaluated on multi-year windows, not 6-month ones, for its actual validation bar.)

Twenty-four rounds: three research-sourced ideas (ATR exits, ADX regime filtering,
walk-forward optimization) were tested with the same rigor as everything homegrown in
this project - one gave a small real win (ETH's ADX filter), one gave nothing (ATR
exits), and one validated the entire existing methodology rather than replacing it
(walk-forward confirming the static configs beat continuous re-optimization).

**Round 25 — `experiments_new_strategies.py` (two more web-research-sourced strategy
families, neither tested in Rounds 1-24: Bollinger Band Squeeze Breakout and simplified
Smart Money Concepts / bullish Fair Value Gap mitigation):** Squeeze Breakout trades a
volatility contraction resolving into a directional move - genuinely different mechanism
from Round 20's Bollinger mean-reversion test (betting on continuation, not reversion).
FVG is a popular retail-crypto pattern (a 3-candle price gap, entered when price returns
to "mitigate" the gap and then continues); outside research already found a rigorous
independent test of the broader SMC/ICT family got "0 of 648 backtests" beating
buy-and-hold on stock indices - tested here anyway on crypto specifically, with this
project's own standard wide SL/TP grid and validation bar, rather than taking that
outside finding at face value.

**Result: Squeeze Breakout validates modestly for all three coins (5-20 combos each) but
none clearly beat what's already live. FVG is a genuinely strong, well-verified result -
and outperforms the outside research's pessimistic finding, at least for crypto.** All
three coins' best FVG picks were checked split-by-split (not just averaged) with zero
gap-through events anywhere, and cadence was computed directly (median days between
trades), not estimated:
- **DOGE: a clean win on every axis** - `SL=3%/TP=30%` beats the current live config on
  return (245.63% vs. 183.53%), drawdown (30.08% vs. 38.92%), AND frequency (13-16.5 day
  median gap vs. 18.8 days). No tradeoff.
- **ETH: a real tradeoff, not a clean win** - `SL=1%/TP=50%` returns far more (285.15%
  vs. 26.11%) but trades *less* often (13-17 days vs. the current 5.5-7.5 days) and
  carries more risk (30.69% vs. 14.87% drawdown).
- **BTC: also a tradeoff** - `SL=1%/TP=100%` returns more on average (68.91% vs. 14.93%)
  but at 2.5x the drawdown (28-33% vs. 12.24%), and is actually worse than the current
  config on 2 of its 4 individual splits despite winning on average - a higher-variance
  alternative, not a strict improvement.

**Shipped: DOGE switched to the FVG strategy** (`SL=3%/TP=30%`) - the only one of the
three with no tradeoff to weigh. BTC and ETH options are documented but not adopted this
round, pending a decision on whether the extra return is worth the extra risk/reduced
frequency for either.

Twenty-five rounds: the outside research that said this pattern family doesn't work was
wrong for crypto, at least for DOGE - a reminder that even a well-sourced outside
finding needs the same in-house verification as anything else before trusting it, in
either direction.

**Round 26 — `experiments_trailing_ratchet.py` (a staged trailing-stop ratchet, per an
exact user spec: trail 1.5% behind the peak every 3% of profit, with hard floor tiers
locking in at 5%->3.5%, 7.5%->5%, 10%->7%, "continue this trend," checked on every price
update rather than just candle close, fee-aware, with the current-price-vs-high-water-
mark question left for this round to answer):** BACKTEST ONLY, nothing wired into any
live bot or config, per explicit instruction to run it as a test first.

**Three assumptions had to be made explicit before any number below means anything.**
(1) The three given milestone/floor pairs don't sit on one clean arithmetic line, but
they do fit floor = 0.70 x milestone almost exactly (exact at 5%->3.5% and 10%->7%, 0.25
points off the user's stated 7.5%->5%) - extended forward at the same 2.5%-profit
spacing (12.5%->8.75%, 15%->10.5%, ...). (2) The milestone table is treated as a hard
floor that the continuously-running 1.5%/3% trail can exceed but never fall below -
effective floor = max(milestone floor, trailing floor). (3) Genuine intrabar data only
exists in this project for BTC (Round 9's 1-minute Kraken CSV); ETH and DOGE have never
had anything finer than Yahoo daily candles anywhere in this project's history. The
primary run below uses each coin's daily high/low as the closest honest approximation
for ETH/DOGE (same convention Round 12 used); a separate BTC-only true-1-minute
sensitivity check quantifies what that approximation costs.

**The noise-handling ask and the current-price-vs-HWM question turned out to be the same
question, answered together:** "HWM" tracking updates the peak from each bar's intrabar
HIGH (reacts to any wick, however brief); "close-confirmed" tracking only updates the
peak from the bar's CLOSE (requires the day to actually finish at a level). A synthetic
case built specifically to separate them (a bar that wicks to +12% intrabar but closes at
+1%) confirmed the engine does the right thing before any real data was touched: the HWM
variant ratchets the floor up past 5% on the wick alone; the close-confirmed variant
never engages the ratchet at all and falls back to the plain entry stop-loss, exactly as
each design should. **Close-confirmed is the noise-resistant variant by construction, and
it also won on real data everywhere it mattered** - it beat the HWM variant's return on
every one of the 8 (coin x split) real-data runs.

**Result: coin-specific, same pattern as Rounds 17-23 - a real win for ETH, a clear no
for BTC and DOGE.** BTC: neither variant validates on all 4 splits (close-confirmed
passes 3 of 4 but fails the most recent split at -6.56% against a baseline that itself
barely cleared breakeven there; HWM passes only 1 of 4); avg return 2.73% (close) / -1.32%
(HWM) vs. the live baseline's 14.93%. **ETH: the close-confirmed ratchet validates on
both splits AND beats the live baseline on both return and drawdown** - 61.84% avg return
/ 13.95% avg drawdown vs. the baseline's 26.11% / 14.87%, split-checked individually
(split1: 82.10% vs baseline's 15.64%; split2: 41.58% vs 36.58%), 0 gap-through risk beyond
what the underlying SL already carries. HWM-variant ETH also validates but trails the
baseline (17.91% avg return) - the noise-resistant variant isn't just safer here, it's
the one that actually performs. **DOGE: fails outright, both variants** - HWM passes
split1 but loses -29.88% on split2 (vs. baseline's +34.26%); close-confirmed fails both
splits. FVG's entry produces fewer, larger, faster-resolving winning trades than the
mean-reversion entries this mechanism was designed around, and DOGE's own volatility
means the 1.5%/3% trail steps get crossed and re-crossed inside single large daily
candles in a way that doesn't suit its entry's shape.

**A finding worth stating plainly rather than burying: in this data, the milestone
ladder - the actual novel part of the spec - rarely engages at all.** `avg_locked_at_exit`
across almost every (coin, split) combination sits between 0.3% and 2.4%, well under the
5% first milestone; most trades exit off the plain original stop-loss or a single 3%
base-trail step long before any tier "raise the floor to X%" ever fires. The one clear
exception is ETH split2, where the milestone table does get exercised (avg locked ~2.0-
2.35%, still below 5% on average but closer) - consistent with that split containing
2021's monster ETH bull run, the kind of move large enough to actually reach multiple
milestones. The mechanism as specified is mostly a base 1.5%/3% trail in this data, not
the tiered ladder it was designed to be, for two of the three coins' actual trade sizes.

**BTC-only true-1-minute sensitivity check** (one split, 2023-01-01..2026-06-30, 25
entries, daily entry signal unchanged - only the exit-check resolution varies): running
the identical close-confirmed logic against real 1-minute Kraken data instead of daily
high/low bars moved the average locked-in exit by -0.111 percentage points per trade
versus the daily-bar approximation, with one trade differing by 4.95 points. Small on
average, not small on the worst case - a real, measurable cost of the "candle close only"
approximation the user flagged going in, concentrated in the fast-moving trades that
prompted the concern in the first place.

**Fees:** round-trip fees are already deducted in the actual cash/equity accounting on
both legs, same model as every engine in this project; `avg_net_locked_pct` additionally
reports each locked floor net of the ~0.52% round-trip fee so a "5% locked in" reading
isn't mistaken for "5% kept."

**No config change - this was run explicitly as a test before touching anything, per
instruction.** ETH's close-confirmed ratchet is a real, validated, split-checked
improvement over the current live config and is the one candidate from this round worth
a deliberate decision to ship; BTC and DOGE are not. **Separately, and left unresolved
this round on purpose:** even if ETH's ratchet is adopted, "check on every price update"
is not actually achievable with the live bots as they exist today - `main.py` and the
`live_*` bots poll on a timer (`POLL_INTERVAL_SECONDS`, currently 60s) and act on Kraken's
latest CLOSED candle via `fetch_ohlc`, not a continuous price stream; genuinely
tick-by-tick checking would need a different price source (e.g. Kraken's public
WebSocket ticker feed) and a rewrite of `paper_trader.py`'s tick loop, not just a new
exit-math function - a real, separate piece of work, not a side effect of shipping this
mechanism. Nothing about that gap was addressed this round.

Twenty-six rounds: the closest result yet to a genuinely new, validated exit mechanism -
real for one coin, out of three, on the same rigor this project has always required -
and, separately, a clean demonstration of why "run it as a test first" was the right
instruction: two of three coins would have been a mistake to ship on the spec as first
described, and even the one that works exposed a live-infrastructure gap (tick-level
polling doesn't exist yet) that only surfaced by actually building and running the thing
rather than reasoning about it in the abstract.

**Round 26 follow-up — is the ETH win robust to the guessed tier formula, and does the
ladder actually do anything?** The user asked both questions directly before any ship
decision. Answered by adding two targeted checks to `experiments_trailing_ratchet.py`
(`run_tier_formula_sensitivity`, `run_ladder_value_analysis`) rather than re-running the
full three-coin grid.

**Tier formula: genuinely robust, but not for a flattering reason.** Three different,
equally-plausible extrapolations of the user's 3 stated tier points were implemented and
re-run against ETH's close-confirmed variant: (A) the original `floor = 0.70 x milestone`
guess; (B) a least-squares linear fit of the milestone-to-floor "give-back gap" through
the exact 3 stated points (`gap = 0.3 x milestone + 0.083`); (C) the simplest literal
reading — keep the user's 3 stated floors (3.5/5/7) exactly, then continue past 10% using
the last stated increment (+2.0 per tier) as a constant step. **All three produced
byte-identical results on both ETH splits** — same trade count, same return to the
hundredth of a percent, same drawdown. That's not because the formulas agree with each
other (they diverge by design past the 10% milestone) — it's because none of them ever
became the *binding* floor. Per-trade inspection of all 19 ETH trades whose peak profit
ever reached the first 5% milestone confirmed this exactly: comparing "ladder on" against
"ladder off, base trail only" trade-by-trade (matched on entry time) showed a **0.0
percentage-point difference on every single one of the 19 matched trades** — not "close to
zero," literally identical locked-exit values down to six decimal places. The reason is
structural, not a data fluke: the base trail's real-world give-back (it re-anchors to the
current peak at each fresh 3%-of-profit crossing, so its gap from peak rarely exceeds
~1.5-2 points in practice) is consistently tighter than the milestone ladder's own
~25-30%-of-milestone give-back at these profit magnitudes, for every trade tested. The
trail structurally dominates the ladder at ETH's actual move sizes, so of course changing
the ladder's formula changes nothing — there's nothing for it to change.

**So: the tiered ladder is not doing any of the work in ETH's validated win.** The entire
61.84%-avg-return / 13.95%-avg-drawdown result attributed to "the ratchet" in the section
above is fully explained by the simple "trail 1.5% behind peak, re-anchored every 3%"
base rule alone — the 5%/7.5%/10%-tier floor system the user actually asked to have
built, tested, and "polished" contributes measurably nothing on this coin's data, at
least at the profit magnitudes ETH's trades reach before something else closes them.
This should change how the recommendation is read: **what's actually validated for ETH
is the plain 1.5%-every-3% trailing stop, not the staged ladder.** Shipping "the ratchet"
and shipping "just the base trail" would produce an identical live result for ETH; the
extra tier-table complexity is currently inert weight, not a proven feature, and — per
this project's own tie-breaker used in Round 12 ("prefer the simpler mechanism unless one
is clearly better") — the base trail alone is the more honest thing to call validated
here, not the full staged design as specified.

**Round 26 follow-up 2 — can the ladder be redesigned to actually bind, and if so does it
help?** User's call after the above: redesign with tighter floors and re-test rather than
just ship the plain trail. Root cause of the inertness was structural: Formulas A/B/C all
give back ~25-30% of the milestone level (e.g. ~3pp at the 10% tier), looser than the base
trail's flat 1.5pp giveback, so the trail always wins the `max()`. Three new designs with
smaller giveback than 1.5pp, added to `experiments_trailing_ratchet.py`
(`milestone_floor_tight_static`, `milestone_floor_peak_relative`,
`milestone_floor_peak_relative_aggressive`), instrumented with a per-trade `milestone_bound`
flag so "does it bind" is measured, not asserted:

- **Design D** (tight static): same milestone boundaries as before, but a flat 0.75pp
  giveback from the boundary itself instead of ~25-30%. Only wins in the gap between one
  3%-trail-step and the next (the trail's anchor is stale there).
- **Design E** (peak-relative, shrinking 1.0/0.75/0.5pp): floor tracks the *live* peak
  continuously, giveback shrinks per tier — structurally dominates the trail on every bar
  once a milestone is active, not just in the inter-step gap.
- **Design F**: Design E with even smaller givebacks (0.5/0.35/0.25pp), testing whether
  squeezing further keeps helping.

All three now genuinely bind (synthetic case 5 confirms Design E overriding a looser trail
floor by design, not by accident). On ETH's same 2 validated splits:

| Design | Binds on | avg return | avg drawdown | vs. base-trail-only (61.84%/13.95%dd) |
|---|---|---|---|---|
| D (tight static, 0.75pp) | 10/104 trades (9.6%) | **64.15%** | 13.95% (tied) | marginally better return, same drawdown |
| E (peak-relative, shrinking) | 19/104 trades (18.3%) | 44.46% | 12.88% | worse return, better drawdown |
| F (peak-relative, aggressive) | 19/104 trades (18.3%) | 35.46% | 12.43% | worse return, better drawdown |

All three still validate on both splits. **Only Design D comes out ahead of the plain base
trail, and only barely** (+2.3pp average return, same drawdown, on 10 of 104 trades where
it actually mattered). Designs E/F bind more often but return less — the classic
tighter-stop tradeoff of cutting winners short outweighs the drawdown benefit at these
giveback sizes, on this data. Win rate was identical (26.0%) across every design, base
trail, and both tighter variants — none of these changes flip a trade from loser to
winner, they only resize how much winners keep.

**Honest bottom line**: a genuinely-binding ladder is possible (Design D proves the
concept isn't dead), but the improvement over the simple base trail is small, affects
under 10% of trades, and is measured on the same 2-split ETH sample as everything else in
this round — not enough to call it a clearly-better-than-simpler win under this project's
own Round 12 tie-breaker. Recommendation stands: **the plain 1.5%-every-3% base trail is
what's actually validated for ETH**; Design D is a documented, working option if the user
wants the extra complexity for a ~2pp edge, not a mechanism this round can call clearly
superior.

No config change; still backtest only.

**Round 26 follow-up 3 — polish Design D's exact numbers, or is that overfitting 2
splits?** User's ask: "make sure the floor is moved to the best tp% given where the bot is
at in the trade" rather than accept the hand-picked 0.75pp-giveback/2.5%-step guess. Built
a proper grid search in `experiments_trailing_ratchet.py`
(`run_design_d_grid_search`) — 107 configs total: 32 static-giveback variants (giveback
0.25-2.0pp x milestone spacing 2-4%, the generalized form of Design D) plus 75
linear-giveback variants (a floor that shrinks its giveback per tier crossed, the
generalized form of Designs E/F) — with the split discipline this project insists on
elsewhere: **split 1's grid and split 2's grid are searched completely independently**,
never sharing data, so their two independently-found "best" configs can be compared as a
direct overfitting test rather than reporting one in-sample winner as if it were proven.

**Result: they disagree, and cross-applying each split's winner to the other split shows
why that matters.**

| | on split 1 (86.63% ceiling) | on split 2 (47.82% ceiling) |
|---|---|---|
| Split 1's own winner (static, giveback=1.00pp, step=3.0%) | **86.63%**/21.51%dd (its home turf) | 41.58%/6.40%dd — identical to base-trail-only, i.e. a no-op here |
| Split 2's own winner (linear, init=1.00pp, decay=0.50pp/tier, step=2.0%) | 43.73%/19.55%dd — return roughly **halved** vs. split 1's base-trail-only (82.10%) | **47.82%**/5.06%dd (its home turf) |

Both grids are also individually "spiky" (return stdev 19.5pp on split 1, 5.9pp on split
2 across the 107 configs) — meaning even within one split, picking the single best cell
carries real noise, before cross-split disagreement is even considered.

**Honest bottom line**: split 1's optimum, taken to split 2, does nothing extra over the
simple base trail (identical result) — harmless but not an improvement. Split 2's
optimum, taken to split 1, actively costs about half the return — a real example of a
config that looks great in-sample and hurts out-of-sample. With only 2 independent ETH
windows and results this sensitive to which one gets searched, **no specific
giveback/spacing number can be honestly "polished" from this data** — doing so would be
fitting noise, exactly the trap this project has refused to fall into elsewhere (Round 13,
Round 24). The originally shipped Design D (giveback=0.75pp, step=2.5%, 64.15%/13.95%dd)
sits inside this same noisy landscape, not distinguishably better or worse than nearby
alternatives — it stays as a documented, working option, but "polished" is not an honest
description of it, and the underlying recommendation is unchanged: **the plain
1.5%-every-3% base trail (61.84%/13.95%dd, validated on both splits) is what this project
can actually stand behind for ETH.** More independent regime windows, not more parameter
searching on the same 2, is what a real answer here would require.

No config change; still backtest only.

**Round 26 follow-up 4 — more independent windows was the real ask: does ETH's own
Kraken data go back further than Yahoo's, and does having 4 splits (like BTC) resolve
anything?** The user asked to go find more ETH history rather than keep tuning on the
same 2 windows. It exists: the same "Kraken_OHLCVT_Full" bulk archive that supplied BTC's
`XBTUSD_1.csv`/`XBTUSD_1440.csv` back in Round 9 covers every Kraken pair, not just
BTC/USD — it just hadn't been extracted for ETH before. Re-downloaded the archive's
central directory (a multi-GB split zip hosted at
`assets.kraken.com/marketing/institutions/`) and pulled just the `ETHUSD_1440.csv` and
`ETHUSD_1.csv` entries directly via HTTP range requests (no need to download the full
~9GB archive) — now sitting alongside the BTC files in
`data/kraken_historical/extracted/`. Range: **2015-08-07 to 2026-06-30**, i.e. genuinely
longer than Yahoo's ETH-USD history (starts 2017-11-09) and long enough to build the SAME
4-split methodology BTC already uses (`ETH_SPLITS` in `experiments_trailing_ratchet.py`,
mirroring `experiments_multisplit_kraken.SPLITS` with the pre-2017 split's train start
capped at ETH's actual listing date instead of BTC's earlier 2013-10-06). **Verified
before trusting it**: spot-checked six well-known ETH price points (the Jan-2018 top,
COVID "Black Thursday" low, Nov-2021 all-time high, mid-2022 bear low, etc.) — all match
documented history — and cross-checked Kraken's daily closes against Yahoo's for every
matching date: mean absolute difference 0.29% (BTC's own Round 9 cross-check was 0.31% —
consistent), worst outlier clustered in December 2017 (same bubble-mania period flagged
as BTC's worst divergence in Round 9). The data itself is trustworthy.

**Result — this doesn't just fail to resolve the earlier ambiguity, it surfaces a bigger
problem: ETH's own LIVE BASELINE (fixed TP/SL, no trailing stop at all) does not validate
on all 4 splits either.** It passes splits 1, 2, and 4 but **fails split 3** (test
2018-01-01..2019-12-31, the 2018 bear market — buy&hold -83.01% that window) with a
-17.29% return. Neither the plain base trail nor Design D v1 validate on all 4 splits
either (both additionally fail split 2, test 2020-2021, where the plain-TP baseline still
passes) — every candidate tested, including the currently-live config, now fails to clear
this project's own "profitable and passes every independent split" bar once a genuine
bear-market window is in the mix:

| | split 1 (2022-25) | split 2 (2020-21) | split 3 (2018-19) | split 4 (2023-26) | validates all 4? |
|---|---|---|---|---|---|
| live baseline | PASS +1.09% | PASS +5.83% | **fail -17.29%** | PASS +27.58% | **No** |
| plain base trail | PASS +18.76% | fail -0.63% | fail -21.61% | PASS +54.02% | **No** |
| Design D v1 | PASS +24.45% | fail -9.40% | fail -21.43% | PASS +61.51% | **No** |

**A second, separate finding worth flagging on its own**: splits 1 and 2 above use the
identical date ranges as the old 2-split Yahoo-based numbers (live baseline averaged
26.11%/14.87%dd across those two splits previously), but the Kraken-sourced re-run of
those same two windows averages closer to 3-6% return — despite the underlying raw price
data being nearly identical (the 0.29%-mean-difference cross-check above). A
threshold-crossing mean-reversion entry (21-day Stochastic + ADX filter) is evidently
sensitive enough to small, sub-1%, expected cross-exchange price differences that entry
timing — and therefore total simulated return — can shift substantially even when the
underlying prices barely move. That's a fragility finding about ETH's live strategy in
its own right, independent of anything to do with the trailing-stop question this round
started from.

**Honest bottom line**: going from 2 to 4 independent ETH windows didn't produce a
cleaner answer on the trailing-stop ladder — it revealed that ETH's currently-live config
itself was never actually tested against a real bear-market split the way BTC's has been
since Round 9/10, and it isn't currently validated the same way BTC's is. This is a
bigger, more foundational finding than the original ask. **No config change made — this
is a backtest-only finding that needs its own decision, separate from the trailing-stop
work**, and it applies to the CURRENT live ETH config, not just the candidates from this
round.

**Round 26 follow-up 5 — Design D finalized on the real 4-split data: the best floor
found, and whether the split-3 loss is actually fixable from the exit side.** The user
asked to lock in on Design D and get it properly polished now that 4 real splits exist,
instead of 2. `run_design_d_grid_search` (follow-up 3) only ever compared the first two
entries of whatever `cfg["splits"]` held — harmless with 2 splits, silently incomplete
now that ETH has 4. Generalized it (`run_design_d_final` in
`experiments_trailing_ratchet.py`) to check agreement across all 4 splits (6 pairs, not
1) and to score candidates the legitimate way: pool every split's own top-5 in-sample
configs (Design D's static-giveback shape and the peak-relative linear-giveback shape
together, static+linear grid unchanged from follow-up 3), then evaluate every pooled
candidate **out-of-sample, jointly, against all 4 splits** — no candidate gets picked
just because it happened to look best on the one split it was found on.

**Cross-split agreement barely exists**: only 1 of 6 pairs agree (splits 1 and 4, both
landing on `static giveback=0.50pp step=2.5%` — the two least-volatile, most-recent
windows). Splits 2 and 3 each want something different from everyone else. This confirms
follow-up 3's "the two splits disagree" finding wasn't a fluke of having only 2 windows —
adding more data revealed *more* disagreement, not less.

**Best jointly-scored candidate: `linear init=1.25pp decay=0.50pp/tier step=2.0%`**
(Design E/F's peak-relative shrinking-giveback shape, not Design D's original static
form). Passes 3 of 4 splits — beats live baseline on splits 1 and 4, is roughly flat on
split 2, and **still fails split 3** (-17.92%, essentially the same loss as baseline's
-17.29%). Beats the live baseline in aggregate (avg return 7.73% vs. 4.30%) and comes
close to the plain base trail's aggregate return (12.63%) with somewhat better per-trade
discipline, but does **not** reach full 4-split validation — nothing tested does.

| | avg return | avg drawdown | splits passed |
|---|---|---|---|
| live baseline | 4.30% | 20.18% | 3/4 |
| plain base trail (no ladder) | 12.63% | 24.77% | 3/4 |
| Design D v1 (hand-picked) | 13.78% | 23.67% | 2/4 |
| **best jointly-scored config (linear init=1.25/decay=0.50/step=2.0)** | **7.73%** | **22.62%** | **3/4** |

**Is split 3's loss exit-fixable, or bounded by the entry?** Directly checked, not
assumed: of the 49 trades split 3's entry signal produces, **42 (86%) never get above
roughly 0% profit before reversing** — straight-down losers from the moment of entry.
Only 4 of 49 ever reach the ladder's first 5% milestone at all. Average peak profit
across every trade in the split is +1.18%. Baseline's own numbers make the same point
differently: 10.4% win rate, average winner +10.00% (hits the fixed TP), average loser
-1.00% (hits the fixed SL) — the classic signature of a mean-reversion entry
("buy the dip") firing repeatedly into a market that keeps dipping further, in a
persistent 2018-19 bear regime. Tested the tightest floor structurally possible (a
0.1pp static giveback — as close to "lock the milestone level itself" as any design in
this file can get): still **-20.31%**, worse than doing nothing. The best jointly-scored
config manages -17.92%, essentially a wash with baseline's -17.29%. **Honest conclusion:
this loss is entry-bound, not exit-fixable.** No trailing floor, however tight, can
protect a trade that never has any unrealized profit to protect in the first place —
fixing split 3 needs a change to the entry signal itself (out of scope for this
exit-only round), not a better floor.

**No config change made** — still backtest-only, per every round of this work. The
practical takeaway for the user's original ask ("create the best floor"): the search was
done properly this time (4 real splits, out-of-sample joint scoring, not a single split's
in-sample winner), and the honest answer is that the best floor found is a real,
measurable improvement over the live baseline in aggregate, but it cannot be called fully
validated while ETH's own entry signal still fails a genuine bear-market regime outright
— that's now the bottleneck, not the exit mechanism.

**Round 26 follow-up 6 — "let's leave the bear market out of this whole equation": Design
D re-finalized on splits 1/2/4 only, split 3 explicitly excluded.** This is a deliberate
user scoping decision, documented here so a future reader knows *why* a split was dropped
rather than just seeing it missing. Follow-up 5 already proved, directly rather than
assumed, that split 3's loss is entry-bound: 42 of 49 trades (86%) never reach positive
profit before reversing, and even the tightest floor structurally possible (0.1pp
giveback) still lost -20.31% there — worse than doing nothing. Since no exit-side design
can influence a trade that has no unrealized profit to protect, holding this
exit-mechanism search accountable to a split it cannot affect was never a fair test of
the mechanism itself. Split 3 (2018-01-01..2019-12-31) remains a real, unresolved problem
with ETH's entry signal in bear regimes — it is being set aside for this exit-only work,
not fixed, not hidden. `run_design_d_final_ex_bear` reruns the exact same procedure as
follow-up 5's `run_design_d_final` (same static+linear grid, same pooled-top-5 +
out-of-sample joint scoring, same close-confirmed variant) against only splits 1
(2022-25), 2 (2020-21), and 4 (2023-26).

**With the entry-bound split removed, something finally validates cleanly.** Live
baseline on the 3 kept splits: 11.50% avg return / 18.05% avg drawdown, passes all 3.
Best jointly-scored ladder candidate — `linear init=1.25pp decay=0.50pp/tier step=2.0%`,
the same peak-relative shrinking-giveback shape that won follow-up 5 — **passes all 3
kept splits**, beating baseline on every one (s1: +7.1% vs +1.1%, s2: +1.6% vs +5.8% is
the one split where it's actually behind baseline on raw return despite still counting
as a pass, s4: +40.1% vs +27.6%). Cross-split agreement is still thin (1 of 3 pairs
agree — splits 1 and 4 land on the *static* giveback shape, split 2 wants the *linear*
shape), so this remains a search result earned from proper out-of-sample scoring across
disagreeing splits, not a case where the answer was obvious going in.

**But it does not beat doing nothing.** The plain base trail (no milestone ladder at
all — just the 1.5%-every-3% continuous trail from the original Round 26 result) was
deliberately left out of the candidate pool above, since it isn't a milestone-ladder
config and including it would let it win the ranking by construction rather than by an
honest side-by-side. Checked explicitly instead:

| | avg return | avg drawdown | splits passed |
|---|---|---|---|
| live baseline (splits 1/2/4) | 11.50% | 18.05% | 3/3 |
| plain base trail (no ladder) | **24.05%** | 23.81% | 3/3 |
| best ladder config (linear init=1.25/decay=0.50/step=2.0) | 16.28% | **21.64%** | 3/3 |

The ladder gives up 7.77 percentage points of average return relative to the plain trail
in exchange for 2.17 points less drawdown — both pass all 3 splits, so this is a
risk/return tradeoff, not a clean win either way. Per this project's own tie-breaker
(prefer the simpler mechanism unless one is clearly better): the plain base trail is
arguably still the better default here — same pass rate, meaningfully higher return, one
fewer moving part (no milestone table to reason about at all).

**Bottom line on "create the best floor," scoped to non-bear regimes**: a genuinely
validated floor design now exists — first time in this whole line of work that anything
has cleared 3-for-3 on real, disagreeing, independent splits — but the honest recommendation
is still the plain 1.5%-every-3% base trail with no milestone ladder on top, because it
outperforms the best ladder on return by a wide margin at only a modest drawdown cost,
using a simpler mechanism. The milestone ladder (`linear init=1.25pp decay=0.50pp/tier
step=2.0%`) is documented, working, and available if the user specifically wants the
drawdown reduction and is willing to pay for it in return — but it is not the default
recommendation. No config shipped to any live bot, as with every round of this work.

**CORRECTION, found while doing follow-up 7 below**: this round's own table above states
the plain base trail (1.5%/3%) "passes all 3" kept splits. Re-verified directly with no
new code involved (`_passed`, the exact same function used everywhere in this project) —
**that is wrong**. Split 2 (2020-2021) actually returns -0.63%, which fails `_passed`'s
plain `total_return_pct > 0` requirement. The three per-split returns (+18.76%, -0.63%,
+54.02%) still average to +24.05% and the three drawdowns still average to 23.81% — so
the *aggregate numbers* reported above are correct, only the "passes all 3 splits" claim
was not. The plain base trail actually passes 2 of 3 kept splits, not 3 of 3. This is
corrected here rather than silently edited into the text above, so the history of what
was claimed and when stays honest.

**Round 26 follow-up 7 — the plain trail's own two parameters were never searched either.**
Every round up to this point treated the base trail's `trail_pct=1.5%` / `trail_step_pct=3%`
as fixed (the user's original spec, used as-is). Since the plain trail is now the actual
best-performing mechanism found (and per the correction above, doesn't even validate
cleanly at its original settings), its own numbers deserve the same search rigor Design
D's floor got. `simulate_trailing_ratchet` gained two real free parameters — `trail_pct`
and `trail_step_pct` (the latter accepts `None` for **continuous** re-anchoring, i.e. the
trail tracks the live peak on every single bar with no discrete step at all, a
generalization of the spec beyond just "every 3%"). Two new synthetic checks (case 6a/6b)
confirm both parameters are genuinely live, not decorative, before trusting any real-data
number: a tighter `trail_pct` measurably locks a higher floor, and `trail_step_pct=None`
measurably re-anchors on a bar where a discrete step would not have. `run_plain_trail_optimization_ex_bear`
grid-searches `trail_pct` (0.5%-4.0%, 11 values) x `trail_step_pct` (1%-5% plus
continuous, 8 values) — 88 configs total — using the exact same split-discipline
methodology as Design D's search: independently per kept split (1/2/4, bear split 3
excluded by the same standing decision as follow-up 6), then pool each split's top-5 and
score every candidate jointly out-of-sample across all 3.

**Result: the original 1.5%/3% spec was not close to optimal, and a real, generalizing
improvement exists.**

| | avg return | avg drawdown | splits passed |
|---|---|---|---|
| live baseline (splits 1/2/4) | 11.50% | 18.05% | 3/3 |
| current default (trail=1.5% / step=3%) | 24.05% | 23.81% | **2/3** (see correction above) |
| **winner: trail=3.5% / step=4%** | **60.34%** | **22.64%** | **3/3** |

The winner beats the (corrected) current default on every axis at once: +36.29
percentage points of return, 1.17 points less drawdown, and it actually clears all 3
splits where the original spec did not (s1: +89.4%, s2: +4.6%, s4: +87.0% — all positive,
vs. the default's -0.63% loss on s2). Cross-split agreement on the exact numbers is
still thin (1 of 3 pairs formally agree within tolerance — splits 1 and 4 both
independently land on exactly `trail=3.5%/step=4%`, split 2 independently wants
`trail=2.5%/step=5%`), but there is a clear *directional* pattern underneath the
disagreement worth stating plainly: **all three splits, independently, want a wider
trail and a wider step than the original 1.5%/3% spec** — they disagree only on exactly
how much wider, not on the direction. The landscape itself is "spiky" (large swings
between nearby grid cells) on splits 1 and 4, so treating `trail=3.5%/step=4%` as some
precisely-tuned optimum would overstate the confidence here — but as a validated,
out-of-sample-scored recommendation that clears every kept split with real margin, it is
a materially better default than the hand-picked original.

**Bottom line, updated**: the recommended plain trail is now `trail_pct=3.5%`,
`trail_step_pct=4%` (both close-confirmed, ladder still off) — not the original
1.5%/3% spec, which turns out not to validate cleanly at all once checked precisely. No
config shipped to any live bot, as with every round of this work.

**[SHIPPED] ETH's live bot now runs this mechanism** — `trail_pct=3.5%`,
`trail_step_pct=4.0%`, close-confirmed, no milestone ladder, replacing the fixed
take-profit exit entirely (`live_eth/paper_trader.py`, `live_eth/config.py`,
`live_eth/.env`). `STOP_LOSS_PCT` reverted from a same-session manual +5% override back to
Round 20's validated 1.0% (the initial floor before the trail engages) — the manual
override's protection level was explicitly preserved via a one-time migration path for
the position that was already open at deploy time, verified in the live logs
(`Migrated open position to Round 26 trailing stop | floor=2567.75`). Bear-market split 3
(2018-19) remains unresolved and out of scope, per follow-up 6's reasoning.

**Round 27 — BTC trailing-stop optimization, same rigor applied fresh (not assumed from
ETH).** The user asked for BTC and DOGE to get the identical treatment now that ETH's
mechanism shipped. `run_btc_trailing_stop_analysis()` in `experiments_trailing_ratchet.py`
repeats follow-ups 4-7's methodology end to end against BTC's real 4-split Kraken-native
data (entry unchanged: SMA(10,30)+RSI, SL=1%/TP=20%) — baseline check, direct (not
assumed) entry-bound diagnosis per split, plain-trail `trail_pct` x `trail_step_pct` grid
search with out-of-sample joint scoring, and a milestone-ladder check. Close-confirmed
variant only throughout, per the standing instruction.

**BTC's live baseline already validates on all 4 splits** (14.93% avg return / 12.24% avg
drawdown) — unlike ETH, there was no foundational validation gap to uncover here.
**Entry-bound diagnosis found no unfixable split for BTC.** Every split's baseline passes,
so the "fails baseline AND >=70% of trades never see positive profit AND the tightest
possible floor (0.1pp giveback) still loses" bar that flagged ETH's 2018-19 split as
structurally unfixable was checked fresh against all 4 BTC splits and never met — BTC's
worst split by "% of trades that never turn positive" is split 2 (80%), but its baseline
still passes there (+4.06%) and even the tightest floor test returns +8.09%, nothing like
ETH split 3's -20%+ result. No split was excluded from this search.

**The plain-trail grid search produced a clean win — first time any coin's exit mechanism
has validated on every real split with no exclusions needed:**

| | avg return | avg drawdown | splits passed |
|---|---|---|---|
| BTC live baseline | 14.93% | 12.24% | 4/4 |
| original ratchet default (trail=1.5%/step=3%) | 2.73% | 11.64% | 0/4 |
| **winner: trail=4.00% / step=4.0%** | **39.14%** | 13.74% | **4/4** |

Beats baseline on return by a wide margin (+24.21pp) at a modest drawdown cost (+1.50pp,
still well under the 40% cap), and — unlike every ETH search — clears all 4 splits
without needing to exclude anything: s1 +68.9%, s2 +1.4%, s3 +47.1%, s4 +39.1%, all
positive. Cross-split agreement is thin (1 of 6 pairs, the same pattern seen everywhere
else in this file), but the winner was still found by proper out-of-sample joint scoring
across all 4 disagreeing splits, not a single split's in-sample pick.

**A milestone ladder does not help BTC either — same conclusion as ETH, now confirmed
independently rather than assumed to transfer.** All three tighter-giveback designs
(D/E/F) tested on top of the winning trail collapse the return to near-zero or negative
(0.79%, -1.02%, -0.27% avg return vs. the plain trail's 39.14%) and none pass all 4
splits. The plain trail alone remains the better mechanism for BTC, same as ETH.

**Recommendation**: `trail_pct=4.0%`, `trail_step_pct=4.0%`, close-confirmed, no ladder —
a genuinely clean, fully-validated result, stronger than ETH's (which still can't clear
its bear-market split). Not yet wired into BTC's live bot — awaiting the same explicit
go-ahead ETH's mechanism got before shipping.

**Round 27 (DOGE) — same rigor, applied fresh, on real DOGE data for the first time.**
DOGE's original Round 26 base finding was a clean "fails outright, both variants" at the
ETH-scale 1.5%/3% trail — a real search, not just accepting that, required both properly
scaled parameters (DOGE's FVG entry produces far fewer, far LARGER, faster-resolving
trades than ETH's — live TP=30% vs. ETH's 10% — so the same 0.5-4.0% grid used elsewhere
was never going to find DOGE's real optimum) and, ideally, more than the 2-split Yahoo
data DOGE had been stuck on.

**Data: DOGE's own native Kraken daily OHLCV, sourced the same way as ETH's** (Round 26
follow-up 4) — targeted HTTP range requests against the "Kraken_OHLCVT_Full_2026Q2" bulk
archive's central directory, 10 requests total, no need to download the ~8.4GB archive.
One wrinkle: **the archive files DOGE under Kraken's historical altname, `XDG`, not
`DOGE`** (`XDGUSD`, not `DOGEUSD`) — the live bot's `PAIR=DOGEUSD` is resolved to `XDGUSD`
internally by Kraken's REST API, so `XDGUSD` is the correct, matching pair. Verified
before trusting it: 2021-05-08 high=$0.74 matches DOGE's widely-documented all-time high
almost exactly, 2021-04-16 high=$0.47 matches that day's well-known pump peak, the
2020-03-13 COVID low (~$0.0012-0.0018) and 2022-06-18 bear low (~$0.053) both match
documented history. Cross-checked against DOGE's existing Yahoo data source: mean abs
diff 0.260% across 2386 matched dates — consistent with ETH's 0.29% and BTC's 0.31%
cross-checks (worst outliers cluster right after Kraken's XDGUSD listing, same
low-early-liquidity pattern as BTC's Dec-2017 outliers, not a data error).

**Unlike ETH, this does NOT extend DOGE's usable history further back** — Kraken's
XDGUSD listing (2019-12-19) is *later* than Yahoo's DOGE-USD start (2017-11-09), so there
is no way to build a 2018-19-bear-market split for DOGE the way ETH's earlier Kraken
listing allowed. What it provides instead: exchange-native data (avoiding the
cross-exchange entry-timing fragility found for ETH) and enough range to build **3**
non-overlapping, structurally distinct test windows — 2022-23 (bear/consolidation), 2021
(meme-mania bull), 2024-26 (recent) — instead of the 2 `REGIME_SPLITS` DOGE was stuck
reusing. More than 2, honestly short of BTC/ETH's 4 because DOGE's real listing history
doesn't support a 4th.

**Entry-bound diagnosis, checked with an ultra-wide sweep this time (0.5%-100% trail),
not just the tight-floor check used for ETH/BTC**: split 1 (2022-23) is confirmed
unfixable — best achievable across the *entire* sweep is still -15.63%, never crosses
zero, 50% of trades never see positive profit before reversing. **Splits 2 and 3 are NOT
entry-bound** — an ultra-wide trail rescues both dramatically (split 2: baseline -36.63%
→ best +170.09%; split 3: baseline +74.94% → best +275.01%), which the narrow 0.5-4.0%
grid used everywhere else in this file would never have found.

**The wide-grid search produced DOGE's most dramatic result in this whole line of work —
and a genuinely important honest caveat that goes with it:**

| | avg return | avg drawdown | splits passed (strict &lt;40% dd cap) |
|---|---|---|---|
| DOGE live baseline (splits 2/3, split 1 excluded) | 19.16% | 43.34% | 0/2 |
| **winner: trail=12% / step=continuous** | **222.09%** | 49.43% | **0/2** |

A trail roughly 3x wider than BTC's optimum and 8x wider than the original 1.5% spec
turns split 2's -36.63% loss into **+173.20%**, and split 3's already-positive +74.94%
into **+271.00%** — by far the largest edge found for any coin in this whole file.
**But neither the winner nor DOGE's own live baseline clears this project's strict <40%
drawdown cap on these splits** — DOGE's SL=3%/TP=30% config is inherently high-drawdown
at this position size regardless of exit mechanism (baseline itself already runs
39-55% drawdown across all 3 splits); this is not a regression the trailing stop
introduces, but it does mean the honest label here is "a dramatically better risk-adjusted
trade than doing nothing," not "fully validated" under this project's own bar — the same
distinction that applied to ETH's bear-split situation, for a different underlying reason.

**The existing ETH-calibrated milestone ladder is actively catastrophic for DOGE — not
just unhelpful, genuinely harmful.** All three ladder designs (D/E/F), built around ETH's
much smaller typical move sizes (milestones starting at 5% profit, givebacks of
0.25-1.5pp), collapse the wide trail's gains into losses when layered on top: plain trail
alone averages +222.09%, adding any ladder design drops that to -29.69% to -37.75% avg
return. The floors, calibrated for profit magnitudes an order of magnitude smaller than
what DOGE routinely reaches, become the binding (and far too tight) constraint almost
immediately once DOGE's price actually moves — cutting off nearly the entire gain. No
ladder recommended for DOGE under any circumstances tested; only the plain wide trail.

**Recommendation**: `trail_pct=12%`, `trail_step_pct=continuous` (re-anchor every bar, no
discrete step), close-confirmed, no ladder, **splits 2 and 3 only** (split 1 excluded as
confirmed entry-bound). A real, substantial, honestly-caveated edge — not full-bar
validated due to the drawdown cap, same as it wasn't for DOGE's own current live config
either. Not wired into any live bot — awaiting the same explicit go-ahead ETH's mechanism
got before shipping, plus the user's own read on whether the drawdown/return tradeoff is
acceptable given neither option clears the strict cap.

## Files

- `config.py` — loads settings from `.env`
- `data_fetcher.py` — fetches/loads OHLCV candles: `fetch_ohlc` (Kraken API, live/near-
  term), `fetch_yahoo_history` (Yahoo Finance, multi-year daily, research/backtest only),
  `load_kraken_csv_1min` + `resample_ohlc` (Kraken's own bulk 1-minute historical data
  and resampling to any coarser interval — see Round 9)
- `strategy.py` — indicator calculation + signal generation
- `paper_trader.py` — simulated portfolio and position-state tracking
- `backtest.py` — historical strategy backtester
- `walk_forward.py` — train/test parameter validation for the default strategy
- `experiments.py` — train/test search across alternative signal families (Kraken data)
- `experiments_regime.py` — same search, validated across market regimes (Yahoo data)
- `experiments_trend.py` — trailing-stop / reverse-signal exits paired with
  trend-following entries, validated across market regimes (Yahoo data)
- `pairs_strategy.py` — BTC/ETH ratio z-score + mean-reversion sanity check (ADF test)
- `pairs_backtest.py` — market-neutral pairs backtester (simulated short-selling, no
  margin/borrow cost modeled)
- `pairs_experiments.py` — regime-split validation grid for the pairs strategy
- `experiments_indicators.py` — MACD/Stochastic/SuperTrend/ADX/VWAP/Ichimoku, validated
  across market regimes (Yahoo data) — see Round 5 above
- `experiments_patient.py` — unconditional entry, no stop-loss, patient profit-target
  exit (ETH only), with per-trade underwater-drawdown tracking — see Round 6 above
- `experiments_wide_tp.py` — tight 1% stop-loss, wide take-profit grid, signal vs.
  unconditional entry comparison, win-rate/expectancy and gap-risk checks — see Round 7
  above
- `experiments_multisplit.py` — round 7's grid re-tested across 4 independent BTC regime
  splits (2014-2026) — see Round 8 above; source of the Round 8 stop/target default
- `experiments_kraken_native.py` — Round 8's exact config re-tested on real Kraken
  1-minute data resampled to 15-minute (matches the live bot) and native 1-minute,
  numpy-array simulate loop for performance on million-row inputs — see Round 9 above;
  source of the current live interval default
- `data/kraken_historical/extracted/` — Kraken's official bulk historical OHLCVT data,
  `XBTUSD_1.csv` (1-minute, Oct 2013–Jun 2026, used by Round 9) and `XBTUSD_1440.csv`
  (native daily, used by Round 10 as the primary data source) — the large source
  zip/part files were deleted after extraction (see Round 9). Also `ETHUSD_1.csv` /
  `ETHUSD_1440.csv` (Aug 2015–Jun 2026, pulled via targeted HTTP range requests against
  the bulk archive's central directory rather than downloading the full ~8.4GB — see
  Round 26 follow-up 4) and `XDGUSD_1.csv` / `XDGUSD_1440.csv` (Dec 2019–Jun 2026, same
  method — note Kraken files DOGE under its historical altname `XDG`, not `DOGE` — see
  Round 27)
- `experiments_multisplit_kraken.py` — Round 8's stop-loss/take-profit grid, extended to
  1%/3%/5%/7% stops, re-run on Kraken's native daily data — see Round 10 above;
  confirmed Round 8's config as still the best, no live changes
- `experiments_entry_style.py` — event-based vs. state-based entry comparison (same
  exit), with re-entry-latency tracking — see Round 11 above; state-based rejected, no
  live changes
- `experiments_exit_mechanisms.py` — trailing profit lock and tiered partial exits vs.
  the fixed-20%-TP baseline, real partial-position engine with synthetic-data
  correctness checks — see Round 12 above; neither adopted (trailing-lock never
  validated, tiered-even was a near-tie kept out by the simplicity tie-breaker)
- `experiments_fast_entry.py` — faster SMA entry periods x a 12-level stop-loss grid,
  with trade-cadence (median days between trades) and implied per-day compounding rate
  instrumentation — see Round 13 above; no faster entry validated at any stop-loss level,
  daily-action / 2-5%-per-day target shown unreachable by this approach
- `experiments_scalp_exit.py` — ultra-tight fee-adjusted take-profit (nets a fixed % after
  both legs' fees) x a relaxed/wide stop-loss grid, across BTC/ETH/SOL and two entry
  styles, with trade-duration and entry-cadence instrumentation — see Round 14 above;
  BTC failed everywhere, ETH/SOL "passes" are single-window survivorship, not an edge
- `experiments_recent_window.py` — EXPLORATORY (see Round 15's caveat): wide stop-loss
  (1-50%) x take-profit (1-100%) grid, unconditional + baseline-signal entry, against only
  the last 2 weeks of live Kraken hourly data across 9 non-BTC coins; writes the full grid
  to `experiments_recent_window_results.csv`
- `experiments_full_history_wide.py` — the identical grid from
  `experiments_recent_window.py`, re-run against each coin's maximum available multi-year
  history under this project's normal regime-split discipline, for direct comparison
  against the 2-week exploratory result — see Round 15 above; writes the full grid to
  `experiments_full_history_wide_results.csv`
- `experiments_pyramiding.py` — multi-concurrent-position engine (equal capital split
  across up to N open positions) tested against each coin's live config and a 5%-target
  variant — see Round 16 above; concurrency reduces return at every level tested, no
  config adopted
- `main.py` — live polling loop (currently: BTC, daily candles, event-based SMA/RSI
  entry, 1% stop-loss / 20% take-profit per Rounds 8-12, run via the
  `AiTradingBotPaperTrader` scheduled task — no live pairs bot was built, see Round 4
  above)
- `experiments_eth_doge_full_search.py` — exhaustive entry-speed x stop-loss x
  take-profit grid search for ETH and DOGE only, reporting the best validated
  frequent-trade and best validated overall combo per coin — see Round 17 above; source
  of ETH's pre-Round-20 config and DOGE's current live config
- `experiments_daily_push.py` — even-faster event entry, state-based entry, and
  unconditional entry tested against ETH/DOGE's live exit configs — see Round 18 above;
  every push toward more-frequent broke the 40% drawdown cap for both coins
- `experiments_daily_small_wins.py` — small-stop-paired-with-small-target grid (the one
  combination not yet tried) across entry speeds for ETH/DOGE — see Round 19 above;
  found safer (lower-drawdown) alternatives for both coins, not yet shipped for DOGE
- `experiments_every_angle.py` — the full Round 5 indicator toolkit (MACD, Stochastic,
  SuperTrend, ADX-filtered, rolling-VWAP, Ichimoku) re-evaluated for cadence and safety
  on ETH/DOGE — see Round 20 above; source of ETH's current live strategy
- `experiments_every_angle_btc.py` — the identical indicator-toolkit search, applied to
  BTC on its proper 4-split Kraken-native data — see Round 21 above; found nothing that
  beats BTC's current config
- `experiments_atr_exit.py` — volatility-scaled (ATR-based) exits instead of a fixed
  percentage, tested against each coin's live entry — see Round 22 above; no real
  improvement, one near-miss (39.87% dd, 0.13pp from failing) flagged as untrustworthy
- `experiments_adx_filter.py` — ADX-threshold regime filter added on top of each coin's
  live entry+exit — see Round 23 above; genuine small win for ETH (shipped), hurts BTC,
  not worth DOGE's return/frequency cost
- `experiments_walk_forward_v2.py` — rolling 2-year-train/6-month-test walk-forward
  optimization vs. the static live config, both evaluated on identical out-of-sample
  folds — see Round 24 above; static config beats continuous re-optimization for all 3
  coins, validating the project's existing fixed-config approach
- `experiments_new_strategies.py` — Bollinger Band Squeeze Breakout and simplified Smart
  Money Concepts (bullish Fair Value Gap) entries, both web-research-sourced, tested
  against all 3 coins — see Round 25 above; FVG is a clean win for DOGE (shipped), a
  return-for-risk/frequency tradeoff for BTC/ETH (not shipped)
- `live_eth/`, `live_sol/` — self-contained ETH and SOL live paper bots (own copies of
  the runtime files, own `.env`/`state.json`/`logs/`), run via
  `AiTradingBotPaperTrader_ETH` / `AiTradingBotPaperTrader_SOL` scheduled tasks.
  `live_eth/strategy.py`/`config.py` were rewritten for Round 20's Stochastic strategy.
  `live_sol/` (formerly `live_doge/`, running Round 25's FVG strategy for DOGE) was
  swapped to SOL in a later session — see "Current status" above and Rounds 17/20/23/25
  for the validation history, and `SOL_RESEARCH.md` for SOL's own config
- `experiments_trailing_ratchet.py` — staged trailing-stop ratchet (1.5%/3% base trail +
  5%/7.5%/10%... hard-floor milestones) against each coin's live entry, with synthetic
  sanity checks, an HWM-vs-close-confirmed noise-filter A/B, and a BTC-only true-1-minute
  sensitivity check against the daily-bar approximation — see Round 26 above; validates
  and beats the live baseline for ETH only (not shipped, backtest only, pending a
  decision), fails outright for BTC and DOGE

**Round 28 - `experiments_round28.py` (can free outside data improve the aggressive ETH_FAST
config?), 2026-09-24:** baseline = live ETH_FAST (SMA(2,3)+RSI entry, SL 2% / TP 50%, daily,
0.26% fee); six variants each add ONE entry filter from free sources (Fear & Greed, Deribit
ETH-PERPETUAL funding - Binance blocks US IPs -, Deribit ETH DVOL, BTC price). Outside data lagged
one day; percentile thresholds trailing-365-day; thresholds fixed up front, not tuned. Output in
`experiments_round28_output.txt`, sources in `ETH_DATA_SOURCES.md`.

**Most important finding - about the baseline itself:** Round 18's "621.85% avg return" is the
average of **-40.7% (2022-2025 test, 62.4% max drawdown)** and **+1,284% (2020-2021 bull, 32.9%
dd)**. The aggressive config LOST money over the most recent multi-year regime; its headline
return came entirely from the 2020-21 bull market. Fresh 2026 window (never used before):
+11.6%, 25.9% dd, 16 trades.

**Result: no filter passes the project's rules** (every variant still breaks the 40% drawdown cap
in 2022-2025). Closest: **"BTC same-day return > 0"** - the only filter better on return AND
drawdown in all three windows: 2022-25 -40.7% -> +23.8% (dd 62.4% -> 43.2%), 2020-21 +1,284% ->
+1,297% (dd 32.9% -> 30.6%), 2026 +11.6% -> +14.5%; costs frequency (median gap 6 -> 8 days).
DVOL spike filter turned 2022-25 positive (+14.8%) but kept 57% dd. Fear & Greed filters cut
drawdown but also cut the bull-market return a lot. Funding filter: no effect. Caveat: 6
variants tried, so one winner could be partly luck - though it improved on the untouched 2026 data
too. **Owner decision 2026-09-24: keep ETH_FAST running unchanged** (no filter) as a pure test of the original config, accepting its 2022-25 result.

import json
import logging
import os

import config

logger = logging.getLogger("trader")

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


class PaperTrader:
    """Simulates a portfolio locally against real Kraken prices — no exchange account needed.

    Enters on the Stochastic/ADX BUY signal. Exit is a Round 26 staged trailing stop
    (see experiments_trailing_ratchet.py, follow-ups 1-7): the floor starts at
    entry * (1 - STOP_LOSS_PCT/100); once unrealized profit crosses a fresh
    TRAIL_STEP_PCT-of-peak-profit threshold, the floor ratchets up to TRAIL_PCT below the
    peak price and never loosens again. Peak tracking is close-confirmed (driven by the
    polled price, which is always a completed candle's close, matching the validated
    "close" variant — never an intrabar wick). No fixed take-profit any more: profit runs
    until the trailing floor is hit. Fees are deducted on both sides. State persists to
    state.json so a restart doesn't lose the simulated position or its trail progress.

    Note: this checks the floor against the latest polled price, not true intrabar
    highs/lows like the backtester does — so live results will tend to run a bit worse
    than backtest, since the backtest gets a favorable "best case" fill within each candle.
    """

    def __init__(self):
        (
            self.cash, self.coins, self.entry_price, self.trades,
            self.peak_price, self.trail_anchor, self.last_trail_step, self.floor_price,
        ) = self._load_state()
        self._migrate_open_position_if_needed()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                state = json.load(f)
            return (
                state["cash"], state["coins"], state.get("entry_price"), state.get("trades", []),
                state.get("peak_price"), state.get("trail_anchor"),
                state.get("last_trail_step", 0), state.get("floor_price"),
            )
        return config.STARTING_CASH, 0.0, None, [], None, None, 0, None

    def _migrate_open_position_if_needed(self):
        """One-time safeguard: if this process (re)starts already in a position opened
        before the Round 26 trailing stop was deployed (peak_price missing from
        state.json), seed the floor at least as protective as the manual +5% override
        that was live immediately before this deploy, so shipping this doesn't regress
        protection on an already-open trade. Runs once — after the first save, peak_price
        is present and this branch is skipped on every later load."""
        if self.in_position and self.peak_price is None:
            preserved_floor = self.entry_price * 1.05
            base_floor = self.entry_price * (1 - config.STOP_LOSS_PCT / 100)
            self.peak_price = self.entry_price
            self.trail_anchor = self.entry_price
            self.last_trail_step = 0
            self.floor_price = max(base_floor, preserved_floor)
            self._save_state()
            logger.info(
                "Migrated open position to Round 26 trailing stop | floor=%.2f "
                "(preserved from the prior manual override, will only ratchet up from here)",
                self.floor_price,
            )

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump({
                "cash": self.cash,
                "coins": self.coins,
                "entry_price": self.entry_price,
                "trades": self.trades,
                "peak_price": self.peak_price,
                "trail_anchor": self.trail_anchor,
                "last_trail_step": self.last_trail_step,
                "floor_price": self.floor_price,
            }, f, indent=2)

    @property
    def in_position(self) -> bool:
        return self.coins > 0

    def equity(self, price: float) -> float:
        return self.cash + self.coins * price

    def on_tick(self, signal: str, price: float, timestamp):
        fee_rate = config.FEE_PCT / 100

        if not self.in_position:
            if signal == "BUY":
                self.entry_price = price
                self.coins = (self.cash * (1 - fee_rate)) / price
                self.cash = 0.0
                self.peak_price = price
                self.trail_anchor = price
                self.last_trail_step = 0
                self.floor_price = price * (1 - config.STOP_LOSS_PCT / 100)
                self._log_trade("BUY", price, timestamp)
                self._save_state()
            else:
                logger.info("Signal %s, no action (flat)", signal)
            return

        if price <= self.floor_price:
            reason = "TRAILING_STOP" if self.last_trail_step >= 1 else "STOP_LOSS"
            self._exit(reason, price, timestamp)
            return

        # peak tracking: close-confirmed (the polled price is always a completed
        # candle's close, matching the validated "close" variant — never an intrabar high)
        self.peak_price = max(self.peak_price, price)
        peak_profit_pct = (self.peak_price / self.entry_price - 1) * 100

        # re-anchor the trail only at each fresh TRAIL_STEP_PCT-of-peak-profit crossing,
        # not continuously — this is the noise filter that stops the floor jittering
        trail_step = int(peak_profit_pct // config.TRAIL_STEP_PCT)
        if trail_step > self.last_trail_step:
            self.last_trail_step = trail_step
            self.trail_anchor = self.peak_price

        if self.last_trail_step >= 1:
            trail_floor = self.trail_anchor * (1 - config.TRAIL_PCT / 100)
            self.floor_price = max(self.floor_price, trail_floor)  # monotonic ratchet, never loosens

        unrealized_pct = (price / self.entry_price - 1) * 100
        locked_pct = (self.floor_price / self.entry_price - 1) * 100
        logger.info(
            "Holding | entry=%.2f price=%.2f peak=%.2f unrealized=%.2f%% floor=%.2f locked=%.2f%%",
            self.entry_price, price, self.peak_price, unrealized_pct, self.floor_price, locked_pct,
        )
        self._save_state()

    def _exit(self, reason: str, price: float, timestamp):
        proceeds = self.coins * price
        self.cash = proceeds * (1 - config.FEE_PCT / 100)
        self.coins = 0.0
        self.entry_price = None
        self.peak_price = None
        self.trail_anchor = None
        self.last_trail_step = 0
        self.floor_price = None
        self._log_trade(f"SELL ({reason})", price, timestamp)
        self._save_state()

    def _log_trade(self, side: str, price: float, timestamp):
        logger.info(
            "%s at %.2f | cash=%.2f coins=%.6f equity=%.2f",
            side, price, self.cash, self.coins, self.equity(price),
        )
        self.trades.append({"side": side, "price": price, "time": str(timestamp)})

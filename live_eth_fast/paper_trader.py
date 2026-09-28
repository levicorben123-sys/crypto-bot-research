import json
import logging
import os

import config

logger = logging.getLogger("trader")

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")


class PaperTrader:
    """Simulates a portfolio locally against real Kraken prices — no exchange account needed.

    Enters on the SMA/RSI BUY signal, then exits automatically at +TAKE_PROFIT_PCT or
    -STOP_LOSS_PCT from entry (whichever hits first) rather than waiting for a reverse
    signal. Fees are deducted on both sides. State persists to state.json so a restart
    doesn't lose the simulated position.

    Plain fixed TP/SL, no trailing stop - per the SOL deep-dive (SOL_RESEARCH.md), no
    trailing-stop mechanism beat this fixed exit on SOL's real data; every config tested
    either underperformed or overfit to a single outlier trade. SL=2%/TP=30% is the
    grid-search-validated combo (32.02% avg return / 14.23% avg dd, passing all 3 real
    Kraken-native splits), replacing the narrower-grid 3%/30% first reported.

    Note: this checks TP/SL against the latest polled price, not true intrabar highs/lows
    like the backtester does — so live results will tend to run a bit worse than backtest,
    since the backtest gets a favorable "best case" fill within each candle.
    """

    def __init__(self):
        self.cash, self.coins, self.entry_price, self.trades = self._load_state()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                state = json.load(f)
            return state["cash"], state["coins"], state.get("entry_price"), state.get("trades", [])
        return config.STARTING_CASH, 0.0, None, []

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump({
                "cash": self.cash,
                "coins": self.coins,
                "entry_price": self.entry_price,
                "trades": self.trades,
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
                self._log_trade("BUY", price, timestamp)
                self._save_state()
            else:
                logger.info("Signal %s, no action (flat)", signal)
            return

        tp_price = self.entry_price * (1 + config.TAKE_PROFIT_PCT / 100)
        sl_price = self.entry_price * (1 - config.STOP_LOSS_PCT / 100)

        if price <= sl_price:
            self._exit("STOP_LOSS", price, timestamp)
        elif price >= tp_price:
            self._exit("TAKE_PROFIT", price, timestamp)
        else:
            unrealized_pct = (price / self.entry_price - 1) * 100
            logger.info(
                "Holding | entry=%.2f price=%.2f unrealized=%.2f%% (tp=%.2f sl=%.2f)",
                self.entry_price, price, unrealized_pct, tp_price, sl_price,
            )

    def _exit(self, reason: str, price: float, timestamp):
        proceeds = self.coins * price
        self.cash = proceeds * (1 - config.FEE_PCT / 100)
        self.coins = 0.0
        self.entry_price = None
        self._log_trade(f"SELL ({reason})", price, timestamp)
        self._save_state()

    def _log_trade(self, side: str, price: float, timestamp):
        logger.info(
            "%s at %.2f | cash=%.2f coins=%.6f equity=%.2f",
            side, price, self.cash, self.coins, self.equity(price),
        )
        self.trades.append({"side": side, "price": price, "time": str(timestamp)})

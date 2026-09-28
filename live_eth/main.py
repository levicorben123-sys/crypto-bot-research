import logging
import time

import config
from data_fetcher import fetch_ohlc
from paper_trader import PaperTrader
from strategy import generate_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("main")


def run():
    logger.info(
        "Starting paper trading bot | pair=%s | interval=%smin | starting_cash=%s",
        config.PAIR, config.INTERVAL_MINUTES, config.STARTING_CASH,
    )

    trader = PaperTrader()

    while True:
        try:
            df = fetch_ohlc(config.PAIR, config.INTERVAL_MINUTES)
            signal = generate_signal(df)
            last = df.iloc[-1]
            price = last["close"]
            logger.info(
                "Price=%.2f Signal=%s Equity=%.2f InPosition=%s",
                price, signal, trader.equity(price), trader.in_position,
            )
            trader.on_tick(signal, price, last["time"])
        except Exception:
            logger.exception("Error in trading loop")

        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()

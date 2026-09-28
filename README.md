# Crypto Bot Research — 28 rounds of honest strategy testing

Crypto trading-strategy research and two live **paper-trading** bots (simulated portfolio, no exchange
account, no real money) using Kraken's public market data.

Built with [Claude Code](https://claude.com/claude-code) as the coding agent; I set the goals and
decisions and reviewed every result.

## The point of this repo
Most strategy ideas look good on the data they were tuned on. This project tested ideas the hard way —
**train/test splits across 4 separate market regimes (2014–2026)** — and recorded the failures as
carefully as the wins. The full log of all 28 rounds is in [`docs/RESEARCH_LOG.md`](docs/RESEARCH_LOG.md).

## Key findings
- Most tested ideas (directional signals, pairs trading, common indicators, many entry/exit variants)
  showed **no validated edge**.
- One configuration held up: SMA(10)/SMA(30) crossover + RSI filter, 1% stop / 20% target, daily candles —
  positive expectancy in all 4 regimes, but it does **not** beat simply holding in bull markets.
- The same settings failed badly on 15-minute candles (-48% to -99%), which caught a bug where the live bot
  was running on the wrong timeframe.
- An aggressive variant averaged +622% in backtests, but that came almost entirely from the 2020–21 bull
  market (-41% in 2022–25, 62% max drawdown). It runs on paper as an explicit stress test.

## Layout
| Path | Purpose |
|---|---|
| `backtest.py`, `walk_forward.py` | Backtest engine and walk-forward validation |
| `experiments_*.py` | One script per research round / idea |
| `pairs_*.py` | Pairs-trading research |
| `strategy.py`, `paper_trader.py`, `main.py` | Strategy, simulated portfolio, live loop |
| `live_eth/`, `live_eth_fast/` | The two running ETH paper bots (conservative and aggressive) |

## Run
```
pip install -r requirements.txt
python backtest.py      # backtest with settings from .env.example
python main.py          # live paper loop
```

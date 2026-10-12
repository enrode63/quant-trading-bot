# US turtle signals and Toss Securities execution

`turtle_bot.py` is the original Discord signal scanner. `toss_trader.py` is a separate US stock order runner using the [official Toss Securities Open API](https://developers.tossinvest.com/). It starts in **read-only preview mode**. `positions.json` belongs to the old alert bot and is never treated as proof of an actual brokerage holding.

## Strategy

- Scan the symbols in `turtle_bot.py` using completed, split-adjusted daily candles from Toss Securities. The independent Discord alert bot still uses Yahoo Finance.
- Buy signal: the completed candle's high breaks the preceding 20-day high. When several symbols qualify, higher ADX is considered first.
- Exit signal: the completed candle's low breaks the preceding 20-day low, or its close is at/below the fixed entry stop (`entry signal close - 2 × ATR`).
- An order is placed during the **next US regular session**, between 09:40 and 15:30 New York time. The strategy is daily, so the stop is checked once per run. It is **not an exchange-hosted intraday stop**. Gaps can cause much larger losses.
- New positions use whole shares and day-valid limit orders at the best ask; exits use whole-share limit orders at the best bid. Unfilled orders can expire at the close. A later run checks brokerage holdings and open orders before acting again.

The strategy was originally an alert script. The existing `positions.json` contains simulated entries and must not be migrated to live holdings. The new runner only manages positions that it submitted and recorded in its private state file. It leaves pre-existing/manual positions alone.

## Configuration

Use Python 3.11+ and install `requirements.txt`. In the [Toss Securities WTS Open API settings](https://developers.tossinvest.com/docs), create a client ID and secret, register the runner's **fixed public IP**, and find your `accountSeq` using the account API. Keep credentials in environment variables or a secret store, never in the repository or Discord.

Required environment variables:

| Name | Meaning |
| --- | --- |
| `TOSS_CLIENT_ID`, `TOSS_CLIENT_SECRET` | Open API credentials |
| `TOSS_ACCOUNT_SEQ` | Brokerage account sequence, not the account number |
| `TOSS_BUYING_POWER_PCT` | Fraction of starting USD cash buying power allocated per new symbol, e.g. `0.05` for 5% |
| `TOSS_MAX_POSITIONS` | Maximum simultaneous US holdings before new buys are skipped |
| `TOSS_MAX_ORDERS_PER_RUN` | Maximum orders in one run |
| `TOSS_STATE_FILE` | Persistent, private file path outside a Git checkout |

Optional `TOSS_MAX_USD_PER_TRADE` places a further dollar cap on each buy. The runner uses the smaller of this cap and `buying power × TOSS_BUYING_POWER_PCT`, then checks the current remaining USD buying power. Toss Open API does not automatically exchange KRW into USD; the account needs enough USD. For live mode, add `TOSS_LIVE_TRADING=YES` **and** the `--live` CLI flag.

```bash
python -m pip install -r requirements.txt
python toss_trader.py --init-state    # Once, on the persistent runner host
python toss_trader.py                 # Read account and show proposed orders only
python toss_trader.py --live          # Sends orders only with TOSS_LIVE_TRADING=YES
```

The runner requires a fresh USD orderbook, rejects spreads over 1%, checks actual holdings, open orders, USD buying power, and sellable quantity, and records an order intent **before** sending it. If a POST times out, its outcome is unknown: inspect Toss order history and the private state file before another run. The symbol stays blocked while an order outcome is unresolved. Do not remove the state file to force a retry. The server's `clientOrderId` protects retries for only 10 minutes; the local state file prevents repeated signals across runs.

## Automation

`.github/workflows/toss_trade.yml` is a template for a **self-hosted GitHub runner with a fixed public IP**. GitHub-hosted runner IPs are not suitable for a stable Toss allowlist. Keep `TOSS_STATE_FILE` on persistent storage outside the checkout and limit access to the runner user. The workflow checks both UTC times needed for US daylight saving time; the Python runner enforces New York trading hours. Before enabling it, review the strategy and position limits, provision USD, register the runner IP, and set the workflow secrets and variables. Its scheduled runs remain preview-only until the live gate is deliberately set.

The existing `turtle_alert.yml` continues to send independent paper-state Discord alerts. Those messages are not evidence of brokerage execution.
Its 20-day-low exits are labeled by the estimated return versus the recorded entry: profit, loss, or break-even. ATR-stop messages identify the stop trigger separately. These are signal estimates, not filled trade P&L.

## API references

- [Toss Securities Open API guide](https://developers.tossinvest.com/)
- [Canonical OpenAPI schema](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)
- [API FAQ](https://openapi.tossinvest.com/openapi-docs/faq.md)

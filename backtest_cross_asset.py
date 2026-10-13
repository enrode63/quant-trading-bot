"""Run the six exit variants on SPY, QQQ, DIA, BTC-USD, and ETH-USD."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backtest_exits import load_prices, simulate


ASSETS = ("QQQ", "SPY", "DIA", "BTC-USD", "ETH-USD")
FRACTIONAL = frozenset({"BTC-USD", "ETH-USD"})
VARIANTS = (("existing", None),) + tuple(("chandelier", multiple) for multiple in range(1, 6))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument(
        "--end", default=datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
        help="exclusive date; use 2026-10-13 to match the 55-stock test",
    )
    parser.add_argument("--cash", type=float, default=100_000)
    parser.add_argument("--friction", type=float, default=0.001)
    parser.add_argument("--output", type=Path, default=Path("backtest_results_cross_asset"))
    args = parser.parse_args()
    if args.cash <= 0 or not 0 <= args.friction < 1:
        parser.error("cash must be positive and friction must be in [0, 1)")

    prices = load_prices(args.start, args.end, ASSETS)
    args.output.mkdir(parents=True, exist_ok=True)
    basket_rows, asset_rows, basket_trades = [], [], []
    for kind, multiple in VARIANTS:
        summary, trades, curve = simulate(prices, kind, multiple, args.cash, args.friction, FRACTIONAL)
        basket_rows.append(summary)
        basket_trades.extend({"variant": summary["variant"], **trade} for trade in trades)
        curve.to_csv(args.output / f"basket_{summary['variant']}_equity.csv")
        for symbol, frame in prices.items():
            single, _, _ = simulate(
                {symbol: frame}, kind, multiple, args.cash, args.friction, FRACTIONAL,
            )
            asset_rows.append({"asset": symbol, **single})
    pd.DataFrame(basket_rows).to_csv(args.output / "basket_summary.csv", index=False)
    pd.DataFrame(asset_rows).to_csv(args.output / "per_asset_summary.csv", index=False)
    pd.DataFrame(basket_trades).to_csv(args.output / "basket_trades.csv", index=False)
    buyhold = pd.DataFrame([
        {
            "asset": symbol,
            "first_date": frame.index[0].strftime("%Y-%m-%d"),
            "last_date": frame.index[-1].strftime("%Y-%m-%d"),
            "buy_hold_return_pct": round(100 * (frame["close"].iloc[-1] / frame["close"].iloc[0] - 1), 2),
        }
        for symbol, frame in prices.items()
    ])
    buyhold.to_csv(args.output / "buy_hold.csv", index=False)
    (args.output / "assumptions.json").write_text(json.dumps({
        "symbols": ASSETS,
        "dow_proxy": "DIA ETF, not the untradeable ^DJI index",
        "fractional_quantity": sorted(FRACTIONAL),
        "fractional_precision": 8,
        "price_source": "Yahoo Finance adjusted daily OHLC",
        "start_inclusive": args.start,
        "end_exclusive": args.end,
        "initial_cash_usd": args.cash,
        "one_way_friction_rate": args.friction,
        "entry": "daily high > prior 20-day high; next daily open fill; ADX priority",
        "fixed_stop": "signal close - 2 * prior simple ATR(14); fixed; close trigger; next daily open fill",
        "existing_exit": "daily low < prior 20-day low; next daily open fill",
        "chandelier_exit": "daily low < max since entry of (prior 22-day high - multiplier * prior simple ATR(22)); next daily open fill",
        "sizing": "remaining USD / (number of assets - held asset count); ETFs whole shares, crypto eight decimal places",
        "calendar": "ETFs trade on US stock sessions; spot crypto trades every calendar day; mixed basket is illustrative",
        "open_positions": "marked at latest available adjusted close",
    }, indent=2), encoding="utf-8")
    print("BASKET")
    print(pd.DataFrame(basket_rows).to_string(index=False))
    print("\nPER ASSET TOTAL RETURN (%)")
    print(pd.DataFrame(asset_rows).pivot(index="asset", columns="variant", values="total_return_pct").to_string())
    print("\nBUY AND HOLD")
    print(buyhold.to_string(index=False))
    print(f"Results: {args.output.resolve()}")


if __name__ == "__main__":
    main()

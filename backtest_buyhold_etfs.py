"""Buy QQQ, SPY, and DIA once, then hold; include standard Toss US stock fees."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backtest_exits import load_prices, toss_commission, toss_sec_fee


ASSETS = ("QQQ", "SPY", "DIA")


def buy_and_hold(prices: dict[str, pd.DataFrame], symbols: tuple[str, ...], initial_cash: float):
    cash = initial_cash
    quantities = {}
    buy_fees = 0.0
    dates = sorted(set().union(*(prices[symbol].index for symbol in symbols)))
    for symbol in symbols:
        bar = prices[symbol].iloc[0]
        price = float(bar["open"])
        budget = initial_cash / len(symbols)
        quantity = math.floor(budget / price)
        while quantity > 0 and quantity * price + toss_commission(quantity * price) > budget:
            quantity -= 1
        gross = quantity * price
        fee = toss_commission(gross)
        cash -= gross + fee
        buy_fees += fee
        quantities[symbol] = quantity
    last_close = {}
    equity = []
    for trade_date in dates:
        for symbol in symbols:
            if trade_date in prices[symbol].index:
                last_close[symbol] = float(prices[symbol].loc[trade_date, "close"])
        value = cash + sum(quantities[symbol] * last_close[symbol] for symbol in last_close)
        equity.append((trade_date, value))
    curve = pd.DataFrame(equity, columns=["date", "equity"]).set_index("date")
    last = curve.index[-1]
    sell_fees = sum(toss_commission(quantities[symbol] * last_close[symbol]) for symbol in symbols)
    sec_fees = sum(toss_sec_fee(quantities[symbol] * last_close[symbol], last) for symbol in symbols)
    final_equity = float(curve["equity"].iloc[-1])
    years = (last - curve.index[0]).days / 365.25
    peak = curve["equity"].cummax().clip(lower=initial_cash)
    summary = {
        "assets": "+".join(symbols),
        "start": curve.index[0].strftime("%Y-%m-%d"),
        "end": last.strftime("%Y-%m-%d"),
        "initial_cash": round(initial_cash, 2),
        "final_equity_held": round(final_equity, 2),
        "held_return_pct": round(100 * (final_equity / initial_cash - 1), 2),
        "held_annualized_pct": round(100 * ((final_equity / initial_cash) ** (1 / years) - 1), 2),
        "max_drawdown_pct": round(100 * float((curve["equity"] / peak - 1).min()), 2),
        "buy_commission_usd": round(buy_fees, 2),
        "hypothetical_sell_commission_usd": round(sell_fees, 2),
        "hypothetical_sec_fee_usd": round(sec_fees, 2),
        "hypothetical_liquidation_return_pct": round(
            100 * ((final_equity - sell_fees - sec_fees) / initial_cash - 1), 2
        ),
        "cash_leftover_usd": round(cash, 2),
        "quantities": quantities,
    }
    return summary, curve


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument(
        "--end", default=datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
        help="exclusive date; use 2026-10-13 to match the strategy backtest",
    )
    parser.add_argument("--cash", type=float, default=100_000)
    parser.add_argument("--output", type=Path, default=Path("backtest_results_etfs_buyhold"))
    args = parser.parse_args()
    if args.cash <= 0:
        parser.error("cash must be positive")
    prices = load_prices(args.start, args.end, ASSETS)
    args.output.mkdir(parents=True, exist_ok=True)
    basket, basket_curve = buy_and_hold(prices, ASSETS, args.cash)
    singles = []
    for symbol in ASSETS:
        summary, curve = buy_and_hold(prices, (symbol,), args.cash)
        singles.append(summary)
        curve.to_csv(args.output / f"{symbol}_equity.csv")
    pd.DataFrame([{key: value for key, value in basket.items() if key != "quantities"}]).to_csv(
        args.output / "basket_summary.csv", index=False,
    )
    pd.DataFrame([{key: value for key, value in row.items() if key != "quantities"} for row in singles]).to_csv(
        args.output / "per_asset_summary.csv", index=False,
    )
    basket_curve.to_csv(args.output / "basket_equity.csv")
    (args.output / "assumptions.json").write_text(json.dumps({
        "assets": ASSETS,
        "price_source": "Yahoo Finance adjusted daily OHLC",
        "start_inclusive": args.start,
        "end_exclusive": args.end,
        "initial_cash_usd": args.cash,
        "purchase": "first available adjusted open, equal dollar budget per ETF, whole shares, no rebalance",
        "valuation": "last available adjusted close; held return includes buy commission only",
        "fees": "Toss standard US stock commission 0.1% each side, cent-truncated, waived at gross <= $10; SEC sell fee from 2026-04-02",
        "liquidation": "hypothetical only; sell fees and SEC fee charged on final adjusted close",
        "basket_quantities": basket["quantities"],
        "single_quantities": {row["assets"]: row["quantities"] for row in singles},
    }, indent=2), encoding="utf-8")
    print("EQUAL-ALLOCATION BASKET")
    print(pd.DataFrame([basket]).drop(columns="quantities").to_string(index=False))
    print("\nEACH ETF ALONE")
    print(pd.DataFrame(singles).drop(columns="quantities").to_string(index=False))
    print(f"Results: {args.output.resolve()}")


if __name__ == "__main__":
    main()

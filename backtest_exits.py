"""Compare the existing exit with 22-day Chandelier exits on the watchlist.

Signals use completed daily bars. Orders fill at the next available daily open.
This is a research backtest; it does not submit brokerage orders.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from turtle_bot import SYMBOLS, calc_adx, calc_atr


@dataclass
class Position:
    quantity: float
    entry_date: str
    entry_price: float
    stop: float
    entry_fee: float = 0.0
    trailing_exit: float = float("-inf")


def toss_commission(gross: float) -> float:
    """Standard US stock commission: 0.1%, cent-truncated, waived up to $10."""
    amount = Decimal(str(gross))
    if amount <= Decimal("10"):
        return 0.0
    return float((amount * Decimal("0.001")).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def toss_sec_fee(gross: float, trade_date: pd.Timestamp) -> float:
    """Customer-paid sell-side SEC fee from 2026-04-02 per Toss's fee page."""
    if trade_date.date() < date(2026, 4, 2):
        return 0.0
    amount = max(Decimal("0.01"), Decimal(str(gross)) * Decimal("0.0000206"))
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def load_prices(start: str, end: str, symbols=None) -> dict[str, pd.DataFrame]:
    prices = {}
    failures = []
    for symbol in (SYMBOLS if symbols is None else symbols):
        try:
            data = yf.download(
                symbol, start=start, end=end, auto_adjust=True,
                progress=False, threads=False, timeout=20,
            )
            if data.empty or len(data) < 40:
                raise ValueError("fewer than 40 daily bars")
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            data.columns = [str(column).lower() for column in data.columns]
            data = data[["open", "high", "low", "close"]].dropna().copy()
            if (data <= 0).any().any():
                raise ValueError("nonpositive OHLC price")
            data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
            data = data[~data.index.duplicated(keep="last")].sort_index()
            data["high20"] = data["high"].shift(1).rolling(20).max()
            data["low20"] = data["low"].shift(1).rolling(20).min()
            data["high22"] = data["high"].shift(1).rolling(22).max()
            data["atr14"] = calc_atr(data, 14).shift(1)
            data["atr22"] = calc_atr(data, 22).shift(1)
            data["adx"] = calc_adx(data, 14).shift(1)
            prices[symbol] = data
        except Exception as exc:
            failures.append(f"{symbol}: {exc}")
    if failures:
        raise RuntimeError("Incomplete watchlist data; no comparison made:\n" + "\n".join(failures))
    return prices


def simulate(
    prices: dict[str, pd.DataFrame], kind: str, multiplier: int | None,
    initial_cash: float, friction: float, fractional_symbols=frozenset(), toss_fees: bool = False,
) -> tuple[dict, list[dict], pd.DataFrame]:
    if toss_fees and fractional_symbols:
        raise ValueError("Toss US stock fees cannot be applied to spot crypto")
    dates = sorted(set().union(*(frame.index for frame in prices.values())))
    cash = initial_cash
    held: dict[str, Position] = {}
    pending_buys: dict[str, tuple[float, float]] = {}
    pending_sells: dict[str, str] = {}
    last_close: dict[str, float] = {}
    trades = []
    curve = []
    broker_fees_paid = 0.0
    sec_fees_paid = 0.0
    for date in dates:
        bars = {symbol: frame.loc[date] for symbol, frame in prices.items() if date in frame.index}
        for symbol, reason in list(pending_sells.items()):
            if symbol not in bars:
                continue
            position = held.pop(symbol)
            price = float(bars[symbol]["open"])
            gross = position.quantity * price
            sell_fee = toss_commission(gross) if toss_fees else gross * friction
            sec_fee = toss_sec_fee(gross, date) if toss_fees else 0.0
            cash += gross - sell_fee - sec_fee
            broker_fees_paid += sell_fee
            sec_fees_paid += sec_fee
            trades.append({
                "symbol": symbol, "entry_date": position.entry_date,
                "exit_date": date.strftime("%Y-%m-%d"), "entry_price": position.entry_price,
                "exit_price": price, "quantity": position.quantity, "reason": reason,
                "buy_fee": position.entry_fee, "sell_fee": sell_fee, "sec_fee": sec_fee,
                "return_pct": 100 * ((gross - sell_fee - sec_fee) / (
                    position.quantity * position.entry_price + position.entry_fee
                ) - 1),
            })
            del pending_sells[symbol]
        for symbol, (stop, adx) in sorted(pending_buys.items(), key=lambda item: -item[1][1]):
            if symbol not in bars or symbol in held:
                continue
            slots = len(prices) - len(held)
            if slots <= 0:
                break
            price = float(bars[symbol]["open"])
            budget = cash / slots
            affordable = budget / price if toss_fees else budget / (price * (1 + friction))
            quantity = math.floor(affordable * 1e8) / 1e8 if symbol in fractional_symbols else math.floor(affordable)
            if toss_fees:
                while quantity > 0 and quantity * price + toss_commission(quantity * price) > budget:
                    quantity -= 1
            if quantity <= 0:
                continue
            gross = quantity * price
            buy_fee = toss_commission(gross) if toss_fees else gross * friction
            cash -= gross + buy_fee
            broker_fees_paid += buy_fee
            held[symbol] = Position(quantity, date.strftime("%Y-%m-%d"), price, stop, buy_fee)
        pending_buys = {}
        for symbol, bar in bars.items():
            last_close[symbol] = float(bar["close"])
            if symbol in held:
                position = held[symbol]
                if float(bar["close"]) <= position.stop:
                    pending_sells[symbol] = "fixed_atr_stop"
                elif kind == "existing":
                    if pd.notna(bar["low20"]) and float(bar["low"]) < float(bar["low20"]):
                        pending_sells[symbol] = "20_day_low"
                elif pd.notna(bar["high22"]) and pd.notna(bar["atr22"]):
                    raw = float(bar["high22"]) - multiplier * float(bar["atr22"])
                    position.trailing_exit = max(position.trailing_exit, raw)
                    if float(bar["low"]) < position.trailing_exit:
                        pending_sells[symbol] = "chandelier"
            elif pd.notna(bar["high20"]) and pd.notna(bar["atr14"]) and pd.notna(bar["adx"]):
                if float(bar["high"]) > float(bar["high20"]):
                    stop = float(bar["close"]) - 2 * float(bar["atr14"])
                    pending_buys[symbol] = (stop, float(bar["adx"]))
        equity = cash + sum(position.quantity * last_close[symbol] for symbol, position in held.items())
        curve.append((date, equity, cash, len(held)))
    daily = pd.DataFrame(curve, columns=["date", "equity", "cash", "positions"]).set_index("date")
    years = max((daily.index[-1] - daily.index[0]).days / 365.25, 1 / 365.25)
    total_return = daily["equity"].iloc[-1] / initial_cash - 1
    drawdown = daily["equity"] / daily["equity"].cummax() - 1
    summary = {
        "variant": "existing_20_day_low" if kind == "existing" else f"chandelier_22_atr_x{multiplier}",
        "start": daily.index[0].strftime("%Y-%m-%d"),
        "end": daily.index[-1].strftime("%Y-%m-%d"),
        "initial_cash": round(initial_cash, 2),
        "final_equity": round(float(daily["equity"].iloc[-1]), 2),
        "total_return_pct": round(100 * total_return, 2),
        "annualized_return_pct": round(100 * ((1 + total_return) ** (1 / years) - 1), 2),
        "max_drawdown_pct": round(100 * float(drawdown.min()), 2),
        "closed_trades": len(trades),
        "win_rate_pct": round(100 * sum(t["return_pct"] > 0 for t in trades) / len(trades), 2) if trades else None,
        "mean_trade_pct": round(sum(t["return_pct"] for t in trades) / len(trades), 2) if trades else None,
        "open_positions": len(held),
        "broker_commissions_usd": round(broker_fees_paid, 2),
        "sec_fees_usd": round(sec_fees_paid, 2),
    }
    return summary, trades, daily


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument(
        "--end", default=datetime.now(ZoneInfo("America/New_York")).date().isoformat(),
        help="exclusive end date; defaults to today's New York date",
    )
    parser.add_argument("--cash", type=float, default=100_000)
    parser.add_argument("--friction", type=float, default=0.001, help="one-way proportional cost")
    parser.add_argument("--toss-fees", action="store_true", help="use standard Toss US stock commissions and dated SEC sell fees")
    parser.add_argument("--output", type=Path, default=Path("backtest_results"))
    args = parser.parse_args()
    if args.cash <= 0 or not 0 <= args.friction < 1:
        parser.error("cash must be positive and friction must be in [0, 1)")
    prices = load_prices(args.start, args.end)
    args.output.mkdir(parents=True, exist_ok=True)
    all_summaries, all_trades, cost_comparison = [], [], []
    for kind, multiplier in [("existing", None)] + [("chandelier", x) for x in range(1, 6)]:
        summary, trades, curve = simulate(
            prices, kind, multiplier, args.cash, args.friction, toss_fees=args.toss_fees,
        )
        all_summaries.append(summary)
        all_trades.extend({"variant": summary["variant"], **trade} for trade in trades)
        curve.to_csv(args.output / f"{summary['variant']}_equity.csv")
        if args.toss_fees:
            old, _, _ = simulate(prices, kind, multiplier, args.cash, args.friction)
            cost_comparison.append({
                "variant": summary["variant"],
                "uniform_cost_return_pct": old["total_return_pct"],
                "toss_fee_return_pct": summary["total_return_pct"],
                "difference_pct_points": round(summary["total_return_pct"] - old["total_return_pct"], 2),
            })
    pd.DataFrame(all_summaries).to_csv(args.output / "summary.csv", index=False)
    if cost_comparison:
        pd.DataFrame(cost_comparison).to_csv(args.output / "cost_comparison.csv", index=False)
    pd.DataFrame(all_trades).to_csv(args.output / "trades.csv", index=False)
    (args.output / "assumptions.json").write_text(json.dumps({
        "symbols": list(SYMBOLS), "price_source": "Yahoo Finance adjusted daily OHLC",
        "start_inclusive": args.start, "end_exclusive": args.end,
        "initial_cash_usd": args.cash, "one_way_friction_rate": 0 if args.toss_fees else args.friction,
        "fee_model": (
            "Toss standard US stock commission: 0.1% each side, truncate below $0.01, waive order gross <= $10; "
            "SEC sell fee from 2026-04-02: max($0.01, 0.00206% of sale) rounded to cents"
            if args.toss_fees else "uniform proportional friction per side"
        ),
        "entry": "signal high > prior 20-day high; next open fill; ADX priority",
        "fixed_stop": "signal close - 2 * prior ATR(14), fixed; close trigger; next open fill",
        "existing_exit": "low < prior 20-day low; next open fill",
        "chandelier_exit": "low < max since entry of (prior 22-day high - multiplier * prior ATR(22)); next open fill",
        "sizing": "available cash / (55 - held watchlist symbols), whole shares",
        "open_positions": "marked at latest available adjusted close",
    }, indent=2), encoding="utf-8")
    print(pd.DataFrame(all_summaries).to_string(index=False))
    print(f"Results: {args.output.resolve()}")


if __name__ == "__main__":
    main()

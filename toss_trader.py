"""Run the turtle signals against a Toss Securities US brokerage account.

Default is a read-only preview. Live orders require both --live and
TOSS_LIVE_TRADING=YES, and must run on a host with a persistent state file.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from toss_client import TossClient
from turtle_bot import SYMBOLS, check_signal


NY = ZoneInfo("America/New_York")
STATE_PATH = Path(os.environ.get("TOSS_STATE_FILE", "toss_state.json"))


def positive_decimal(name: str) -> Decimal:
    try:
        value = Decimal(os.environ[name])
    except (KeyError, InvalidOperation) as exc:
        raise ValueError(f"Set {name} to a positive number") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"Set {name} to a positive number")
    return value


def position_budget(available_cash: Decimal, remaining_slots: int) -> Decimal:
    if remaining_slots < 1:
        return Decimal(0)
    budget = available_cash / remaining_slots
    if os.environ.get("TOSS_MAX_USD_PER_TRADE"):
        budget = min(budget, positive_decimal("TOSS_MAX_USD_PER_TRADE"))
    return budget


def positive_int(name: str) -> int:
    try:
        value = int(os.environ[name])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Set {name} to a positive integer") from exc
    if value < 1:
        raise ValueError(f"Set {name} to a positive integer")
    return value


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"managed": {}, "orders": {}}
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not isinstance(state.get("managed"), dict) or not isinstance(state.get("orders"), dict):
        raise ValueError("Invalid Toss state file; refusing to trade")
    return state


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name == "posix":
        temp.chmod(0o600)
    os.replace(temp, STATE_PATH)


def initialize_state():
    if not STATE_PATH.is_absolute():
        raise ValueError("TOSS_STATE_FILE must be an absolute path")
    client = TossClient(
        os.environ["TOSS_CLIENT_ID"],
        os.environ["TOSS_CLIENT_SECRET"],
        positive_int("TOSS_ACCOUNT_SEQ"),
    )
    client.validate_account()
    if STATE_PATH.exists():
        raise ValueError("State file already exists; refusing to overwrite")
    save_state({"managed": {}, "orders": {}})
    print(f"Initialized private state at {STATE_PATH}; existing holdings are unmanaged")


def market_window(now: datetime) -> bool:
    local = now.astimezone(NY)
    return local.weekday() < 5 and (local.hour, local.minute) >= (9, 40) and (
        local.hour, local.minute
    ) < (15, 30)


def latest_closed_signal(client: TossClient, symbol: str, now: datetime):
    candles = client.daily_candles(symbol)
    if not candles:
        return None
    today_ny = now.astimezone(NY).date()
    rows = []
    for candle in candles:
        if candle.get("currency") != "USD":
            raise ValueError(f"{symbol}: daily candle is not in USD")
        timestamp = datetime.fromisoformat(candle["timestamp"])
        if timestamp.tzinfo is None:
            raise ValueError(f"{symbol}: daily candle lacks timezone")
        trade_date = timestamp.astimezone(NY).date()
        if trade_date < today_ny:
            rows.append((trade_date, candle))
    if not rows:
        return None
    rows.sort(key=lambda row: row[0])
    signal_date = rows[-1][0]
    if today_ny - signal_date > timedelta(days=5):
        return None
    closed = pd.DataFrame(
        [
            {
                "open": float(c["openPrice"]),
                "high": float(c["highPrice"]),
                "low": float(c["lowPrice"]),
                "close": float(c["closePrice"]),
                "volume": float(c["volume"]),
            }
            for _, c in rows
        ]
    )
    signal = check_signal(closed)
    return (signal_date.isoformat(), signal) if signal else None


def quote_price(client: TossClient, symbol: str, side: str, now: datetime) -> Decimal:
    book = client.orderbook(symbol)
    if book.get("currency") != "USD":
        raise ValueError(f"{symbol}: orderbook is not in USD")
    updated = datetime.fromisoformat(book["timestamp"])
    if updated.tzinfo is None or abs((now - updated).total_seconds()) > 60:
        raise ValueError(f"{symbol}: stale orderbook")
    asks, bids = book.get("asks", []), book.get("bids", [])
    if not asks or not bids:
        raise ValueError(f"{symbol}: empty orderbook")
    ask, bid = Decimal(asks[0]["price"]), Decimal(bids[0]["price"])
    if bid <= 0 or ask < bid or (ask - bid) / bid > Decimal("0.01"):
        raise ValueError(f"{symbol}: invalid or wide spread")
    return ask if side == "BUY" else bid


def order_key(signal_date: str, symbol: str, side: str) -> str:
    return f"{signal_date}:{symbol}:{side}"


def client_order_id(signal_date: str, symbol: str, side: str) -> str:
    return f"turtle-{signal_date.replace('-', '')}-{symbol}-{side.lower()}"[:36]


def reconcile(client: TossClient, state: dict, holdings: dict, open_orders: list):
    open_ids = {order["orderId"] for order in open_orders}
    for record in state["orders"].values():
        symbol = record["symbol"]
        if record["side"] == "BUY" and record.get("status") == "HELD" and holdings.get(symbol, Decimal(0)) == 0:
            record["status"] = "CLOSED"
            continue
        if record.get("status") == "CLOSED":
            continue
        if not record.get("orderId"):
            continue  # Unknown POST outcome: manual reconciliation required.
        detail = client.order_detail(record["orderId"])
        filled = Decimal(detail["execution"]["filledQuantity"])
        status = detail["status"]
        if record["side"] == "BUY" and filled > 0:
            managed_qty = min(holdings.get(symbol, Decimal(0)), filled)
            if managed_qty > 0:
                state["managed"][symbol] = {
                    "stop": record["stop"],
                    "quantity": str(managed_qty),
                }
                record["status"] = "HELD"
            else:
                record["status"] = "AWAITING_HOLDING"
        if record["side"] == "SELL":
            managed = state["managed"].get(symbol)
            if managed:
                managed["quantity"] = str(min(Decimal(managed["quantity"]), holdings.get(symbol, Decimal(0))))
            if holdings.get(symbol, Decimal(0)) == 0:
                state["managed"].pop(symbol, None)
                record["status"] = "CLOSED"
        if record.get("status") not in {"HELD", "CLOSED", "AWAITING_HOLDING"}:
            if record["orderId"] in open_ids or status in {
                "PENDING", "PARTIAL_FILLED", "PENDING_CANCEL", "PENDING_REPLACE"
            }:
                record["status"] = "OPEN"
            elif status in {"CANCELED", "REJECTED"}:
                record["status"] = "CLOSED"
            else:
                record["status"] = "AWAITING_SELL" if record["side"] == "SELL" else "AWAITING_HOLDING"
    # A position can also have been closed manually in the Toss app.
    for symbol in list(state["managed"]):
        if holdings.get(symbol, Decimal(0)) == 0:
            state["managed"].pop(symbol)


def run(live: bool, *, client=None, now=None):
    injected_time = now is not None
    now = now or datetime.now(tz=NY)
    if live and os.environ.get("TOSS_LIVE_TRADING") != "YES":
        raise ValueError("Live orders require TOSS_LIVE_TRADING=YES")
    if live and not market_window(now):
        print("Outside 09:40-15:30 New York trading window; no orders")
        return
    if live and (not STATE_PATH.is_absolute() or not STATE_PATH.exists()):
        raise ValueError("Live mode requires an existing absolute TOSS_STATE_FILE")
    if live and STATE_PATH.resolve().is_relative_to(Path(__file__).resolve().parent):
        raise ValueError("Live state file must be outside the Git checkout")
    max_positions = (
        positive_int("TOSS_MAX_POSITIONS")
        if os.environ.get("TOSS_MAX_POSITIONS")
        else len(SYMBOLS)
    )
    max_orders = positive_int("TOSS_MAX_ORDERS_PER_RUN")
    if client is None:
        client = TossClient(
            os.environ["TOSS_CLIENT_ID"],
            os.environ["TOSS_CLIENT_SECRET"],
            positive_int("TOSS_ACCOUNT_SEQ"),
        )
    client.validate_account()
    state = load_state()
    items = client.holdings()
    holdings = {
        item["symbol"]: Decimal(item["quantity"])
        for item in items
        if item.get("marketCountry") == "US" and item.get("currency") == "USD"
    }
    open_orders = client.open_orders()
    open_symbols = {order["symbol"] for order in open_orders}
    reconcile(client, state, holdings, open_orders)
    if live:
        save_state(state)
    unresolved_symbols = {
        record["symbol"]
        for record in state["orders"].values()
        if record.get("status") in {
            "PREPARED", "SUBMITTED", "OPEN", "AWAITING_HOLDING", "AWAITING_SELL"
        }
    }
    candidates = []
    for symbol in SYMBOLS:
        try:
            result = latest_closed_signal(client, symbol, now)
            if result is None:
                continue
            signal_date, signal = result
            if not all(Decimal(str(signal[field])).is_finite() for field in ("price", "stop", "adx")):
                continue
            managed = state["managed"].get(symbol)
            side = None
            if managed and holdings.get(symbol, Decimal(0)) > 0:
                if Decimal(str(signal["price"])) <= Decimal(managed["stop"]) or signal["exit"]:
                    side = "SELL"
            elif holdings.get(symbol, Decimal(0)) == 0 and signal["entry"]:
                side = "BUY"
            if (
                side
                and symbol not in open_symbols
                and symbol not in unresolved_symbols
                and order_key(signal_date, symbol, side) not in state["orders"]
            ):
                candidates.append((0 if side == "SELL" else 1, -signal["adx"], symbol, side, signal_date, signal))
        except Exception as exc:
            print(f"SKIP {symbol}: signal error: {exc}")
    candidates.sort()
    order_count = 0
    occupied_symbols = {
        symbol for symbol, quantity in holdings.items() if symbol in SYMBOLS and quantity > 0
    }
    occupied_symbols.update(
        order["symbol"]
        for order in open_orders
        if order.get("side") == "BUY" and order["symbol"] in SYMBOLS
    )
    held_count = len(occupied_symbols)
    cash = Decimal(client.buying_power_usd())
    for _, _, symbol, side, signal_date, signal in candidates:
        if order_count >= max_orders:
            break
        quote_now = now if injected_time else datetime.now(tz=NY)
        if live and not market_window(quote_now):
            print("Trading window closed during scan; stopping")
            break
        if side == "BUY" and held_count >= max_positions:
            continue
        try:
            price = quote_price(client, symbol, side, quote_now)
            if side == "BUY":
                budget = position_budget(cash, len(SYMBOLS) - held_count)
                if budget <= 0:
                    continue
                quantity = int((min(budget, cash * Decimal("0.99")) / price).to_integral_value(rounding=ROUND_DOWN))
                if quantity < 1:
                    continue
            else:
                managed = state["managed"][symbol]
                quantity = int(
                    min(
                        holdings[symbol],
                        Decimal(managed["quantity"]),
                        Decimal(client.sellable_quantity(symbol)),
                    )
                )
                if quantity < 1:
                    continue
            key = order_key(signal_date, symbol, side)
            record = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": str(price),
                "stop": str(signal["stop"]),
                "clientOrderId": client_order_id(signal_date, symbol, side),
                "status": "PREPARED",
            }
            print(f"{'ORDER' if live else 'PREVIEW'} {side} {symbol} {quantity} @ {price} USD ({signal_date})")
            if live:
                # Persist before POST. A timeout has an unknown result and must not be blindly retried.
                state["orders"][key] = record
                save_state(state)
                result = client.create_limit_order(symbol, side, quantity, str(price), record["clientOrderId"])
                record["orderId"] = result["orderId"]
                record["status"] = "SUBMITTED"
                save_state(state)
            order_count += 1
            if side == "BUY":
                held_count += 1
                cash -= price * quantity
        except Exception as exc:
            print(f"SKIP {symbol}: order preparation/submission failed: {exc}")
            if live and order_key(signal_date, symbol, side) in state["orders"]:
                raise RuntimeError("Order outcome unknown; inspect Toss orders before another run") from exc
    print(f"{'Submitted' if live else 'Previewed'} {order_count} orders")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="submit live orders with the extra environment gate")
    parser.add_argument("--init-state", action="store_true", help="create the private state file without trading")
    args = parser.parse_args()
    if args.init_state and args.live:
        parser.error("--init-state and --live cannot be combined")
    if args.init_state:
        initialize_state()
    else:
        run(args.live)

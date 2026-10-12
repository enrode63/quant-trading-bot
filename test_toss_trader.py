import json
import os
import tempfile
import unittest
from datetime import datetime, date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import toss_trader
from toss_client import TossClient
from turtle_bot import build_msg2


NOW = datetime(2026, 10, 13, 10, 0, tzinfo=toss_trader.NY)
ORIGINAL_LATEST = toss_trader.latest_closed_signal
SIGNAL = {
    "price": 20,
    "stop": 18,
    "adx": 30,
    "entry": True,
    "exit": False,
}


class FakeClient:
    def __init__(self):
        self.orders = []

    def validate_account(self):
        pass

    def holdings(self):
        return []

    def open_orders(self):
        return []

    def buying_power_usd(self):
        return "1000"

    def orderbook(self, symbol):
        return {
            "currency": "USD",
            "timestamp": NOW.isoformat(),
            "asks": [{"price": "20", "volume": "100"}],
            "bids": [{"price": "19.99", "volume": "100"}],
        }

    def create_limit_order(self, *args):
        self.orders.append(args)
        return {"orderId": "broker-order-1"}

    def order_detail(self, order_id):
        return {"status": "PENDING", "execution": {"filledQuantity": "0"}}

    def sellable_quantity(self, symbol):
        return "5"


class TraderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_path = Path(self.temp.name) / "state.json"
        self.state_path.write_text(json.dumps({"managed": {}, "orders": {}}), encoding="utf-8")
        self.patches = [
            patch.object(toss_trader, "STATE_PATH", self.state_path),
            patch.object(toss_trader, "SYMBOLS", {"AAPL": "technology"}),
            patch.object(toss_trader, "latest_closed_signal", return_value=("2026-10-12", SIGNAL)),
            patch.dict(
                os.environ,
                {
                    "TOSS_MAX_POSITIONS": "3",
                    "TOSS_MAX_ORDERS_PER_RUN": "2",
                    "TOSS_LIVE_TRADING": "YES",
                },
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_preview_never_submits_or_changes_state(self):
        client = FakeClient()
        original = self.state_path.read_text(encoding="utf-8")
        toss_trader.run(False, client=client, now=NOW)
        self.assertEqual(client.orders, [])
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), original)

    def test_live_uses_remaining_slots_and_deduplicates(self):
        client = FakeClient()
        toss_trader.run(True, client=client, now=NOW)
        self.assertEqual(client.orders, [("AAPL", "BUY", 49, "20", "turtle-20261012-AAPL-buy")])
        toss_trader.run(True, client=client, now=NOW)
        self.assertEqual(len(client.orders), 1)

    def test_unresolved_order_blocks_next_signal_date(self):
        state = {
            "managed": {},
            "orders": {
                "2026-10-09:AAPL:BUY": {
                    "symbol": "AAPL", "side": "BUY", "stop": "17", "status": "PREPARED"
                }
            },
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        client = FakeClient()
        toss_trader.run(True, client=client, now=NOW)
        self.assertEqual(client.orders, [])

    def test_remaining_slot_budget_and_zero_slots(self):
        self.assertEqual(toss_trader.position_budget(Decimal("550"), 55), Decimal("10"))
        self.assertEqual(toss_trader.position_budget(Decimal("520"), 52), Decimal("10"))
        self.assertEqual(toss_trader.position_budget(Decimal("100"), 0), Decimal("0"))

    def test_us_holding_outside_watchlist_does_not_reduce_slots(self):
        client = FakeClient()
        client.holdings = lambda: [{
            "symbol": "TSLA", "quantity": "2", "marketCountry": "US", "currency": "USD"
        }]
        watchlist = {"AAPL": "tech", "MSFT": "tech", "NVDA": "tech"}
        with patch.object(toss_trader, "SYMBOLS", watchlist), patch.object(
            toss_trader, "latest_closed_signal",
            side_effect=lambda client, symbol, now: ("2026-10-12", SIGNAL) if symbol == "AAPL" else None,
        ):
            toss_trader.run(True, client=client, now=NOW)
        self.assertEqual(client.orders[0][2], 16)  # floor((1000 / 3) / 20)

    def test_each_buy_recalculates_cash_per_remaining_slot(self):
        client = FakeClient()
        watchlist = {"AAPL": "tech", "MSFT": "tech", "NVDA": "tech"}
        with patch.object(toss_trader, "SYMBOLS", watchlist), patch.object(
            toss_trader, "latest_closed_signal",
            side_effect=lambda client, symbol, now: ("2026-10-12", SIGNAL) if symbol != "NVDA" else None,
        ):
            toss_trader.run(True, client=client, now=NOW)
        self.assertEqual([(o[0], o[2]) for o in client.orders], [("AAPL", 16), ("MSFT", 17)])

    def test_sell_is_capped_to_strategy_managed_shares(self):
        self.state_path.write_text(
            json.dumps({"managed": {"AAPL": {"stop": "18", "quantity": "2"}}, "orders": {}}),
            encoding="utf-8",
        )
        client = FakeClient()
        client.holdings = lambda: [{
            "symbol": "AAPL", "quantity": "5", "marketCountry": "US", "currency": "USD"
        }]
        signal = dict(SIGNAL, exit=True, entry=False)
        with patch.object(toss_trader, "latest_closed_signal", return_value=("2026-10-12", signal)):
            toss_trader.run(True, client=client, now=NOW)
        self.assertEqual(client.orders, [("AAPL", "SELL", 2, "19.99", "turtle-20261012-AAPL-sell")])

    def test_toss_daily_signal_excludes_unfinished_candle(self):
        candles = []
        for days_ago in range(40, -1, -1):
            day = date(2026, 10, 13) - timedelta(days=days_ago)
            high = 200 if days_ago == 1 else (300 if days_ago == 0 else 100)
            candles.append(
                {
                    "timestamp": f"{day.isoformat()}T13:00:00+09:00",
                    "currency": "USD",
                    "openPrice": "90", "highPrice": str(high),
                    "lowPrice": "80", "closePrice": "95", "volume": "1000",
                }
            )
        client = FakeClient()
        client.daily_candles = lambda symbol: candles
        signal_date, signal = ORIGINAL_LATEST(client, "AAPL", NOW)
        self.assertEqual(signal_date, "2026-10-12")
        self.assertTrue(signal["entry"])

    def test_discord_loss_exit_is_not_called_profit(self):
        message = build_msg2(
            "2026-10-13",
            [{"symbol": "AAPL", "sector": "tech", "entry_price": 100, "sig": {"price": 95, "stop": 90}}],
            [],
        )
        self.assertIn("**손절**", message)
        self.assertIn("20일 저점 이탈", message)
        self.assertNotIn("**익절**", message)

    def test_order_uses_account_header_and_idempotency_key(self):
        class Response:
            status_code = 200
            headers = {}

            def __init__(self, payload):
                self.payload = payload

            def json(self):
                return self.payload

        class Session:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append(("token", url, kwargs))
                return Response({"access_token": "test-token", "expires_in": 86400})

            def request(self, method, url, **kwargs):
                self.calls.append((method, url, kwargs))
                return Response({"result": {"orderId": "123"}})

        session = Session()
        client = TossClient("id", "secret", 3, session=session)
        client.create_limit_order("AAPL", "BUY", 2, "20.01", "test-order-1")
        method, url, args = session.calls[-1]
        self.assertEqual((method, url), ("POST", "https://openapi.tossinvest.com/api/v1/orders"))
        self.assertEqual(args["headers"]["X-Tossinvest-Account"], "3")
        self.assertEqual(args["json"]["clientOrderId"], "test-order-1")
        self.assertEqual(args["json"]["quantity"], "2")
        self.assertEqual(args["json"]["price"], "20.01")


if __name__ == "__main__":
    unittest.main()

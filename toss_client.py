"""Small, account-scoped client for the official Toss Securities Open API."""

from __future__ import annotations

import time
from typing import Any

import requests


BASE_URL = "https://openapi.tossinvest.com"


class TossAPIError(RuntimeError):
    pass


class TossClient:
    def __init__(self, client_id: str, client_secret: str, account_seq: int, session=None):
        if not client_id or not client_secret or account_seq < 1:
            raise ValueError("Toss credentials and a positive account sequence are required")
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_seq = account_seq
        self.session = session or requests.Session()
        self._token: str | None = None
        self._expires_at = 0.0

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        response = self.session.post(
            BASE_URL + "/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=15,
        )
        self._check(response)
        payload = response.json()
        self._token = payload["access_token"]
        self._expires_at = time.monotonic() + max(0, int(payload["expires_in"]) - 60)
        return self._token

    @staticmethod
    def _check(response):
        if response.status_code >= 400:
            # Broker errors can contain sensitive account data; log only status and request ID.
            request_id = response.headers.get("X-Request-Id", "unknown")
            raise TossAPIError(f"Toss API HTTP {response.status_code}; requestId={request_id}")

    def _request(self, method: str, path: str, *, params=None, body=None, account=True) -> Any:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if account:
            headers["X-Tossinvest-Account"] = str(self.account_seq)
        for attempt in range(3):
            response = self.session.request(
                method, BASE_URL + path, headers=headers, params=params, json=body, timeout=15
            )
            if method != "GET" or response.status_code != 429 or attempt == 2:
                break
            try:
                delay = float(response.headers.get("Retry-After", "1"))
            except ValueError:
                delay = 1.0
            time.sleep(min(5.0, max(0.1, delay)))
        self._check(response)
        payload = response.json()
        if "result" not in payload:
            raise TossAPIError(f"Missing result in {path} response")
        return payload["result"]

    def validate_account(self):
        accounts = self._request("GET", "/api/v1/accounts", account=False)
        if not any(
            int(a["accountSeq"]) == self.account_seq and a.get("accountType") == "BROKERAGE"
            for a in accounts
        ):
            raise TossAPIError("Configured account is not an available brokerage account")

    def holdings(self):
        return self._request("GET", "/api/v1/holdings")["items"]

    def open_orders(self):
        return self._request("GET", "/api/v1/orders", params={"status": "OPEN"})["orders"]

    def order_detail(self, order_id: str):
        return self._request("GET", f"/api/v1/orders/{order_id}")

    def buying_power_usd(self):
        result = self._request("GET", "/api/v1/buying-power", params={"currency": "USD"})
        if result.get("currency") != "USD":
            raise TossAPIError("Unexpected buying-power currency")
        return result["cashBuyingPower"]

    def sellable_quantity(self, symbol: str):
        return self._request("GET", "/api/v1/sellable-quantity", params={"symbol": symbol})[
            "sellableQuantity"
        ]

    def orderbook(self, symbol: str):
        return self._request("GET", "/api/v1/orderbook", params={"symbol": symbol}, account=False)

    def daily_candles(self, symbol: str):
        return self._request(
            "GET",
            "/api/v1/candles",
            params={"symbol": symbol, "interval": "1d", "count": 70, "adjusted": "true"},
            account=False,
        )["candles"]

    def create_limit_order(self, symbol: str, side: str, quantity: int, price: str, client_order_id: str):
        if side not in {"BUY", "SELL"} or quantity < 1:
            raise ValueError("Invalid order direction or quantity")
        return self._request(
            "POST",
            "/api/v1/orders",
            body={
                "symbol": symbol,
                "side": side,
                "orderType": "LIMIT",
                "timeInForce": "DAY",
                "quantity": str(quantity),
                "price": price,
                "clientOrderId": client_order_id,
            },
        )

"""Read-only Toss connection check. Never sends an order or saves credentials."""

from __future__ import annotations

import argparse
import getpass
import os

from toss_client import TossClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-seq", type=int, help="also show USD buying power for this account")
    args = parser.parse_args()

    client_id = os.environ.get("TOSS_CLIENT_ID") or input("Toss client ID: ").strip()
    client_secret = os.environ.get("TOSS_CLIENT_SECRET") or getpass.getpass("Toss client secret: ").strip()
    client = TossClient(client_id, client_secret, args.account_seq if args.account_seq is not None else 1)
    accounts = [a for a in client.accounts() if a.get("accountType") == "BROKERAGE"]
    if not accounts:
        raise SystemExit("No brokerage account was returned by Toss")
    print("Connected. Brokerage accountSeq values:")
    for account in accounts:
        print(f"  {account['accountSeq']}")
    if args.account_seq is not None:
        client.validate_account()
        print(f"USD cash buying power: {client.buying_power_usd()}")


if __name__ == "__main__":
    main()

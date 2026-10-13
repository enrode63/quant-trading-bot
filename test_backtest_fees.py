"""Check the Toss fee rules used in the ETF backtest."""

import unittest

import pandas as pd

from backtest_exits import toss_commission, toss_sec_fee


class TossFeeTests(unittest.TestCase):
    def test_commission_waiver_and_cent_truncation(self):
        self.assertEqual(toss_commission(10), 0)
        self.assertEqual(toss_commission(10.01), 0.01)
        self.assertEqual(toss_commission(1234.56), 1.23)

    def test_sec_fee_effective_date_and_minimum(self):
        self.assertEqual(toss_sec_fee(100_000, pd.Timestamp("2026-04-01")), 0)
        self.assertEqual(toss_sec_fee(100, pd.Timestamp("2026-04-02")), 0.01)
        self.assertEqual(toss_sec_fee(100_000, pd.Timestamp("2026-04-02")), 2.06)


if __name__ == "__main__":
    unittest.main()

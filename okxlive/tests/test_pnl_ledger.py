import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from pnl_ledger import BTCPnLLedger


BASELINE = Path(__file__).resolve().parents[1] / "data" / "btc_pnl_baseline.json"
LOCAL_TRADE = {
    "time": "2026-11-15T06:14:52.820258+08:00",
    "direction": "short",
    "sz": 0.01,
    "entry_price": 66986.7,
    "exit_price": 63924.8,
}
LOCAL_TP1 = {
    "time": "2026-11-15T00:49:06.571818+08:00",
    "direction": "short",
    "sz": 0.01,
    "entry_price": 66986.7,
    "exit_price": 65040.0,
}


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.last_params = None

    def _get(self, path, params):
        self.calls += 1
        self.last_params = dict(params)
        return {"data": self.rows}


class BTCPnLLedgerTests(unittest.TestCase):
    def test_local_gap_is_provisional_until_okx_bill_arrives(self):
        with tempfile.TemporaryDirectory() as folder:
            cache_path = str(Path(folder) / "ledger.json")
            ledger = BTCPnLLedger(str(BASELINE), cache_path)
            no_bills = FakeClient([])
            provisional = ledger.get_total(no_bills, [LOCAL_TRADE, LOCAL_TP1], 0.01)
            self.assertIn("end", no_bills.last_params)
            self.assertAlmostEqual(provisional["booked_usdt"], 16.49314581)
            self.assertAlmostEqual(provisional["provisional_usdt"], 0.50086)
            self.assertAlmostEqual(provisional["net_usdt"], 16.99400581)
            ledger.get_total(no_bills, [LOCAL_TRADE, LOCAL_TP1], 0.01)
            self.assertEqual(no_bills.calls, 1)

            ledger.next_refresh = 0
            booked_bill = {
                "billId": "4100086268727644161",
                "instId": "BTC-USDT-SWAP",
                "ccy": "USDT",
                "ts": str(int(datetime.fromisoformat("2026-11-15T06:14:52+08:00").timestamp() * 1000)),
                "balChg": "0.29000000",
            }
            booked = ledger.get_total(FakeClient([booked_bill]), [LOCAL_TRADE, LOCAL_TP1], 0.01)
            self.assertAlmostEqual(booked["booked_usdt"], 16.78314581)
            self.assertEqual(booked["provisional_usdt"], 0)

            reloaded = BTCPnLLedger(str(BASELINE), cache_path)
            again = reloaded.get_total(FakeClient([booked_bill]), [LOCAL_TRADE, LOCAL_TP1], 0.01)
            self.assertAlmostEqual(again["booked_usdt"], 16.78314581)

    def test_only_btc_usdt_bills_are_added(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = BTCPnLLedger(str(BASELINE), str(Path(folder) / "ledger.json"))
            other = {
                "billId": "4100086268727644161",
                "instId": "ETH-USDT-SWAP",
                "ccy": "USDT",
                "ts": "1794704092000",
                "balChg": "100",
            }
            total = ledger.get_total(FakeClient([other]), [], 0.01)
            self.assertAlmostEqual(total["booked_usdt"], 16.49314581)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from realized_pnl_curve import RealizedPnLCurve


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def _get(self, path, params):
        self.calls.append((path, dict(params)))
        return {"data": self.rows}


class FailedClient:
    def _get(self, path, params):
        return None


class RealizedPnLCurveTests(unittest.TestCase):
    def test_replays_only_instrument_bills_and_ends_at_total_pnl(self):
        with tempfile.TemporaryDirectory() as folder:
            curve = RealizedPnLCurve(str(Path(folder) / "curve.json"), "BTC-USDT-SWAP", "USDT")
            now_ms = 10_000_000_000_000
            start = now_ms - 100_000
            rows = [
                {"billId": "12", "ts": str(start + 3000), "instId": "BTC-USDT-SWAP", "ccy": "USDT", "balChg": "-2"},
                {"billId": "11", "ts": str(start + 2000), "instId": "BTC-USDT-SWAP", "ccy": "USDT", "balChg": "5"},
                {"billId": "10", "ts": str(start + 1000), "instId": "ETH-USDT-SWAP", "ccy": "USDT", "balChg": "100"},
            ]
            client = FakeClient(rows)
            curve.refresh(client, now_ms=now_ms)
            self.assertEqual(client.calls[0][1]["instId"], "BTC-USDT-SWAP")
            summary = {"booked_usdt": 16, "net_usdt": 17, "provisional_usdt": 1,
                       "as_of_time": datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat(),
                       "baseline_bill_id": "9", "last_bill_id": "12"}
            result = curve.series(summary)
            self.assertTrue(result["ready"])
            self.assertEqual([item["pnl"] for item in result["history"]], [13, 18, 16])
            self.assertEqual(result["live_pnl"], 17)
            self.assertEqual(len(RealizedPnLCurve(str(Path(folder) / "curve.json"), "BTC-USDT-SWAP", "USDT").bills), 2)
            # Exchange bill time may be slightly ahead of the server clock.
            summary["as_of_time"] = datetime.fromtimestamp((now_ms + 60_000) / 1000, timezone.utc).isoformat()
            self.assertTrue(curve.series(summary)["ready"])

    def test_waits_until_bill_cache_catches_total_pnl(self):
        with tempfile.TemporaryDirectory() as folder:
            curve = RealizedPnLCurve(str(Path(folder) / "curve.json"), "BTC-USDT-SWAP", "USDT")
            now_ms = 10_000_000_000_000
            curve.refresh(FakeClient([]), now_ms=now_ms)
            summary = {"booked_usdt": 16, "net_usdt": 16, "provisional_usdt": 0,
                       "as_of_time": datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat(),
                       "baseline_bill_id": "9", "last_bill_id": "12"}
            self.assertFalse(curve.series(summary)["ready"])
            curve._worker(FailedClient())
            self.assertEqual(curve.series(summary)["status"], "query_failed")


if __name__ == "__main__":
    unittest.main()

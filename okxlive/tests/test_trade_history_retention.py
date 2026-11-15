import tempfile
import unittest
from pathlib import Path

from state_manager import StateManager


class TradeHistoryRetentionTests(unittest.TestCase):
    def test_closed_trades_are_not_truncated_after_200_records(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "trader_state.json")
            state = StateManager(path=path)
            for index in range(205):
                state.add_trade_record({"time": str(index), "direction": "long"})
            state.save()

            restored = StateManager(path=path)
            self.assertEqual(len(restored.get_trade_history()), 205)
            self.assertEqual(restored.get_trade_history()[0]["time"], "204")
            self.assertEqual(restored.get_trade_history()[-1]["time"], "0")


if __name__ == "__main__":
    unittest.main()

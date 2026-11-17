import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dashboard_state import DashboardStateReader


class DashboardStateReaderTests(unittest.TestCase):
    def test_partial_write_keeps_last_valid_history_then_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "trader_state.json"
            history_path = Path(folder) / "trade_history.json"
            equity_path = Path(folder) / "equity_history.json"
            history_path.write_text(json.dumps([{"id": 1}]), encoding="utf-8")
            equity_path.write_text(json.dumps({"equity_history": []}), encoding="utf-8")

            reader = DashboardStateReader(str(state_path))
            self.assertEqual(reader.get_trade_history(), [{"id": 1}])
            with patch("dashboard_state.json.load", side_effect=AssertionError("unchanged file was parsed")):
                reader.reload()

            history_path.write_text("[", encoding="utf-8")
            with patch("dashboard_state.logger.error") as log_error:
                reader.reload()
                log_error.assert_called_once()
            self.assertEqual(reader.get_trade_history(), [{"id": 1}])

            history_path.write_text(json.dumps([{"id": 2}]), encoding="utf-8")
            reader.reload()
            self.assertEqual(reader.get_trade_history(), [{"id": 2}])


if __name__ == "__main__":
    unittest.main()

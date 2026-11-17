"""Read trader output without allowing a partial write to stop the dashboard."""

import json
import logging
import os
import time


logger = logging.getLogger(__name__)


class DashboardStateReader:
    def __init__(self, state_path: str):
        folder = os.path.dirname(state_path)
        self.history_path = os.path.join(folder, "trade_history.json")
        self.equity_path = os.path.join(folder, "equity_history.json")
        self._history = []
        self._equity = {"equity_history": []}
        self._failed_paths = set()
        self.reload()

    def _read(self, path: str, expected_type: type, previous):
        for attempt in range(3):
            try:
                with open(path, encoding="utf-8") as file:
                    value = json.load(file)
                if not isinstance(value, expected_type):
                    raise ValueError(f"expected {expected_type.__name__}")
                self._failed_paths.discard(path)
                return value
            except FileNotFoundError:
                return previous
            except (OSError, ValueError) as exc:
                if attempt < 2:
                    time.sleep(0.02)
                elif path not in self._failed_paths:
                    logger.error("看板读取 %s 失败，暂用上次有效数据: %s", path, exc)
                    self._failed_paths.add(path)
        return previous

    def reload(self):
        self._history = self._read(self.history_path, list, self._history)
        self._equity = self._read(self.equity_path, dict, self._equity)

    def get_trade_history(self) -> list:
        return self._history

    def get_equity_data(self) -> dict:
        return self._equity

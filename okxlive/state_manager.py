"""
state_manager.py
将策略状态（持仓 entry、统计数据）持久化到本地 JSON 文件
程序重启后可恢复状态
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STATE = {
    "long_entries":           [],
    "short_entries":          [],
    "completed_long_trades":  0,
    "completed_short_trades": 0,
    "leverage_set":           False,
}


class StateManager:
    def __init__(self, path: str = "state/trader_state.json"):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"状态已恢复自: {self.path}")
                return data
            except Exception as e:
                logger.warning(f"状态文件读取失败，使用默认值: {e}")
        return dict(DEFAULT_STATE)

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"状态保存失败: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value

    def inc(self, key: str, delta: int = 1):
        self._data[key] = self._data.get(key, 0) + delta

    def dump(self) -> dict:
        return dict(self._data)

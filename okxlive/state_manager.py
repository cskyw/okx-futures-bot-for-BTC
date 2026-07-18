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
    "dashboard_start_time":   None,
}


class StateManager:
    def __init__(self, path: str = "state/trader_state.json"):
        self.path = path
        self.history_path = path.replace("trader_state.json", "trade_history.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._data = self._load(self.path, dict(DEFAULT_STATE))
        self._history = self._load(self.history_path, [])

        if not self._data.get("dashboard_start_time"):
            from datetime import datetime, timezone
            self._data["dashboard_start_time"] = datetime.now(timezone.utc).isoformat()
            self.save()

    def _load(self, filepath: str, default: Any) -> Any:
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data
            except Exception as e:
                logger.warning(f"状态文件读取失败，使用默认值: {e}")
        return default

    def reload(self):
        self._data = self._load(self.path, dict(DEFAULT_STATE))
        self._history = self._load(self.history_path, [])

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            with open(self.history_path, "w", encoding="utf-8") as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
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

    def get_trade_history(self) -> list:
        return self._history

    def add_trade_record(self, record: dict, max_records: int = 200):
        """记录历史交割单，头部插入保证最新的在最前，限制最大长度"""
        self._history.insert(0, record)
        if len(self._history) > max_records:
            self._history = self._history[:max_records]

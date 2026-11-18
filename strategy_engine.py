"""
strategy_engine.py
从 K 线数据计算均线信号，与 backtrader 策略逻辑完全对齐
"""

import logging
import numpy as np
from typing import List, Dict

logger = logging.getLogger(__name__)


class StrategyEngine:
    def __init__(self, config: dict):
        self.ma_fast = config.get("ma_fast", 5)
        self.ma_slow = config.get("ma_slow", 10)

    def compute(self, klines: List[Dict]) -> Dict:
        """
        输入升序 K 线列表，输出信号字典
        """
        closes = [k["close"] for k in klines]
        n      = len(closes)

        def sma2(period):
            """返回 (当前SMA, 上一根SMA)"""
            if n < period + 1:
                return float("nan"), float("nan")
            cur  = float(np.mean(closes[-period:]))
            prev = float(np.mean(closes[-(period+1):-1]))
            return cur, prev

        ma5_cur,   ma5_prev   = sma2(self.ma_fast)
        ma10_cur,  ma10_prev  = sma2(self.ma_slow)
        ma20_cur,  _          = sma2(20)
        ma30_cur,  _          = sma2(30)
        ma60_cur,  _          = sma2(60)
        ma120_cur, _          = sma2(120)
        ma180_cur, _          = sma2(180)
        ma240_cur, _          = sma2(240)

        price  = closes[-1]
        prev_c = closes[-2] if n >= 2 else price

        # CrossOver(close, MA5)
        cross5 = 0
        if not (np.isnan(ma5_cur) or np.isnan(ma5_prev)):
            if prev_c <= ma5_prev and price > ma5_cur:
                cross5 = 1
            elif prev_c >= ma5_prev and price < ma5_cur:
                cross5 = -1

        # CrossOver(close, MA10)
        cross10 = 0
        if not (np.isnan(ma10_cur) or np.isnan(ma10_prev)):
            if prev_c <= ma10_prev and price > ma10_cur:
                cross10 = 1
            elif prev_c >= ma10_prev and price < ma10_cur:
                cross10 = -1

        return {
            "price":   price,
            "ma5":     ma5_cur,
            "ma10":    ma10_cur,
            "ma20":    ma20_cur,
            "ma30":    ma30_cur,
            "ma60":    ma60_cur,
            "ma120":   ma120_cur,
            "ma180":   ma180_cur,
            "ma240":   ma240_cur,
            "cross5":  cross5,
            "cross10": cross10,
        }

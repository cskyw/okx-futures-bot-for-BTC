"""
okx_client.py
OKX REST API 封装 —— 合约 + 双向持仓 + 杠杆版本

关键概念：
- instId   : BTC-USDT-SWAP（永续合约）
- tdMode   : cross（全仓）/ isolated（逐仓）
- posSide  : long（多头）/ short（空头）—— 双向持仓模式
- sz       : 张数（合约张数，1张 = 0.01 BTC）
- 开多     : side=buy,  posSide=long
- 平多     : side=sell, posSide=long
- 开空     : side=sell, posSide=short
- 平空     : side=buy,  posSide=short
"""

import hmac
import base64
import hashlib
import json
import logging
import requests
from datetime import datetime, timezone
from typing import Optional
import math

logger = logging.getLogger(__name__)

# 延迟导入避免循环依赖，仅用于 get_positions 默认杠杆 fallback
try:
    from config import CONFIG as _CONFIG
except ImportError:
    _CONFIG = {}

BASE_URL = "https://www.okx.com"


class OKXClient:
    def __init__(
        self,
        api_key:    str,
        secret_key: str,
        passphrase: str,
        simulated:  bool = False,
    ):
        self.api_key    = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.simulated  = simulated
        self.session    = requests.Session()
        self.session.headers.update({
            "Content-Type":          "application/json",
            "x-simulated-trading":   "1" if simulated else "0",
        })

    # ==================== 签名 ====================
    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        msg = f"{timestamp}{method.upper()}{path}{body}"
        mac = hmac.new(
            self.secret_key.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode()

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return {
            "OK-ACCESS-KEY":        self.api_key,
            "OK-ACCESS-SIGN":       self._sign(ts, method, path, body),
            "OK-ACCESS-TIMESTAMP":  ts,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
        }

    # ==================== HTTP ====================
    def _get(self, path: str, params: dict = None) -> Optional[dict]:
        qs  = ("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""
        url = BASE_URL + path
        try:
            resp = self.session.get(
                url,
                headers=self._headers("GET", path + qs),
                params=params,
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != "0":
                logger.error(f"GET {path} error: {data}")
                return None
            return data
        except Exception as e:
            logger.error(f"GET {path} exception: {e}")
            return None

    def _post(self, path: str, body: dict) -> Optional[dict]:
        body_str = json.dumps(body)
        url      = BASE_URL + path
        try:
            resp = self.session.post(
                url,
                headers={**self._headers("POST", path, body_str), "Content-Type": "application/json"},
                data=body_str,
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != "0":
                logger.error(f"POST {path} error: {data}")
                return None
            return data
        except Exception as e:
            logger.error(f"POST {path} exception: {e}")
            return None

    # ==================== 杠杆设置 ====================
    def set_leverage(self, instId: str, lever: int, td_mode: str) -> bool:
        """
        设置合约杠杆倍数
        双向持仓需要分别对 long / short 设置
        """
        success = True
        for pos_side in ["long", "short"]:
            body = {
                "instId":  instId,
                "lever":   str(lever),
                "mgnMode": td_mode,
                "posSide": pos_side,
            }
            data = self._post("/api/v5/account/set-leverage", body)
            if data:
                logger.info(f"杠杆设置成功: {instId} {pos_side} {lever}x")
            else:
                logger.error(f"杠杆设置失败: {instId} {pos_side}")
                success = False
        return success

    # ==================== 行情 ====================
    def get_klines(self, instId: str, bar: str = "1H", limit: int = 300) -> list:
        """
        返回按时间升序排列的 K 线列表
        OKX 返回降序，此处反转为升序
        """
        path   = "/api/v5/market/candles"
        params = {"instId": instId, "bar": bar, "limit": str(limit)}
        data   = self._get(path, params)
        if not data:
            return []

        rows = []
        for item in reversed(data["data"]):
            rows.append({
                "ts":     int(item[0]),
                "open":   float(item[1]),
                "high":   float(item[2]),
                "low":    float(item[3]),
                "close":  float(item[4]),
                "volume": float(item[5]),
            })
        return rows

    # ==================== 账户 ====================
    def get_account_balance(self, ccy: str = "USDT") -> dict:
        """
        返回合约账户信息
        totalEq  : 账户总权益（USDT）
        availBal : 可用保证金
        """
        path = "/api/v5/account/balance"
        data = self._get(path, {"ccy": ccy})
        if not data or not data["data"]:
            return {"totalEq": 0.0, "availBal": 0.0}

        detail    = data["data"][0]
        total_eq  = float(detail.get("totalEq", 0))
        avail_bal = 0.0
        for d in detail.get("details", []):
            if d.get("ccy") == ccy:
                avail_bal = float(d.get("availBal", 0))
                break
        return {"totalEq": total_eq, "availBal": avail_bal}

    def get_positions(self, instId: str) -> dict:
        """
        获取当前合约持仓，返回完整信息供启动同步使用
        返回格式：
        {
          "long":  { "sz": float, "avgPx": float, "upl": float, "lever": int } | None,
          "short": { "sz": float, "avgPx": float, "upl": float, "lever": int } | None,
        }
        sz    : 持仓张数
        avgPx : 开仓均价
        upl   : 未实现盈亏（USDT）
        lever : 当前杠杆倍数
        """
        path = "/api/v5/account/positions"
        data = self._get(path, {"instId": instId})
        result = {"long": None, "short": None}
        if not data:
            return result
        for pos in data.get("data", []):
            ps  = pos.get("posSide", "")
            sz  = float(pos.get("pos", 0))
            if sz == 0:
                continue
            info = {
                "sz":    sz,
                "avgPx": float(pos.get("avgPx", 0) or 0),
                "upl":   float(pos.get("upl",   0) or 0),
                "lever": int(float(pos.get("lever", _CONFIG.get("lever", 5)) or 5)),
            }
            if ps == "long":
                result["long"] = info
            elif ps == "short":
                result["short"] = info
        return result

    # ==================== 下单（合约双向持仓）====================
    def _contracts_from_usdt(self, usdt_amount: float, price: float, ct_val: float, lever: int) -> int:
        """
        根据 USDT 保证金计算合约张数
        公式：张数 = (USDT保证金 × 杠杆) / (合约面值 × 价格)
        """
        if price <= 0 or ct_val <= 0:
            return 0
        raw = (usdt_amount * lever) / (ct_val * price)
        # sz  = max(1, int(raw))   # 最少 1 张，向下取整避免超额
        sz = math.floor(raw * 100) / 100
        sz = max(0.02, sz)
        return sz

    # def open_long(
    #     self,
    #     instId:  str,
    #     usdt_margin: float,   # 投入的保证金（USDT）
    #     price:   float,
    #     ct_val:  float,
    #     lever:   int,
    #     td_mode: str = "cross",
    # ) -> Optional[int]:
    #     """
    #     开多：buy + posSide=long
    #     返回实际下单张数，失败返回 None
    #     """
    #     sz = self._contracts_from_usdt(usdt_margin, price, ct_val, lever)
    #     logger.info(f"[开多] 保证金={usdt_margin:.2f}U price={price:.2f} → {sz}张")
    #     body = {
    #         "instId":  instId,
    #         "tdMode":  td_mode,
    #         "side":    "buy",
    #         "posSide": "long",
    #         "ordType": "market",
    #         "sz":      str(sz),
    #     }
    #     data = self._post("/api/v5/trade/order", body)
    #     if data and data["data"]:
    #         logger.info(f"[开多成功] ordId={data['data'][0].get('ordId')} sz={sz}张")
    #         return sz
    #     return None

    # def close_long(
    #     self,
    #     instId:  str,
    #     sz:      int,          # 平仓张数
    #     td_mode: str = "cross",
    # ) -> bool:
    #     """平多：sell + posSide=long"""
    #     sz = max(1, int(sz))
    #     logger.info(f"[平多] sz={sz}张")
    #     body = {
    #         "instId":  instId,
    #         "tdMode":  td_mode,
    #         "side":    "sell",
    #         "posSide": "long",
    #         "ordType": "market",
    #         "sz":      str(sz),
    #     }
    #     data = self._post("/api/v5/trade/order", body)
    #     if data and data["data"]:
    #         logger.info(f"[平多成功] ordId={data['data'][0].get('ordId')}")
    #         return True
    #     return False

    # def open_short(
    #     self,
    #     instId:  str,
    #     usdt_margin: float,
    #     price:   float,
    #     ct_val:  float,
    #     lever:   int,
    #     td_mode: str = "cross",
    # ) -> Optional[int]:
    #     """开空：sell + posSide=short，返回张数"""
    #     sz = self._contracts_from_usdt(usdt_margin, price, ct_val, lever)
    #     logger.info(f"[开空] 保证金={usdt_margin:.2f}U price={price:.2f} → {sz}张")
    #     body = {
    #         "instId":  instId,
    #         "tdMode":  td_mode,
    #         "side":    "sell",
    #         "posSide": "short",
    #         "ordType": "market",
    #         "sz":      str(sz),
    #     }
    #     data = self._post("/api/v5/trade/order", body)
    #     if data and data["data"]:
    #         logger.info(f"[开空成功] ordId={data['data'][0].get('ordId')} sz={sz}张")
    #         return sz
    #     return None

    # def close_short(
    #     self,
    #     instId:  str,
    #     sz:      int,
    #     td_mode: str = "cross",
    # ) -> bool:
    #     """平空：buy + posSide=short"""
    #     sz = max(1, int(sz))
    #     logger.info(f"[平空] sz={sz}张")
    #     body = {
    #         "instId":  instId,
    #         "tdMode":  td_mode,
    #         "side":    "buy",
    #         "posSide": "short",
    #         "ordType": "market",
    #         "sz":      str(sz),
    #     }
    #     data = self._post("/api/v5/trade/order", body)
    #     if data and data["data"]:
    #         logger.info(f"[平空成功] ordId={data['data'][0].get('ordId')}")
    #         return True
    #     return False



    def open_long(
        self,
        instId:  str,
        usdt_margin: float,
        price:   float,
        ct_val:  float,
        lever:   int,
        td_mode: str = "cross",
        offset:  float = 0.001,   # 👈 新增：默认低0.1%挂单
    ) -> Optional[int]:

        sz = self._contracts_from_usdt(usdt_margin, price, ct_val, lever)

        # 👇 关键：限价低于当前价格
        limit_price = price * (1 - offset)

        logger.info(f"[开多-限价] 保证金={usdt_margin:.2f}U 当前价={price:.2f} 限价={limit_price:.2f} → {sz}张")

        body = {
            "instId":  instId,
            "tdMode":  td_mode,
            "side":    "buy",
            "posSide": "long",
            "ordType": "limit",              # ✅ 改这里
            "px":      str(round(limit_price, 4)),  # ✅ 限价
            "sz":      str(sz),
        }

        data = self._post("/api/v5/trade/order", body)
        if data and data["data"]:
            ordId = data["data"][0].get("ordId")
            logger.info(f"[开多挂单成功] ordId={ordId} sz={sz}张")
            return sz

        return None

    def close_long(
        self,
        instId:  str,
        sz:      int,
        td_mode: str = "cross",
    ) -> bool:

        sz = max(0.01, round(sz, 2))
        logger.info(f"[平多-市价] sz={sz}张")

        body = {
            "instId":  instId,
            "tdMode":  td_mode,
            "side":    "sell",
            "posSide": "long",
            "ordType": "market",   # ✅ 不建议改
            "sz":      str(sz),
        }

        data = self._post("/api/v5/trade/order", body)
        if data and data["data"]:
            logger.info(f"[平多成功] ordId={data['data'][0].get('ordId')}")
            return True

        return False


    def open_short(
        self,
        instId:  str,
        usdt_margin: float,
        price:   float,
        ct_val:  float,
        lever:   int,
        td_mode: str = "cross",
        offset:  float = 0.001,   # 👈 新增
    ) -> Optional[int]:

        sz = self._contracts_from_usdt(usdt_margin, price, ct_val, lever)

        # 👇 空头挂高一点
        limit_price = price * (1 + offset)

        logger.info(f"[开空-限价] 保证金={usdt_margin:.2f}U 当前价={price:.2f} 限价={limit_price:.2f} → {sz}张")

        body = {
            "instId":  instId,
            "tdMode":  td_mode,
            "side":    "sell",
            "posSide": "short",
            "ordType": "limit",              # ✅ 改这里
            "px":      str(round(limit_price, 4)),
            "sz":      str(sz),
        }

        data = self._post("/api/v5/trade/order", body)
        if data and data["data"]:
            ordId = data["data"][0].get("ordId")
            logger.info(f"[开空挂单成功] ordId={ordId} sz={sz}张")
            return sz

        return None

    def close_short(
        self,
        instId:  str,
        sz:      int,
        td_mode: str = "cross",
    ) -> bool:

        sz = max(0.01, round(sz, 2))
        logger.info(f"[平空-市价] sz={sz}张")

        body = {
            "instId":  instId,
            "tdMode":  td_mode,
            "side":    "buy",
            "posSide": "short",
            "ordType": "market",
            "sz":      str(sz),
        }

        data = self._post("/api/v5/trade/order", body)
        if data and data["data"]:
            logger.info(f"[平空成功] ordId={data['data'][0].get('ordId')}")
            return True

        return False

    

    def get_last_fill_price(self, instId: str) -> Optional[float]:
        """获取最近一笔成交价"""
        data = self._get("/api/v5/trade/fills", {"instId": instId, "limit": "1"})
        if data and data["data"]:
            return float(data["data"][0]["fillPx"])
        return None

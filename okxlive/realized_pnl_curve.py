"""Historical realized BTC PnL from the same OKX bills as Total PnL."""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal


logger = logging.getLogger(__name__)
WINDOW_SECONDS = 89 * 86400  # Inside OKX's rolling three-month archive.


class RealizedPnLCurve:
    def __init__(self, cache_path, instrument, currency, refresh_seconds=60):
        self.cache_path = cache_path
        self.instrument = instrument
        self.currency = currency
        self.refresh_seconds = refresh_seconds
        self.lock = threading.Lock()
        self.next_refresh = 0.0
        self.refreshing = False
        self.last_error = None
        self.coverage_start_ms = None
        self.coverage_end_ms = None
        self.bills = {}
        try:
            with open(cache_path, encoding="utf-8") as file:
                cache = json.load(file)
            if cache.get("instrument") == instrument and cache.get("currency") == currency:
                self.coverage_start_ms = cache.get("coverage_start_ms")
                self.coverage_end_ms = cache.get("coverage_end_ms")
                self.bills = cache.get("bills", {})
        except (FileNotFoundError, OSError, ValueError) as exc:
            if not isinstance(exc, FileNotFoundError):
                logger.warning("已实现收益曲线缓存读取失败: %s", exc)

    def refresh_async(self, client):
        with self.lock:
            if self.refreshing or time.monotonic() < self.next_refresh:
                return
            self.refreshing = True
            self.next_refresh = time.monotonic() + self.refresh_seconds
        threading.Thread(target=self._worker, args=(client,), daemon=True).start()

    def _worker(self, client):
        try:
            self.refresh(client)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("OKX 已实现收益账单查询失败，沿用已有曲线: %s", exc)
            with self.lock:
                self.last_error = str(exc)
        finally:
            with self.lock:
                self.refreshing = False

    def refresh(self, client, now_ms=None):
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        begin_ms = now_ms - WINDOW_SECONDS * 1000
        # OKX returns rows nearest `begin` when it is supplied alone. Supplying
        # both bounds starts at the newest rows, so `after` can page backward.
        params = {"instId": self.instrument, "ccy": self.currency,
                  "begin": str(begin_ms), "end": str(now_ms), "limit": "100"}
        fetched = {}
        for _ in range(100):
            response = client._get("/api/v5/account/bills-archive", params)
            if response is None:
                raise ValueError("OKX 未返回交易账单")
            rows = response.get("data", [])
            if not isinstance(rows, list):
                raise ValueError("OKX 交易账单格式错误")
            for row in rows:
                if row.get("instId") != self.instrument or row.get("ccy") != self.currency:
                    continue
                bill_id = str(row["billId"])
                ts = int(row["ts"])
                change = Decimal(row["balChg"])
                if not change.is_finite():
                    raise ValueError("账单金额不是有限数")
                fetched[bill_id] = {"ts": ts, "amount": str(change)}
            if len(rows) < 100:
                break
            params["after"] = rows[-1]["billId"]
            time.sleep(0.45)
        else:
            raise ValueError("交易账单超过 100 页")

        with self.lock:
            contiguous = self.coverage_end_ms is not None and begin_ms <= self.coverage_end_ms
            bills = {**self.bills, **fetched} if contiguous else fetched
            start = min(self.coverage_start_ms, begin_ms) if contiguous else begin_ms
            cache = {"instrument": self.instrument, "currency": self.currency,
                     "coverage_start_ms": start, "coverage_end_ms": now_ms, "bills": bills}
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            temp_path = self.cache_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(cache, file, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.cache_path)
            self.bills = bills
            self.coverage_start_ms = start
            self.coverage_end_ms = now_ms
            self.last_error = None

    def series(self, summary):
        """Reverse known bill changes from Total PnL, then replay in time order."""
        with self.lock:
            start = self.coverage_start_ms
            end = self.coverage_end_ms
            bills = dict(self.bills)
            refreshing = self.refreshing
            last_error = self.last_error
        empty = {"ready": False, "history": [], "coverage_start": None,
                 "as_of_time": None, "live_pnl": None,
                 "status": "loading" if refreshing else ("query_failed" if last_error else "waiting"),
                 "error": last_error}
        if start is None or end is None or summary is None:
            return empty
        as_of_time = summary["as_of_time"]
        as_of_ms = int(datetime.fromisoformat(as_of_time).timestamp() * 1000)
        latest_id = int(summary["last_bill_id"])
        baseline_id = int(summary["baseline_bill_id"])
        # The two independent readers must agree on the latest booked bill.
        if latest_id > baseline_id and str(latest_id) not in bills:
            return {**empty, "status": "query_failed" if last_error else "syncing_latest_bill"}
        events = sorted((int(row["ts"]), int(bill_id), Decimal(row["amount"]))
                        for bill_id, row in bills.items()
                        if start < int(row["ts"]) <= as_of_ms and int(bill_id) <= latest_id)
        booked = Decimal(str(summary["booked_usdt"]))
        value = booked - sum((amount for _, _, amount in events), Decimal("0"))
        history = [{"time": datetime.fromtimestamp(start / 1000, timezone.utc).isoformat(),
                    "pnl": float(value)}]
        for ts, _, amount in events:
            value += amount
            history.append({"time": datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(),
                            "pnl": float(value)})
        live = float(Decimal(str(summary["net_usdt"])))
        live_time = (datetime.now(timezone.utc).isoformat()
                     if summary["provisional_usdt"] else as_of_time)
        return {"ready": True, "history": history,
                "coverage_start": history[0]["time"], "as_of_time": as_of_time,
                "live_pnl": live, "live_time": live_time, "status": "ready", "error": None}

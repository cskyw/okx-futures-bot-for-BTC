"""Read-only OKX bill reconciliation for the BTC dashboard."""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal


logger = logging.getLogger(__name__)
UTC8 = timezone(timedelta(hours=8))


class BTCPnLLedger:
    def __init__(self, baseline_path: str, cache_path: str, refresh_seconds: int = 60):
        with open(baseline_path, encoding="utf-8") as f:
            self.baseline = json.load(f)
        self.cache_path = cache_path
        self.refresh_seconds = refresh_seconds
        self.lock = threading.Lock()
        self.next_refresh = 0.0
        self.snapshot = {
            "baseline_bill_id": self.baseline["last_bill_id"],
            "last_bill_id": self.baseline["last_bill_id"],
            "as_of_time": self.baseline["as_of_time"],
            "net_usdt": self.baseline["net_usdt"],
        }
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("baseline_bill_id") == self.baseline["last_bill_id"]:
                    self.snapshot = cached
            except (OSError, ValueError) as exc:
                logger.error("BTC 账单缓存读取失败，将从基准重新核对: %s", exc)

    def _refresh(self, client) -> bool:
        known_id = int(self.snapshot["last_bill_id"])
        begin_ms = int(datetime.fromisoformat(self.baseline["as_of_time"]).timestamp() * 1000)
        params = {
            "instId": self.baseline["instrument"],
            "ccy": self.baseline["currency"],
            "begin": str(begin_ms),
            "end": str(int(time.time() * 1000)),
            "limit": "100",
        }
        new_bills = []
        for _ in range(100):
            response = client._get("/api/v5/account/bills-archive", params)
            if response is None:
                return False
            rows = response.get("data", [])
            reached_known = False
            for row in rows:
                if int(row["billId"]) <= known_id:
                    reached_known = True
                    break
                if row.get("instId") == self.baseline["instrument"] and row.get("ccy") == self.baseline["currency"]:
                    new_bills.append(row)
            if reached_known or len(rows) < 100:
                break
            params["after"] = rows[-1]["billId"]
        else:
            logger.error("BTC 账单翻页超过 100 页，保留上次核对结果")
            return False

        if not new_bills:
            return True

        total = Decimal(self.snapshot["net_usdt"])
        total += sum(Decimal(row["balChg"]) for row in new_bills)
        newest = max(new_bills, key=lambda row: int(row["billId"]))
        next_snapshot = {
            "baseline_bill_id": self.baseline["last_bill_id"],
            "last_bill_id": newest["billId"],
            "as_of_time": datetime.fromtimestamp(int(newest["ts"]) / 1000, UTC8).isoformat(),
            "net_usdt": str(total),
        }
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        temp_path = self.cache_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(next_snapshot, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.cache_path)
        self.snapshot = next_snapshot
        return True

    def get_total(self, client, local_history: list, ct_val: float) -> dict:
        with self.lock:
            if time.monotonic() >= self.next_refresh:
                try:
                    refreshed = self._refresh(client)
                except (OSError, ValueError, KeyError) as exc:
                    logger.error("BTC 账单核对失败，保留上次结果: %s", exc)
                    refreshed = False
                self.next_refresh = time.monotonic() + (self.refresh_seconds if refreshed else 60)

            # Only local records newer than the newest booked bill are provisional.
            # A short grace period avoids counting a just-booked fill twice.
            cutoff = datetime.fromisoformat(self.snapshot["as_of_time"]) + timedelta(minutes=2)
            provisional = Decimal("0")
            for record in local_history:
                try:
                    if datetime.fromisoformat(record["time"]) <= cutoff:
                        continue
                    sign = 1 if record["direction"] == "long" else -1
                    provisional += (
                        Decimal(str(record["exit_price"])) - Decimal(str(record["entry_price"]))
                    ) * Decimal(str(record["sz"])) * Decimal(str(ct_val)) * sign
                except (KeyError, ValueError):
                    continue

            booked = Decimal(self.snapshot["net_usdt"])
            return {
                "net_usdt": float(booked + provisional),
                "booked_usdt": float(booked),
                "provisional_usdt": float(provisional),
                "from_time": self.baseline["from_time"],
                "as_of_time": self.snapshot["as_of_time"],
                "baseline_bill_id": self.baseline["last_bill_id"],
                "last_bill_id": self.snapshot["last_bill_id"],
            }

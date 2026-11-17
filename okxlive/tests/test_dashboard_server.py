import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import api_server


class DashboardServerTests(unittest.TestCase):
    def test_static_page_and_health_respond_during_slow_status(self):
        blocked = threading.Event()
        release = threading.Event()
        errors = []

        def slow_balance(**_kwargs):
            blocked.set()
            release.wait(timeout=5)
            return {"totalEq": 100, "availBal": 90}

        with patch.object(api_server.client, "get_account_balance", side_effect=slow_balance), \
             patch.object(api_server.client, "get_positions", return_value={}), \
             patch.object(api_server.client, "get_ticker", return_value=65000), \
             patch.object(api_server, "pnl_ledger", None):
            with api_server.create_dashboard_server("127.0.0.1", 0) as server:
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                base = f"http://127.0.0.1:{server.server_port}"

                def request_status():
                    try:
                        with urlopen(base + "/api/status", timeout=8) as response:
                            self.assertEqual(response.status, 200)
                    except Exception as exc:
                        errors.append(exc)

                status_thread = threading.Thread(target=request_status, daemon=True)
                try:
                    status_thread.start()
                    self.assertTrue(blocked.wait(timeout=2), "status request did not begin")
                    with urlopen(base + "/api/health", timeout=2) as response:
                        self.assertEqual(response.status, 200)
                    with urlopen(base + "/dashboard/index.html", timeout=2) as response:
                        self.assertEqual(response.status, 200)
                finally:
                    release.set()
                    status_thread.join(timeout=8)
                    server.shutdown()
                    server_thread.join(timeout=2)

                self.assertFalse(status_thread.is_alive())
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

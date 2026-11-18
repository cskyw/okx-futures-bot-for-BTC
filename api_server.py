import os
import logging
import threading
from functools import wraps
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from flask import Flask, jsonify, request, redirect, send_from_directory
from flask_cors import CORS
from okx_client import OKXClient
from config import CONFIG
from dashboard_state import DashboardStateReader
from pnl_ledger import BTCPnLLedger
from realized_pnl_curve import RealizedPnLCurve
from datetime import datetime, timezone, timedelta

# Use absolute paths so it works reliably on a server regardless of the current working directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")

app = Flask(__name__, static_folder=DASHBOARD_DIR)
CORS(app)  # Enable CORS
_status_slots = threading.BoundedSemaphore(2)


def limit_status_requests(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not _status_slots.acquire(blocking=False):
            return jsonify({"success": False, "error": "看板状态请求繁忙，请稍后重试"}), 503
        try:
            return handler(*args, **kwargs)
        finally:
            _status_slots.release()
    return wrapped


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16
    max_workers = 3

    def __init__(self, *args, **kwargs):
        self._worker_slots = threading.BoundedSemaphore(self.max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


def create_dashboard_server(host="0.0.0.0", port=5000):
    return make_server(host, port, app,
                       server_class=ThreadedWSGIServer,
                       handler_class=QuietRequestHandler)

# Initialize OKX Client
client = OKXClient(
    api_key=CONFIG["api_key"],
    secret_key=CONFIG["secret_key"],
    passphrase=CONFIG["passphrase"],
    simulated=CONFIG.get("simulated", False)
)

state = DashboardStateReader(state_path=CONFIG["state_file"])
realized_curve = RealizedPnLCurve(
    os.path.join(os.path.dirname(CONFIG["state_file"]), "realized_pnl_curve.json"),
    CONFIG["inst_id"], CONFIG["quote_ccy"])
_pnl_baseline_path = os.path.join(BASE_DIR, "data", "btc_pnl_baseline.json")
pnl_ledger = (
    BTCPnLLedger(
        baseline_path=_pnl_baseline_path,
        cache_path=os.path.join(os.path.dirname(CONFIG["state_file"]), "btc_pnl_ledger.json"),
    )
    if os.path.exists(_pnl_baseline_path) else None
)

@app.route('/')
def root():
    return redirect('/dashboard/index.html')


@app.route('/api/health', methods=['GET'])
def health():
    """Local server health independent of OKX availability."""
    return jsonify({"success": True})

@app.route('/dashboard/<path:filename>')
def serve_dashboard(filename):
    return send_from_directory(DASHBOARD_DIR, filename)

@app.route('/api/status', methods=['GET'])
@limit_status_requests
def get_status():
    """
    Fetch real-time data from OKX API and merge with local strategy metadata
    """
    try:
        # Get real account balance
        account_info = client.get_account_balance(ccy=CONFIG["quote_ccy"])
        
        # Get real positions
        positions = client.get_positions(CONFIG["inst_id"])
        
        # Get ticker price for calculating values
        current_price = client.get_ticker(CONFIG["inst_id"])

        # Reload local state to get the latest completed trades count
        state.reload()
        
        # Get start and end dates from query args
        start_date_str = request.args.get('start')
        end_date_str = request.args.get('end')

        # Calculate statistics from trade_history
        trade_history = state.get_trade_history()
        all_trade_history = trade_history
        
        sd, ed = None, None
        if start_date_str or end_date_str:
            filtered = []
            try:
                if start_date_str:
                    sd = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=8)))
                if end_date_str:
                    ed = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone(timedelta(hours=8)))
            except ValueError:
                pass
                
            for t in trade_history:
                try:
                    trade_time = datetime.fromisoformat(t["time"])
                    if sd and trade_time < sd: continue
                    if ed and trade_time > ed: continue
                    filtered.append(t)
                except Exception:
                    filtered.append(t)
            trade_history = filtered

        total_trades = len(trade_history)
        winning_trades = sum(1 for t in trade_history if t.get("lev_pnl_pct", 0) > 0)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        # Calculate cumulative and average PnL (unleveraged)
        cumulative_pnl_pct = sum(t.get("pnl_pct", 0) for t in trade_history)
        avg_pnl_pct = (cumulative_pnl_pct / total_trades) if total_trades > 0 else 0

        cumulative_pnl_usdt = 0.0
        ct_val = CONFIG.get("ct_val", 0.01)
        for t in trade_history:
            sz = t.get("sz", 0)
            ep = t.get("entry_price", 0)
            xp = t.get("exit_price", 0)
            direction = t.get("direction", "long")
            if direction == "long":
                cumulative_pnl_usdt += (xp - ep) * sz * ct_val
            elif direction == "short":
                cumulative_pnl_usdt += (ep - xp) * sz * ct_val

        pnl_from_time = trade_history[-1].get("time") if trade_history else None
        pnl_basis = "local_history"
        pnl_as_of_time = None
        pnl_provisional_usdt = 0.0
        all_time_pnl = None
        all_time_from = None
        summary = None
        if pnl_ledger is not None:
            summary = pnl_ledger.get_total(client, all_trade_history, ct_val)
            all_time_pnl = summary["net_usdt"]
            all_time_from = summary["from_time"]
            if not start_date_str and not end_date_str:
                cumulative_pnl_usdt = all_time_pnl
                pnl_from_time = summary["from_time"]
                pnl_as_of_time = summary["as_of_time"]
                pnl_provisional_usdt = summary["provisional_usdt"]
                pnl_basis = "okx_bills_with_local_gap" if pnl_provisional_usdt else "okx_bills"
        elif not start_date_str and not end_date_str:
            cumulative_pnl_usdt = None
            pnl_basis = "unavailable"

        # 与旧看板相同：由当前权益减去累计收益倒推本金，再按运行天数线性年化。
        # 充值、转账及浮动盈亏会影响该估算，因此不能当作精确的资金加权收益率。
        total_return_pct = None
        annualized_return = None
        if all_time_pnl is not None and all_time_from and account_info:
            try:
                equity = float(account_info["totalEq"])
                inferred_initial = equity - all_time_pnl
                elapsed_days = (datetime.now(timezone.utc) - datetime.fromisoformat(all_time_from)).total_seconds() / 86400
                if equity > 0 and inferred_initial > 0 and elapsed_days > 0:
                    total_return_pct = all_time_pnl / inferred_initial * 100
                    annualized_return = total_return_pct / elapsed_days * 365
            except (KeyError, ValueError, TypeError, OverflowError):
                pass

        completed_long_trades = sum(1 for t in trade_history if t.get("direction") == "long")
        completed_short_trades = sum(1 for t in trade_history if t.get("direction") == "short")

        if summary is not None:
            realized_curve.refresh_async(client)
        realized_pnl = realized_curve.series(summary)
        max_points = 2000
        pnl_history = realized_pnl["history"]
        if len(pnl_history) > max_points:
            step = len(pnl_history) / max_points
            reduced = [pnl_history[int(i * step)] for i in range(max_points)]
            if reduced[-1] != pnl_history[-1]:
                reduced.append(pnl_history[-1])
            realized_pnl["history"] = reduced

        return jsonify({
            "success": True,
            "data": {
                "account": account_info,
                "positions": positions,
                "current_price": current_price,
                "metadata": {
                    "completed_long_trades": completed_long_trades,
                    "completed_short_trades": completed_short_trades,
                    "simulated": CONFIG.get("simulated", False),
                    "lever": CONFIG.get("lever", 5),
                    "win_rate": win_rate,
                    "avg_pnl_pct": avg_pnl_pct,
                    "total_trades_history": total_trades,
                    "cumulative_pnl_pct": total_return_pct if pnl_basis != "local_history" else None,
                    "cumulative_pnl_usdt": cumulative_pnl_usdt,
                    "pnl_from_time": pnl_from_time,
                    "pnl_as_of_time": pnl_as_of_time,
                    "pnl_provisional_usdt": pnl_provisional_usdt,
                    "pnl_basis": pnl_basis,
                    "annualized_return": annualized_return
                },
                "trade_history": trade_history[:50], # Send last 50 for UI
                "realized_pnl_curve": realized_pnl
            }
        })
    except Exception as e:
        app.logger.exception("获取看板状态失败")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """
    Return the last N lines of the log file
    """
    lines = max(1, min(request.args.get('lines', default=50, type=int), 200))
    log_path = os.path.join(BASE_DIR, CONFIG['log_dir'], "trader.log")
    
    if not os.path.exists(log_path):
        return jsonify({"success": True, "logs": ["No log file found."]})
    
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            position = f.tell()
            tail = b""
            while position > 0 and tail.count(b"\n") <= lines:
                chunk_size = min(8192, position)
                position -= chunk_size
                f.seek(position)
                tail = f.read(chunk_size) + tail
            logs = [line.decode("utf-8", errors="replace") + "\n" for line in tail.splitlines()[-lines:]]
            return jsonify({"success": True, "logs": logs})
    except Exception as e:
        app.logger.exception("读取交易日志失败")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    with create_dashboard_server() as server:
        print("看板服务已启动，监听端口 5000", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

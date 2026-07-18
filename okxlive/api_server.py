import os
from flask import Flask, jsonify, request, redirect, send_from_directory
from flask_cors import CORS
from okx_client import OKXClient
from config import CONFIG
from state_manager import StateManager

# Use absolute paths so it works reliably on a server regardless of the current working directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")

app = Flask(__name__, static_folder=DASHBOARD_DIR)
CORS(app)  # Enable CORS

# Initialize OKX Client
client = OKXClient(
    api_key=CONFIG["api_key"],
    secret_key=CONFIG["secret_key"],
    passphrase=CONFIG["passphrase"],
    simulated=CONFIG.get("simulated", False)
)

state = StateManager(path=CONFIG["state_file"])

@app.route('/')
def root():
    return redirect('/dashboard/index.html')

@app.route('/dashboard/<path:filename>')
def serve_dashboard(filename):
    return send_from_directory(DASHBOARD_DIR, filename)

@app.route('/api/status', methods=['GET'])
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

        from datetime import datetime, timezone
        
        # Get start and end dates from query args
        start_date_str = request.args.get('start')
        end_date_str = request.args.get('end')

        # Calculate statistics from trade_history
        trade_history = state.get_trade_history()
        
        sd, ed = None, None
        if start_date_str or end_date_str:
            filtered = []
            try:
                if start_date_str:
                    sd = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if end_date_str:
                    ed = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
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

        completed_long_trades = sum(1 for t in trade_history if t.get("direction") == "long")
        completed_short_trades = sum(1 for t in trade_history if t.get("direction") == "short")

        # Calculate annualized return
        annualized_return = 0
        try:
            now_time = datetime.now(timezone.utc)
            actual_sd = sd
            
            if not actual_sd:
                start_time_str = state.get("dashboard_start_time")
                if start_time_str:
                    actual_sd = datetime.fromisoformat(start_time_str)

            if actual_sd:
                actual_ed = min(ed, now_time) if ed else now_time
                days_elapsed = (actual_ed - actual_sd).total_seconds() / 86400.0
                days_elapsed = max(days_elapsed, 1.0 / 24.0) # Assume at least 1 hour has passed
                annualized_return = (cumulative_pnl_pct / days_elapsed) * 365
        except Exception:
            pass

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
                    "cumulative_pnl_pct": cumulative_pnl_pct,
                    "annualized_return": annualized_return
                },
                "trade_history": trade_history[:50] # Send last 50 for UI
            }
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    """
    Return the last N lines of the log file
    """
    lines = request.args.get('lines', default=50, type=int)
    log_path = os.path.join(BASE_DIR, CONFIG['log_dir'], "trader.log")
    
    if not os.path.exists(log_path):
        return jsonify({"success": True, "logs": ["No log file found."]})
    
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            return jsonify({"success": True, "logs": all_lines[-lines:]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

if __name__ == "__main__":
    from waitress import serve
    print("=====================================================")
    print("🚀 QuantLive Dashboard Server Starting (Production Mode)...")
    print(f"📂 Dashboard Path: {DASHBOARD_DIR}")
    print("🌐 Access the dashboard at: http://你的公网IP:5000/")
    print("🛑 Press Ctrl+C to stop the server cleanly.")
    print("=====================================================\n")
    # Waitress is a production-grade WSGI server for Windows
    serve(app, host="0.0.0.0", port=5000)

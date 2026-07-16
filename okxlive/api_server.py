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
        state._data = state._load()

        return jsonify({
            "success": True,
            "data": {
                "account": account_info,
                "positions": positions,
                "current_price": current_price,
                "metadata": {
                    "completed_long_trades": state.get("completed_long_trades", 0),
                    "completed_short_trades": state.get("completed_short_trades", 0),
                    "simulated": CONFIG.get("simulated", False),
                    "lever": CONFIG.get("lever", 5)
                }
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

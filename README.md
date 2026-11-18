# OKX BTC Futures Trading Bot

An automated OKX BTC-USDT perpetual-swap bot based on moving-average signals. It includes a web dashboard and supports demo and live trading.

## Project structure

```text
okx-futures-bot-for-BTC/
├── live_trader.py       # Trading engine
├── okx_client.py        # OKX API client
├── strategy_engine.py   # Moving-average signals
├── state_manager.py     # Position and trade-history persistence
├── api_server.py        # Dashboard API server
├── dashboard/           # Web dashboard
├── config.py            # API and strategy settings
├── requirements.txt
├── logs/                # Runtime logs
└── state/               # Runtime data
    ├── trader_state.json   # Current positions
    ├── trade_history.json  # Closed trades
    └── equity_history.json # Raw equity snapshots
```

## Features

**Trading**

- Checks the market every 10 seconds and prints a detailed hourly status report.
- Places a separate TP1 order after an entry fills and tracks it by entry order ID.
- Cancels a remaining TP1 order if the initial stop closes the position.
- Shows underlying price changes in the terminal while applying trailing-stop rules to leveraged returns.

**Dashboard**

- Shows win rate, total PnL, and an estimated annualized return.
- Lists closed trades with exit reasons and supports date filtering.
- Stores positions, trade history, and raw equity snapshots separately. The realized PnL chart is built from OKX bills.

## Setup

### 1. Prepare your OKX account

1. Transfer funds to the account used for perpetual-swap trading.
2. Enable long/short (hedge) position mode in OKX trading settings.
3. Create an API key with read and trade permissions. Withdrawal permission is unnecessary.
4. If your server has a fixed IP address, consider restricting the API key to that IP.

### 2. Install dependencies

```bash
sudo apt update && sudo apt install -y python3 python3-pip
cd okx-futures-bot-for-BTC
pip3 install -r requirements.txt
```

### 3. Configure credentials and strategy

Create `.env` in the repository root with your OKX credentials:

```dotenv
OKX_API_KEY=your-api-key
OKX_SECRET_KEY=your-secret-key
OKX_PASSPHRASE=your-passphrase
```

`config.py` loads these variables. Review its strategy settings before running the bot. Its current default is `"simulated": False`, which enables **live trading**. Set it to `True` for a demo run, then switch it back only when you intend to place real orders. Adjust leverage, position size, take-profit, and stop-loss settings for your account.

### 4. Test in demo mode

With `"simulated": True` in `config.py`, run:

```bash
python3 live_trader.py
```

Check the logs for successful candle retrieval, leverage setup, signal and price output, and order handling.

### 5. Run as a service

After testing, set `"simulated": False` if you intend to trade live. For example, create `/etc/systemd/system/okx-trader.service`:

```ini
[Unit]
Description=OKX Live Trader
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/okx-futures-bot-for-BTC
ExecStart=/usr/bin/python3 /home/ubuntu/okx-futures-bot-for-BTC/live_trader.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

Adjust the user, paths, and Python executable for your server, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now okx-trader
sudo systemctl status okx-trader
tail -f logs/trader.log
```

## Strategy

### Signals

The strategy uses hourly candles and three moving averages: MA5 (fast), MA10 (slow), and MA120 (trend). A long signal requires price to cross above MA5 and MA10 while staying above MA120. A short signal requires the opposite conditions.

### Entry orders and size

Entries use limit orders. For a long, the limit price is the current price multiplied by `(1 - limit_offset)`; for a short, it is multiplied by `(1 + limit_offset)`. The default `limit_offset` is `0.0002` (0.02%). A larger offset can make fills less likely; a smaller one can make an order more likely to execute as a taker.

`fixed_open_sz` controls contracts per entry and defaults to `0.02`. For BTC-USDT-SWAP, one contract represents `0.01 BTC`, so `0.02` contracts represent `0.0002 BTC`. Review OKX's current order-size rules before placing an order.

### Exits

1. **Initial stop-loss:** `sl_pct` defaults to `0.13`, or a 13% adverse price move. This is a last-resort stop, not a 13% loss on margin.
2. **TP1:** After the entry fills, the bot places a separate limit take-profit order for half the position at a 3% favorable price move (`tp1_pct`). Once it executes, the local stop for the remainder moves to the entry price.
3. **TP2 trailing stop:** It activates at a 20% leveraged return (`tp2_active_pct`) and closes the remainder at market after a 5% pullback from the peak return (`tp2_trail_pct`).

With 5× leverage, a 1% price move corresponds to approximately a 5% change in margin PnL before fees and funding. The 13% stop could therefore mean approximately a 65% loss of the margin allocated to that position. Liquidation and execution behavior can differ from this estimate.

## Web dashboard

The dashboard in `dashboard/` reads trading state through `api_server.py`. Its threaded server handles up to three concurrent requests, including up to two state requests. It reparses state files only when they change and keeps the last valid JSON snapshot if a file is temporarily incomplete during a write.

Start it after the trader is running:

```bash
python3 api_server.py
```

Open `http://your-server-ip:5000`. Use `http://your-server-ip:5000/api/health` to check whether the service responds; that endpoint does not contact OKX.

| Module | Details |
| --- | --- |
| Account overview | Total equity and available margin. |
| Open positions | Long and short positions, average entry prices, unrealized PnL, and leverage from OKX. |
| Realized PnL chart | Cumulative BTC-USDT-SWAP USDT-account bill PnL, including fees and funding but excluding unrealized PnL and transfers. Bills refresh every minute and are cached in `state/realized_pnl_curve.json`. Online bills cover roughly the latest three months; the chart offers ALL, 7D, 30D, and 90D views. Recent unbilled closes use a temporary local estimate at the end of the chart. |
| Summary metrics | Total PnL combines `data/btc_pnl_baseline.json`, local closed trades, and later OKX bills. The baseline compensates for older history previously limited to 200 trades. Annualized return estimates starting capital from current equity and cumulative PnL, then scales linearly by days run; deposits, transfers, and unrealized PnL can affect it. |
| Closed trades | All subsequent closes remain in `state/trade_history.json`; the table shows the latest 50, newest first, including direction, size, prices, leveraged return, and exit reason. |
| Date filter | Filters locally available closed trades. Older records removed by the previous 200-trade limit cannot be recovered individually. Filtered amounts do not deduct fees. |
| Log terminal | Shows the latest 70 lines of `trader.log`. |

The older `state/equity_history.json` snapshots remain on the running machine but are no longer used for the realized PnL chart or committed to the repository.

If port 5000 is blocked, update your firewall rules. To access the dashboard without exposing that port publicly, use an SSH tunnel:

```bash
ssh -L 5000:localhost:5000 user@your-server-ip
```

Then open `http://localhost:5000` locally.

## Troubleshooting

- **Leverage setup fails:** Check long/short position mode and the API key's trade permission.
- **Insufficient margin:** Reduce `fixed_open_sz` or `lever`, or add funds to the trading account.
- **State after a restart:** The bot restores local state from `state/trader_state.json`. Compare it with your actual OKX positions after restarting.

## Disclaimer

This project is for learning and research. Cryptocurrency derivatives trading carries substantial risk. Understand the strategy and its failure modes before using it with real funds. The author is not responsible for losses caused by its use.

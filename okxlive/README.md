# OKX 合约实盘交易程序

基于双均线交叉策略的 OKX 永续合约自动交易程序，配套 Web 数据看板，支持模拟盘/实盘切换。

## 目录结构

```
okx_live/
├── live_trader.py       # 主程序（交易引擎入口）
├── okx_client.py        # OKX API 封装
├── strategy_engine.py   # 均线量化信号引擎
├── state_manager.py     # 持仓与历史记录本地持久化
├── api_server.py        # Flask API 服务（供前端看板读取数据）
├── dashboard/           # Web 前端（Glassmorphism 风格数据看板）
├── config.py            # 配置文件（填入你的 API 信息和策略参数）
├── requirements.txt
├── logs/                # 运行日志（自动创建）
└── state/               # 数据存储（自动创建）
    ├── trader_state.json   # 实时持仓信息
    └── trade_history.json  # 历史交割记录（胜率/年化计算数据来源）
```

---

## 核心特性

**策略执行**
- 10 秒高频巡航 + 整点详情播报，兼顾追踪止损响应速度与终端可读性
- 解决了 OKX API 带止损的单子分批止盈必须用市价的限制，TP1 半仓止盈正常触发
- 终端输出现货涨跌幅，底层追踪止损按杠杆后真实盈亏执行

**数据看板**
- 实时展示胜率、总收益率、动态年化收益率
- 历史平仓订单流水，标注每笔交易的平仓原因（硬止盈/追踪止损/手动平仓等）
- 自定义日期过滤器，任意时间段的统计数据秒级响应
- 持仓状态文件与历史记录文件独立存储，重启服务不丢数据

---

## 部署步骤

### 1. OKX 账户前置设置

| 步骤 | 操作 |
|------|------|
| 1 | 划转资金到合约账户 |
| 2 | 持仓模式改为双向持仓（交易设置 → 持仓模式） |
| 3 | 创建 API Key，开启读取 + 交易权限，无需提币权限 |
| 4 | 若服务器 IP 固定，建议在 API 中绑定 IP 白名单 |

### 2. 安装依赖

```bash
sudo apt update && sudo apt install -y python3 python3-pip
cd okx_live
pip3 install -r requirements.txt
```

### 3. 填写配置

编辑 `config.py`，填入你的 OKX API 信息：

```python
"api_key":    "你的 API Key",
"secret_key": "你的 Secret Key",
"passphrase": "你的 Passphrase",
"simulated":  True,    # 先模拟盘测试，确认无误后改为 False
"lever":      5,       # 杠杆倍数
```

其余策略参数（止盈止损比例、开仓仓位等）也在 `config.py` 中，根据自己的风险偏好调整。

### 4. 模拟盘测试

```bash
python3 live_trader.py
```

观察日志确认以下项目正常：
- K 线拉取成功
- 杠杆设置成功
- 信号与价格正确打印
- 下单逻辑无报错

### 5. 切换实盘 + 后台运行

将 `config.py` 中 `"simulated"` 改为 `False`，然后创建 systemd 服务：

```bash
sudo nano /etc/systemd/system/okx-trader.service
```

```ini
[Unit]
Description=OKX Live Trader
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/okx_live
ExecStart=/usr/bin/python3 /home/ubuntu/okx_live/live_trader.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable okx-trader
sudo systemctl start okx-trader

# 查看状态
sudo systemctl status okx-trader

# 实时日志
tail -f logs/trader.log
```

---

## 交易策略说明

### 信号生成（基于 1 小时 K 线）

计算三条移动平均线：

- MA5（快线）：5 周期均线
- MA10（慢线）：10 周期均线
- MA120（趋势线）：120 周期牛熊分界

开仓条件（需同时满足）：
- 做多：价格向上突破 MA5 和 MA10，且价格高于 MA120
- 做空：价格向下突破 MA5 和 MA10，且价格低于 MA120

### 开仓下单方式

程序采用**限价单**开仓（非市价单），以减少滑点和 Taker 手续费。下单价格在当前市价基础上加一个微小偏移量：

- 做多时：限价 = 当前价 × (1 + `limit_offset`)
- 做空时：限价 = 当前价 × (1 - `limit_offset`)

`limit_offset` 默认 `0.0002`（即 0.02%），数值越小越省手续费，但挂单可能更难成交；数值过大则接近市价单，手续费更高。

### 开仓张数

程序采用**固定张数**开仓（全仓模式下不需要按保证金比例计算）。每次开仓的合约张数由 `config.py` 中的 `fixed_open_sz` 参数控制，默认 `0.02` 张（即 0.0002 BTC，OKX BTC-USDT-SWAP 最小下单单位为 0.01 张）。

根据自己的仓位大小调整这个数值即可。

### 阶梯式止盈止损

1. **初始止损（SL）**：开仓后立即挂 5%（`sl_pct`）止损单，价格反向 5% 直接平仓
2. **TP1 半仓止盈 + 保本**：顺向 3%（`tp1_pct`）触发半仓平仓，同时将剩余仓位止损线移至开仓均价
3. **TP2 动态追踪止损**：
   - 杠杆收益率达 20%（`tp2_active_pct`）时激活追踪止损
   - 从最高收益回撤 5%（`tp2_trail_pct`）时市价全平
   - 收益率直接到 27%（`tp2_hard_pct`）则不等回撤，直接平仓

---

## 风险说明

| 参数 | 值 | 说明 |
|------|----|------|
| 杠杆 | 5x | 价格波动 1% = 保证金盈亏 5% |
| sl_pct | 5% | 价格反向 5% 止损，约损失 25% 单笔保证金 |
| fixed_open_sz | 0.02 | 每次固定开仓 0.02 张，根据自己仓位调整 |
| limit_offset | 0.0002 | 限价单偏移量，0.02% |

---

## 常见问题

**Q: 提示"杠杆设置失败"？**
检查账户是否开启双向持仓模式，以及 API 权限是否包含"交易"。

**Q: 下单提示 Margin 不足？**
减小 `fixed_open_sz` 或 `lever`，或往合约账户划入更多资金。

**Q: 程序重启后仓位状态如何？**
持仓状态自动从 `state/trader_state.json` 恢复。建议重启后手动核对 OKX 实际持仓与 state 文件是否一致。

---

## 免责声明

本项目仅供学习与研究使用。加密货币合约交易风险极高，使用本程序造成的任何资金损失，作者概不负责。请在充分理解策略逻辑和风险后谨慎使用。

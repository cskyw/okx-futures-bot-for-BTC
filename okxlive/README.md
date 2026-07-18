# OKX 合约实盘交易程序 - 部署说明

## 目录结构

```
okx_live/
├── live_trader.py       # 主程序（入口与交易引擎）
├── okx_client.py        # OKX API 封装（已解决分批市价止盈限制）
├── strategy_engine.py   # 核心均线量化信号引擎
├── state_manager.py     # 持仓与历史交割记录本地持久化
├── api_server.py        # Flask API 服务器（供前端看板读取数据）
├── dashboard/           # Web 前端（包含拟物化 UI 和数据交互逻辑）
├── config.py            # 配置文件（填入 API Key）
├── requirements.txt
├── logs/                # 运行日志（自动创建）
└── state/               # 数据存储文件夹（自动创建）
    ├── trader_state.json   # 实时持仓信息
    └── trade_history.json  # 历史交割记录（用于胜率/年化计算）
```

---

## 🌟 核心特性升级 (v2.0)

**1. 策略执行层极致优化**
* **双轨架构巡航**：主程序采用 `静默高频 10 秒巡航` + `整点详情播报`，既保证了毫秒级的追踪止损反应，又保持了终端界面的清爽。
* **规避交易所限制**：完美解决 OKX API “带有止损的单子分批止盈必须使用市价” 的限制，TP1 半仓止盈丝滑触发。
* **精准 PnL 判定**：终端日志统一输出“标的现货涨跌幅”，而底层追踪止损逻辑严格按照“杠杆倍数后真实盈亏”执行，满足直观对比与精准止盈的双重需求。

**2. 量化数据面板 (Glassmorphism Dashboard)**
* **多维统计中心**：不仅显示实时持仓，更具备强大的后台记账功能，能够实时展示策略的 **胜率 (Win Rate)**、**总计无杠杆收益率 (Total PnL)** 以及 **动态年化收益率 (Annualized Return)**。
* **历史交割单追溯**：自带历史平仓订单流水表，清晰标注每一笔交易的平仓原因（如：硬止盈、追踪止损、手动平仓等）。
* **自定义时间切片**：强大的自定义日期过滤器。任意选择一个时间段（如“上个月”），Dashboard 会瞬间将该时间段内的胜率、收益率、交易笔数剥离出来单独展示，同时无缝兼容 5 秒实时轮询！
* **数据安全隔离**：历史记录账本 (`trade_history.json`) 与核心开仓仓位状态 (`trader_state.json`) 严格分离，随意重启服务器，数据永久安全。

---

## 1. OKX 账户前置设置（重要！）

在 OKX App / 网页端手动完成以下设置：

| 步骤 | 操作 |
|------|------|
| ① | 划转资金到**合约账户** |
| ② | 持仓模式改为**双向持仓**（交易设置 → 持仓模式） |
| ③ | 创建 API Key，权限：**读取 + 交易**，不需要提币权限 |
| ④ | 如果服务器 IP 固定，建议在 API 中绑定 IP 白名单 |

---

## 2. 服务器安装依赖

```bash
sudo apt update && sudo apt install -y python3 python3-pip
cd okx_live
pip3 install -r requirements.txt
```

---

## 3. 填写配置

编辑 `config.py`：

```python
"api_key":    "你的 API Key",
"secret_key": "你的 Secret Key",
"passphrase": "你的 Passphrase",
"simulated":  True,    # 先模拟盘，确认无误后改 False
"lever":      5,       # 杠杆倍数
```

---

## 4. 模拟盘测试

```bash
python3 live_trader.py
```

确认日志正常输出：
- ✅ K 线拉取成功
- ✅ 杠杆设置成功  
- ✅ 信号/价格正确打印
- ✅ 下单逻辑无报错

---

## 5. 切换实盘 + 后台运行

```python
# config.py 改为
"simulated": False,
```

创建 systemd 服务：

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

## 6. 📈 核心交易策略逻辑

本程序内置了一套基于**双均线交叉 + 长期趋势过滤**的量化顺势策略，配合极其严密的**阶梯式止盈止损机制**，旨在“截断亏损，让利润奔跑”。

### 6.1 信号生成 (基于 1 小时 K 线)
系统每小时拉取最新 K 线，计算移动平均线（MA）：
* **MA5 (快线)**: 5 周期移动平均线
* **MA10 (慢线)**: 10 周期移动平均线
* **MA120 (趋势线)**: 120 周期牛熊分界线

**开仓条件 (必须同时满足)**：
* **做多 (Long)**：当前价格同时**向上突破** MA5 和 MA10，并且当前价格**高于** MA120（确保处于大级别多头趋势）。
* **做空 (Short)**：当前价格同时**向下突破** MA5 和 MA10，并且当前价格**低于** MA120（确保处于大级别空头趋势）。

### 6.2 阶梯式止盈止损 (TP/SL) 流程
每一笔订单在开仓的同时，会直接在 OKX 交易所挂载高级条件单，并由本地程序配合进行动态追踪：

1. **初始硬止损 (SL)**
   * 开仓后立即挂载 `5%`（默认 `sl_pct`）的止损单。只要价格反向移动 5%，立即斩仓（在 5x 杠杆下约损失 25% 的单笔保证金）。
2. **TP1 半仓止盈 & 保本机制**
   * 当价格顺向移动达到 `3%`（默认 `tp1_pct`），触发**半仓市价平仓**，提前锁定一半利润。
   * **保本动作**：TP1 触发后，本地程序会立刻将剩余仓位的止损线**移动到开仓均价**。这意味着就算后续行情大反转，这笔交易最差也是不亏钱的。
3. **TP2 动态追踪止损 (Trailing Stop)**
   * **激活追踪**：当剩余仓位的**杠杆收益率**达到 `20%`（默认 `tp2_active_pct`）时，系统悄悄激活追踪止损。
   * **回撤平仓**：激活后，系统会记录曾经达到的最高收益率。一旦当前收益率从最高点**回撤 5%**（默认 `tp2_trail_pct`），立刻市价全平，吃尽波段鱼身。
   * **终极硬止盈**：如果行情暴走，杠杆收益率直接飙升到 `30%`（默认 `tp2_hard_pct`），不再等待回撤，直接市价落袋为安。

---

## 7. 杠杆与风险说明

| 参数 | 值 | 说明 |
|------|----|------|
| 杠杆 | 5x | 价格波动 1% → 保证金盈亏 5% |
| sl_pct | 5% | 价格反向 5% 触发止损 → 亏损约 25% 保证金 |
| buy_pct | 15% | 每次开仓使用账户权益的 15% 作为保证金 |
| 名义价值 | 15% × 5 = 75% | 每笔合约名义敞口约占账户 75% |

**合约张数计算公式：**
```
张数 = (保证金 × 杠杆) / (合约面值 × 价格)
     = (账户权益 × 15% × 5) / (0.01 × BTC价格)
```

示例：账户 1000 USDT，BTC = 95000 USDT
```
张数 = (1000 × 0.15 × 5) / (0.01 × 95000) = 750 / 950 ≈ 0 → 取整 1 张
```
> 注意：账户较小时可能只能下 1 张（最小单位），建议账户至少 500 USDT。

---

## 7. 常见问题

**Q: 提示 "杠杆设置失败"？**  
A: 检查账户是否已开启双向持仓模式，或 API 权限是否包含交易。

**Q: 下单提示 Margin 不足？**  
A: 减小 `buy_pct` 或 `lever`，或往合约账户划入更多资金。

**Q: 程序重启后仓位状态如何？**  
A: 状态自动从 `state/trader_state.json` 恢复，包含所有开仓 entry。  
   但建议重启后核对 OKX 实际持仓与 state 文件是否一致。

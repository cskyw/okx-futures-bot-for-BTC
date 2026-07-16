# OKX 合约实盘交易程序 - 部署说明

## 目录结构

```
okx_live/
├── live_trader.py       # 主程序（入口）
├── okx_client.py        # OKX API 封装（合约双向持仓）
├── strategy_engine.py   # 均线信号计算
├── state_manager.py     # 持仓状态持久化
├── config.py            # 配置文件（填入 API Key）
├── requirements.txt
├── logs/                # 运行日志（自动创建）
└── state/               # 持仓状态 JSON（自动创建）
```

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

## 6. 杠杆与风险说明

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

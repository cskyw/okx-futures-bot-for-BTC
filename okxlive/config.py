"""
config.py
实盘配置 - 修改此文件填入你的 API 信息和策略参数

交易模式：OKX 永续合约 (BTC-USDT-SWAP)，全仓模式，5 倍杠杆
合约规格：BTC-USDT-SWAP 每张 = 0.01 BTC
"""

CONFIG = {
    # ====== OKX API 配置 ======
    "api_key": "9347dc9b-74ed-467d-ac19-bfe40754ecbe",
    "secret_key": "B1F35B84C84C3A817C75D5B38B4565B2",
    "passphrase": "Geyi761212.",

    # True = 模拟盘（测试用），False = 实盘
    "simulated": False,

    # ====== 合约交易配置 ======
    "inst_id":     "BTC-USDT-SWAP",   # 永续合约
    "quote_ccy":   "USDT",
    "td_mode":     "cross",            # cross=全仓, isolated=逐仓
    "lever":       5,                  # 杠杆倍数
    "ct_val":      0.01,               # 合约面值：1张 = 0.01 BTC (OKX 标准)
    "pos_side":    "long_short",       # "long_short"=双向持仓, "net"=单向持仓
                                       # 双向持仓需在 OKX App 中开启「双向持仓」模式

    # ====== 策略参数（与回测保持一致）======
    "ma_fast":       5,
    "ma_slow":       10,
    "fixed_open_sz": 0.02,     # 每次固定开仓的合约张数（如果是固定开仓模式）
    "buy_pct":       0.15,     # 每次开仓使用账户权益的 15%（作为保证金）
    "tp1_pct":       0.03,     # TP1 止盈 3%（基于开仓价）
    "tp2_active_pct":0.20,     # TP2 追踪止损激活线 20%
    "tp2_hard_pct":  0.27,     # TP2 硬性止盈 27%
    "tp2_trail_pct": 0.05,     # TP2 追踪回撤 5%
    "sl_pct":        0.05,     # 止损 5%（5倍杠杆下实际亏损约25%保证金，请勿随意调大）
    "tp1_sell_prop": 0.5,      # TP1 平掉该仓位的 50% (半仓)

    # ====== 文件路径 ======
    "state_file": "state/trader_state.json",
    "log_dir":    "logs",
}

# ====== 杠杆与止损说明 ======
# 5 倍杠杆下，价格波动 1% = 保证金盈亏 5%
# sl_pct=0.05 表示价格反向 5% 触发止损，等效损失约 25% 保证金
# 建议根据自己的风险承受能力调整 sl_pct，不建议低于 0.03（太容易被扫）

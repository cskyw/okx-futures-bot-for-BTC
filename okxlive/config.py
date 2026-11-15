"""
config.py
实盘配置 - 修改此文件填入你的 API 信息和策略参数

交易模式：OKX 永续合约 (BTC-USDT-SWAP)，全仓模式，5 倍杠杆
合约规格：BTC-USDT-SWAP 每张 = 0.01 BTC
"""
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

CONFIG = {
    # ====== OKX API 配置 ======
    "api_key": os.getenv("OKX_API_KEY", ""),
    "secret_key": os.getenv("OKX_SECRET_KEY", ""),
    "passphrase": os.getenv("OKX_PASSPHRASE", ""),

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
    "tp1_pct":       0.03,     # TP1 止盈 3%（基于开仓价）
    "tp2_active_pct":0.20,     # TP2 追踪止损激活线 20%
    "tp2_trail_pct": 0.05,     # TP2 追踪回撤 5%
    "sl_pct":        0.13,     # 止损 13%（5倍杠杆下实际亏损约65%保证金，作为多空对冲策略的最后保险）
    "tp1_sell_prop": 0.5,      # TP1 平掉该仓位的 50% (半仓)
    "limit_offset":  0.0002,   # 挂单限价偏移量 (0.0002 = 0.02%)，数值越小越容易成交，但也越容易变成吃单(Taker)

    # ====== 文件路径 ======
    "state_file": os.path.join(BASE_DIR, "state", "trader_state.json"),
    "log_dir":    os.path.join(BASE_DIR, "logs"),
}

# ====== 杠杆与止损说明 ======
# 5 倍杠杆下，价格波动 1% = 保证金盈亏 5%
# sl_pct=0.13 表示价格反向 13% 触发止损，等效损失约 65% 保证金
# 策略为多空对冲+追踪止盈，SL 仅作最后防线（防黑天鹅），常规震荡（3~8%）不会触发
# 不建议低于 0.05（太容易被震荡扫掉），也不建议高于 0.15（爆仓风险上升）

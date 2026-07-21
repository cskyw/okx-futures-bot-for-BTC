"""
live_trader.py
OKX 永续合约实盘交易程序
- 双向持仓（long / short 独立管理）
- 5 倍杠杆
- 每小时整点执行一次
"""

import time
import logging
import os
import traceback
from datetime import datetime, timezone, timedelta

from okx_client import OKXClient
from strategy_engine import StrategyEngine
from state_manager import StateManager
from config import CONFIG

# ====== 日志配置 ======
os.makedirs(CONFIG["log_dir"], exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"{CONFIG['log_dir']}/trader.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ==================== pending 订单处理 ====================

def process_pending_orders(client: OKXClient, state: StateManager):
    """
    检查所有挂单中的开仓订单：
    - filled         → 用真实成交均价写入 long_entries / short_entries
    - canceled       → 清除
    - live / partial → 继续等待，保留在 pending_orders
    """
    pending = state.get("pending_orders", [])
    if not pending:
        logger.debug("没有 pending 订单需要处理")
        return

    logger.debug(f"检查 pending_orders，共 {len(pending)} 笔")
    remaining = []

    for p in pending:
        result = client.get_order(CONFIG["inst_id"], p["ordId"])
        if not result:
            logger.warning(f"  [PENDING] ordId={p['ordId']} 查询失败，保留等下次重试")
            remaining.append(p)
            continue

        state_val = result["state"]
        logger.info(f"  [PENDING] ordId={p['ordId']} direction={p['direction']} state={state_val} avgPx={result['avgPx']} fillSz={result['fillSz']}")

        if state_val == "filled":
            avg_px = result["avgPx"]
            fill_sz = result["fillSz"]
            if avg_px <= 0 or fill_sz <= 0:
                logger.error(f"  [PENDING] 成交价或张数异常，跳过: avgPx={avg_px} fillSz={fill_sz}")
                continue

            direction = p["direction"]
            sl_price  = avg_px * (1 - CONFIG["sl_pct"]) if direction == "long" \
                        else avg_px * (1 + CONFIG["sl_pct"])
            entry = {
                "price":     avg_px,
                "sz":        fill_sz,
                "tp1_done":  False,
                "sl_price":  sl_price,
                "direction": direction,
                "open_time": p["open_time"],
                "margin":    p["margin"],
                "lever":     p["lever"],
            }
            key     = "long_entries" if direction == "long" else "short_entries"
            entries = state.get(key, [])
            entries.append(entry)
            state.set(key, entries)
            logger.info(f"  [PENDING 成交确认] {direction} avgPx={avg_px:.2f} sz={fill_sz}张 sl={sl_price:.2f}")

        elif state_val == "canceled":
            logger.info(f"  [PENDING 已取消] ordId={p['ordId']} 清除")

        else:
            # live 或 partially_filled，继续等待
            remaining.append(p)

    state.set("pending_orders", remaining)
    state.save()
    logger.debug("—— pending 订单检查完成 ——\n")


# ==================== 启动时持仓同步 ====================

def sync_positions_from_okx(client: OKXClient, state: StateManager, price: float):
    """
    程序启动时从 OKX 拉取真实持仓，与本地 state 做对比：

    情况 A: OKX 有仓 & state 也有 → 以 OKX 张数为准更新 state
    情况 B: OKX 有仓 & state 没有 → 从 OKX 持仓重建 entry
    情况 C: OKX 无仓 & state 有   → 清除幽灵仓位
    情况 D: 都没有                 → 正常空仓
    """
    logger.debug("—— 启动同步：从 OKX 读取真实持仓 ——")
    real = client.get_positions(CONFIG["inst_id"])
    if real is None:
        logger.error("获取 OKX 持仓失败（可能是网络异常或 API 限制），跳过本次同步，保护本地状态。")
        return
        
    logger.debug(f"  OKX 实际持仓: long={real['long']} short={real['short']}")

    state_long  = state.get("long_entries",  [])
    state_short = state.get("short_entries", [])
    logger.debug(f"  本地 state : long={len(state_long)}笔  short={len(state_short)}笔")

    changed = False

    # ===== 多头同步 =====
    okx_long = real["long"]

    if okx_long and okx_long["sz"] > 0:
        okx_sz    = okx_long["sz"]
        okx_avgpx = okx_long["avgPx"]

        if state_long:
            total_state_sz = sum(e["sz"] for e in state_long)
            if abs(total_state_sz - okx_sz) > 0.005:
                logger.warning(
                    f"  [多头] 总张数不一致! state={total_state_sz} OKX={okx_sz}，"
                    f"请手动核对，本次不做任何调整"
                )
            else:
                logger.debug(f"  [多头] state 与 OKX 一致，无需修改")
    
        else:
            logger.warning(
                f"  [多头] OKX 有持仓 {okx_sz}张 均价={okx_avgpx:.2f}，state 为空，自动重建"
            )
            sl_price = okx_avgpx * (1 - CONFIG["sl_pct"])
            entry = {
                "price":     okx_avgpx,
                "sz":        okx_sz,
                "tp1_done":  False,
                "sl_price":  sl_price,
                "direction": "long",
                "open_time": "recovered",
                "margin":    0,
                "lever":     okx_long["lever"],
                "recovered": True,
            }
            state.set("long_entries", [entry])
            changed = True
            logger.info(f"  [多头] 重建完成: sz={okx_sz}张 avgPx={okx_avgpx:.2f} sl={sl_price:.2f}")
    else:
        if state_long:
            logger.warning(f"  [多头] OKX 无持仓，state 有 {len(state_long)} 笔，清除幽灵仓位")
            for entry in state_long:
                ep = entry["price"]
                lever = entry.get("lever", CONFIG["lever"])
                pnl = (price / ep) - 1.0
                state.add_trade_record({
                    "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "direction": "long",
                    "sz": entry["sz"],
                    "entry_price": ep,
                    "exit_price": price,
                    "pnl_pct": pnl,
                    "lev_pnl_pct": pnl * lever,
                    "reason": "SL / Manual"
                })
                state.inc("completed_long_trades")
            state.set("long_entries", [])
            changed = True
        else:
            logger.debug("  [多头] OKX 无仓，state 也为空，正常")

    # ===== 空头同步 =====
    okx_short = real["short"]

    if okx_short and okx_short["sz"] > 0:
        okx_sz    = okx_short["sz"]
        okx_avgpx = okx_short["avgPx"]

        if state_short:
            total_state_sz = sum(e["sz"] for e in state_short)
            if abs(total_state_sz - okx_sz) > 0.005:
                logger.warning(
                    f"  [空头] 总张数不一致! state={total_state_sz} OKX={okx_sz}，"
                    f"请手动核对，本次不做任何调整"
                )
            else:
                logger.debug(f"  [空头] state 与 OKX 一致，无需修改")
        
        else:
            logger.warning(
                f"  [空头] OKX 有持仓 {okx_sz}张 均价={okx_avgpx:.2f}，state 为空，自动重建"
            )
            sl_price = okx_avgpx * (1 + CONFIG["sl_pct"])
            entry = {
                "price":     okx_avgpx,
                "sz":        okx_sz,
                "tp1_done":  False,
                "sl_price":  sl_price,
                "direction": "short",
                "open_time": "recovered",
                "margin":    0,
                "lever":     okx_short["lever"],
                "recovered": True,
            }
            state.set("short_entries", [entry])
            changed = True
            logger.info(f"  [空头] 重建完成: sz={okx_sz}张 avgPx={okx_avgpx:.2f} sl={sl_price:.2f}")
    else:
        if state_short:
            logger.warning(f"  [空头] OKX 无持仓，state 有 {len(state_short)} 笔，清除幽灵仓位")
            for entry in state_short:
                ep = entry["price"]
                lever = entry.get("lever", CONFIG["lever"])
                pnl = (ep / price) - 1.0
                state.add_trade_record({
                    "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "direction": "short",
                    "sz": entry["sz"],
                    "entry_price": ep,
                    "exit_price": price,
                    "pnl_pct": pnl,
                    "lev_pnl_pct": pnl * lever,
                    "reason": "SL / Manual"
                })
                state.inc("completed_short_trades")
            state.set("short_entries", [])
            changed = True
        else:
            logger.debug("  [空头] OKX 无仓，state 也为空，正常")

    if changed:
        state.save()
        logger.info("  同步完成，state 已更新")
    else:
        logger.debug("  同步完成，state 无需更新")
    logger.debug("—— 持仓同步结束 ——\n")


# ==================== 止盈止损管理 ====================

def manage_long_entries(client: OKXClient, state: StateManager, price: float) -> bool:
    """
    管理所有多头仓位的止盈止损
    返回 True 表示本次执行了操作
    """
    entries     = state.get("long_entries", [])
    new_entries = []
    acted = False

    for i, entry in enumerate(entries):
        ep      = entry["price"]
        sz      = entry["sz"]
        tp1done = entry["tp1_done"]
        sl_p    = entry["sl_price"]
        pnl_pct = (price / ep) - 1.0

        logger.debug(
            f"  [多头检查] entry={ep:.2f} sz={sz}张 sl={sl_p:.2f} "
            f"pnl={pnl_pct*100:.2f}% tp1_done={tp1done}"
        )

        # ---- TP1：达到涨幅，市价平半仓，立即标记完成 ----
        if not tp1done and pnl_pct >= CONFIG["tp1_pct"]:
            tp_sz = max(0.01, round(sz * CONFIG.get("tp1_sell_prop", 0.5), 2))
            logger.info(f"  [LONG TP1] 达到 {CONFIG['tp1_pct']*100}% 涨幅，向交易所发市价平半仓指令 {tp_sz}张")
            ok = client.close_long(CONFIG["inst_id"], tp_sz, CONFIG["td_mode"], ordType="market")
            if ok:
                # 立即更新本地状态：扣减张数、标记完成、移动止损至保本
                entry["sz"] = round(sz - tp_sz, 2)
                entry["tp1_done"] = True
                entry["sl_price"] = ep  # 止损移至开仓价（保本）
                lever = entry.get("lever", CONFIG["lever"])
                state.add_trade_record({
                    "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "direction": "long",
                    "sz": tp_sz,
                    "entry_price": ep,
                    "exit_price": price,
                    "pnl_pct": pnl_pct,
                    "lev_pnl_pct": pnl_pct * lever,
                    "reason": "TP1 Partial"
                })
                logger.info(f"  [LONG TP1] 平仓成功，剩余 {entry['sz']}张，止损移至 {ep:.2f}")
                acted = True
            else:
                logger.error(f"  [LONG TP1] 市价平半仓下单失败，保留 entry 等下次重试")
                new_entries.append(entry)
                continue

        # ---- TP2 (动态追踪止损 & 硬性止盈) ----
        if tp1done:
            lever = entry.get("lever", CONFIG["lever"])
            lev_pnl_pct = pnl_pct * lever
            
            # 1. 硬性止盈检查 (30%)
            if lev_pnl_pct >= CONFIG.get("tp2_hard_pct", 0.30):
                logger.info(f"  [LONG TP2] 硬性止盈触发! 杠杆收益 {lev_pnl_pct*100:.2f}% >= {CONFIG.get('tp2_hard_pct', 0.30)*100}%，市价全平 {sz}张")
                ok = client.close_long(CONFIG["inst_id"], sz, CONFIG["td_mode"])
                if ok:
                    state.add_trade_record({
                        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                        "direction": "long",
                        "sz": sz,
                        "entry_price": ep,
                        "exit_price": price,
                        "pnl_pct": pnl_pct,
                        "lev_pnl_pct": lev_pnl_pct,
                        "reason": "TP2 Hard"
                    })
                    state.inc("completed_long_trades")
                    acted = True
                else:
                    logger.error("  [LONG TP2] 硬性止盈下单失败")
                    new_entries.append(entry)
                continue
                
            # 2. 追踪止损激活与最高点更新
            max_pnl = entry.get("max_pnl_pct", 0.0)
            if lev_pnl_pct >= CONFIG.get("tp2_active_pct", 0.20):
                if not entry.get("tp2_active"):
                    logger.info(f"  [LONG TP2] 追踪止损已激活! 当前收益 {lev_pnl_pct*100:.2f}%")
                entry["tp2_active"] = True
                
            if entry.get("tp2_active", False):
                if lev_pnl_pct > max_pnl:
                    entry["max_pnl_pct"] = lev_pnl_pct
                    max_pnl = lev_pnl_pct
                    
                # 3. 追踪止损触发检查
                trail_pct = CONFIG.get("tp2_trail_pct", 0.05)
                if lev_pnl_pct <= max_pnl - trail_pct:
                    logger.info(f"  [LONG TP2] 追踪止损触发! 最高收益 {max_pnl*100:.2f}%, 回撤至 {lev_pnl_pct*100:.2f}%，市价全平 {sz}张")
                    ok = client.close_long(CONFIG["inst_id"], sz, CONFIG["td_mode"])
                    if ok:
                        state.add_trade_record({
                            "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                            "direction": "long",
                            "sz": sz,
                            "entry_price": ep,
                            "exit_price": price,
                            "pnl_pct": pnl_pct,
                            "lev_pnl_pct": lev_pnl_pct,
                            "reason": "TP2 Trail"
                        })
                        state.inc("completed_long_trades")
                        acted = True
                    else:
                        logger.error("  [LONG TP2] 追踪止损下单失败")
                        new_entries.append(entry)
                    continue

        new_entries.append(entry)

    state.set("long_entries", new_entries)
    if acted:
        state.save()
    return acted


def manage_short_entries(client: OKXClient, state: StateManager, price: float) -> bool:
    """管理所有空头仓位的止盈止损"""
    entries     = state.get("short_entries", [])
    new_entries = []
    acted = False

    for i, entry in enumerate(entries):
        ep      = entry["price"]
        sz      = entry["sz"]
        tp1done = entry["tp1_done"]
        sl_p    = entry["sl_price"]
        pnl_pct = (ep / price) - 1.0

        logger.debug(
            f"  [空头检查] entry={ep:.2f} sz={sz}张 sl={sl_p:.2f} "
            f"pnl={pnl_pct*100:.2f}% tp1_done={tp1done}"
        )

        # ---- TP1：达到涨幅，市价平半仓，立即标记完成 ----
        if not tp1done and pnl_pct >= CONFIG["tp1_pct"]:
            tp_sz = max(0.01, round(sz * CONFIG.get("tp1_sell_prop", 0.5), 2))
            logger.info(f"  [SHORT TP1] 达到 {CONFIG['tp1_pct']*100}% 涨幅，向交易所发市价平半仓指令 {tp_sz}张")
            ok = client.close_short(CONFIG["inst_id"], tp_sz, CONFIG["td_mode"], ordType="market")
            if ok:
                # 立即更新本地状态：扣减张数、标记完成、移动止损至保本
                entry["sz"] = round(sz - tp_sz, 2)
                entry["tp1_done"] = True
                entry["sl_price"] = ep  # 止损移至开仓价（保本）
                lever = entry.get("lever", CONFIG["lever"])
                state.add_trade_record({
                    "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "direction": "short",
                    "sz": tp_sz,
                    "entry_price": ep,
                    "exit_price": price,
                    "pnl_pct": pnl_pct,
                    "lev_pnl_pct": pnl_pct * lever,
                    "reason": "TP1 Partial"
                })
                logger.info(f"  [SHORT TP1] 平仓成功，剩余 {entry['sz']}张，止损移至 {ep:.2f}")
                acted = True
            else:
                logger.error(f"  [SHORT TP1] 市价平半仓下单失败，保留 entry 等下次重试")
                new_entries.append(entry)
                continue

        # ---- TP2 (动态追踪止损 & 硬性止盈) ----
        if tp1done:
            lever = entry.get("lever", CONFIG["lever"])
            lev_pnl_pct = pnl_pct * lever
            
            # 1. 硬性止盈检查 (30%)
            if lev_pnl_pct >= CONFIG.get("tp2_hard_pct", 0.30):
                logger.info(f"  [SHORT TP2] 硬性止盈触发! 杠杆收益 {lev_pnl_pct*100:.2f}% >= {CONFIG.get('tp2_hard_pct', 0.30)*100}%，市价全平 {sz}张")
                ok = client.close_short(CONFIG["inst_id"], sz, CONFIG["td_mode"])
                if ok:
                    state.add_trade_record({
                        "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                        "direction": "short",
                        "sz": sz,
                        "entry_price": ep,
                        "exit_price": price,
                        "pnl_pct": pnl_pct,
                        "lev_pnl_pct": lev_pnl_pct,
                        "reason": "TP2 Hard"
                    })
                    state.inc("completed_short_trades")
                    acted = True
                else:
                    logger.error("  [SHORT TP2] 硬性止盈下单失败")
                    new_entries.append(entry)
                continue
                
            # 2. 追踪止损激活与最高点更新
            max_pnl = entry.get("max_pnl_pct", 0.0)
            if lev_pnl_pct >= CONFIG.get("tp2_active_pct", 0.20):
                if not entry.get("tp2_active"):
                    logger.info(f"  [SHORT TP2] 追踪止损已激活! 当前收益 {lev_pnl_pct*100:.2f}%")
                entry["tp2_active"] = True
                
            if entry.get("tp2_active", False):
                if lev_pnl_pct > max_pnl:
                    entry["max_pnl_pct"] = lev_pnl_pct
                    max_pnl = lev_pnl_pct
                    
                # 3. 追踪止损触发检查
                trail_pct = CONFIG.get("tp2_trail_pct", 0.05)
                if lev_pnl_pct <= max_pnl - trail_pct:
                    logger.info(f"  [SHORT TP2] 追踪止损触发! 最高收益 {max_pnl*100:.2f}%, 回撤至 {lev_pnl_pct*100:.2f}%，市价全平 {sz}张")
                    ok = client.close_short(CONFIG["inst_id"], sz, CONFIG["td_mode"])
                    if ok:
                        state.add_trade_record({
                            "time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                            "direction": "short",
                            "sz": sz,
                            "entry_price": ep,
                            "exit_price": price,
                            "pnl_pct": pnl_pct,
                            "lev_pnl_pct": lev_pnl_pct,
                            "reason": "TP2 Trail"
                        })
                        state.inc("completed_short_trades")
                        acted = True
                    else:
                        logger.error("  [SHORT TP2] 追踪止损下单失败")
                        new_entries.append(entry)
                    continue

        new_entries.append(entry)

    state.set("short_entries", new_entries)
    if acted:
        state.save()
    return acted


# ==================== 主逻辑 ====================

def run_high_freq_tasks(client: OKXClient, state: StateManager):
    """
    10秒高频任务：更新价格、处理待定订单、同步仓位、执行追踪止损
    """
    # 1. 获取最新市价
    price = client.get_ticker(CONFIG["inst_id"])
    if not price:
        return
        
    # 2. 处理上次挂单中的 pending 订单
    process_pending_orders(client, state)
    
    # 3. 持仓同步 (从 OKX 同步真实数量)
    sync_positions_from_okx(client, state, price)
    
    # 4. 止盈止损追踪管理 (10秒级高频检测)
    acted_long  = manage_long_entries(client, state, price)
    acted_short = manage_short_entries(client, state, price)
    if acted_long or acted_short:
        state.save()

def run_hourly_tasks(client: OKXClient, state: StateManager, engine: StrategyEngine):
    """
    小时级任务：拉取K线、计算信号、开仓
    """
    logger.info("====== 开始执行小时级策略开仓检查 ======")
    
    # 获取账户信息
    account      = client.get_account_balance(ccy=CONFIG["quote_ccy"])
    if not account:
        return
    total_equity = account["totalEq"]
    avail_bal    = account["availBal"]
    logger.info(f"账户权益: {total_equity:.4f} USDT | 可用保证金: {avail_bal:.4f} USDT")

    if total_equity <= 0:
        logger.error("账户权益为 0，检查 API 配置")
        return

    # 拉取 K 线
    klines = client.get_klines(
        instId = CONFIG["inst_id"],
        bar    = "1H",
        limit  = 300,
    )
    if not klines or len(klines) < 241:
        logger.warning(f"K 线不足({len(klines) if klines else 0})，跳过")
        return

    logger.info(f"K 线数量: {len(klines)}，最新时间: {datetime.fromtimestamp(klines[-1]['ts']/1000, tz=timezone.utc)}")

    # 计算信号
    signals = engine.compute(klines)
    price   = signals["price"]
    logger.info(
        f"price={price:.2f} | MA5={signals['ma5']:.2f} | MA10={signals['ma10']:.2f} | "
        f"MA120={signals['ma120']:.2f} | cross5={signals['cross5']} | cross10={signals['cross10']}"
    )

    # 开仓信号检测
    cross5  = signals["cross5"]
    cross10 = signals["cross10"]
    ma120   = signals["ma120"]

    # 做多信号
    if cross5 > 0 and cross10 > 0 and price > ma120:
        sz       = CONFIG.get("fixed_open_sz", 0.02)
        lever    = CONFIG["lever"]
        notional = sz * CONFIG["ct_val"] * price
        margin   = notional / lever
        logger.info(
            f"[SIGNAL LONG] price={price:.2f} 所需保证金={margin:.2f}U "
            f"({lever}x) 名义={notional:.2f}U 固定开仓={sz}张"
        )

        if avail_bal >= margin:
            tp_sz = max(0.01, round(sz * CONFIG.get("tp1_sell_prop", 0.5), 2))
            ordId = client.open_long(
                instId      = CONFIG["inst_id"],
                usdt_margin = margin,
                price       = price,
                ct_val      = CONFIG["ct_val"],
                lever       = lever,
                td_mode     = CONFIG["td_mode"],
                offset      = CONFIG.get("limit_offset", 0.001),
                sl_pct      = CONFIG.get("sl_pct", 0.05),
                # tp_pct      = CONFIG.get("tp1_pct", 0.03), # 带单员限制，交由机器人轮询处理
                # tp_sz       = tp_sz,
            )
            if ordId:
                sz     = CONFIG.get("fixed_open_sz", 0.02)
                pending = state.get("pending_orders", [])
                pending.append({
                    "ordId":     ordId,
                    "direction": "long",
                    "sz":        sz,
                    "open_time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "margin":    margin,
                    "lever":     lever,
                })
                state.set("pending_orders", pending)
                state.save()
                logger.info(f"[LONG 挂单记录] ordId={ordId} sz≈{sz}张，等待成交确认")
        else:
            logger.warning(f"可用保证金不足: avail={avail_bal:.2f} need={margin:.2f}")

    # 做空信号
    elif cross5 < 0 and cross10 < 0 and price < ma120:
        sz       = CONFIG.get("fixed_open_sz", 0.02)
        lever    = CONFIG["lever"]
        notional = sz * CONFIG["ct_val"] * price
        margin   = notional / lever
        logger.info(
            f"[SIGNAL SHORT] price={price:.2f} 所需保证金={margin:.2f}U "
            f"({lever}x) 名义={notional:.2f}U 固定开仓={sz}张"
        )

        if avail_bal >= margin:
            tp_sz = max(0.01, round(sz * CONFIG.get("tp1_sell_prop", 0.5), 2))
            ordId = client.open_short(
                instId      = CONFIG["inst_id"],
                usdt_margin = margin,
                price       = price,
                ct_val      = CONFIG["ct_val"],
                lever       = lever,
                td_mode     = CONFIG["td_mode"],
                offset      = CONFIG.get("limit_offset", 0.001),
                sl_pct      = CONFIG.get("sl_pct", 0.05),
                # tp_pct      = CONFIG.get("tp1_pct", 0.03), # 带单员限制，交由机器人轮询处理
                # tp_sz       = tp_sz,
            )
            if ordId:
                sz     = CONFIG.get("fixed_open_sz", 0.02)
                pending = state.get("pending_orders", [])
                pending.append({
                    "ordId":     ordId,
                    "direction": "short",
                    "sz":        sz,
                    "open_time": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "margin":    margin,
                    "lever":     lever,
                })
                state.set("pending_orders", pending)
                state.save()
                logger.info(f"[SHORT 挂单记录] ordId={ordId} sz≈{sz}张，等待成交确认")
        else:
            logger.warning(f"可用保证金不足: avail={avail_bal:.2f} need={margin:.2f}")

    else:
        logger.info("无开仓信号")

    # 打印当前持仓摘要及详情
    long_entries  = state.get("long_entries",  [])
    short_entries = state.get("short_entries", [])
    pending       = state.get("pending_orders", [])
    
    for entry in long_entries:
        ep = entry["price"]
        sz = entry["sz"]
        sl_p = entry["sl_price"]
        pnl_pct = (price / ep) - 1.0
        logger.info(f"  [多头检查] entry={ep:.2f} sz={sz}张 sl={sl_p:.2f} pnl={pnl_pct*100:.2f}% tp1_done={entry['tp1_done']}")

    for entry in short_entries:
        ep = entry["price"]
        sz = entry["sz"]
        sl_p = entry["sl_price"]
        pnl_pct = (ep / price) - 1.0
        logger.info(f"  [空头检查] entry={ep:.2f} sz={sz}张 sl={sl_p:.2f} pnl={pnl_pct*100:.2f}% tp1_done={entry['tp1_done']}")
    logger.info(
        f"当前持仓 | 多头: {len(long_entries)}笔 | 空头: {len(short_entries)}笔 | "
        f"pending: {len(pending)}笔 | "
        f"完成多: {state.get('completed_long_trades',0)} 完成空: {state.get('completed_short_trades',0)}"
    )
    logger.info("====== 小时级策略检查完成 ======\n")


# ==================== 程序入口 ====================

def main():
    mode = "模拟盘" if CONFIG.get("simulated") else "实盘"
    logger.info("=" * 50)
    logger.info(f"  OKX 合约实盘交易程序启动")
    logger.info(f"  交易对 : {CONFIG['inst_id']}")
    logger.info(f"  杠杆   : {CONFIG['lever']}x ({CONFIG['td_mode']})")
    logger.info(f"  模式   : {mode}")
    logger.info(f"  buy_pct: {CONFIG['buy_pct']*100:.0f}%  sl: {CONFIG['sl_pct']*100:.0f}%  tp1: {CONFIG['tp1_pct']*100:.1f}%")
    logger.info("=" * 50)

    client = OKXClient(
        api_key    = CONFIG["api_key"],
        secret_key = CONFIG["secret_key"],
        passphrase = CONFIG["passphrase"],
        simulated  = CONFIG.get("simulated", False), # 默认实盘，防止服务器缺少配置时连到模拟盘
    )
    state  = StateManager(path=CONFIG["state_file"])
    engine = StrategyEngine(CONFIG)

    # 首次启动：设置杠杆
    if not state.get("leverage_set"):
        logger.info(f"设置杠杆: {CONFIG['lever']}x ...")
        ok = client.set_leverage(CONFIG["inst_id"], CONFIG["lever"], CONFIG["td_mode"])
        if ok:
            state.set("leverage_set", True)
            state.save()
        else:
            logger.error("杠杆设置失败，本次跳过")

    # 每次程序重启时重置同步标志
    state.set("synced_this_run", False)
    state.save()
    logger.info("已重置持仓同步标志，本次启动将重新同步 OKX 持仓\n")

    # 打印初始状态让用户安心
    long_entries  = state.get("long_entries",  [])
    short_entries = state.get("short_entries", [])
    logger.info(f"成功加载本地持仓记录 | 多头: {len(long_entries)}笔 | 空头: {len(short_entries)}笔")
    
    # 启动时打印一次完整的持仓检查明细，与原来保持一致
    price = client.get_ticker(CONFIG["inst_id"])
    if not price:
        price = 1.0 # fallback
        
    for entry in long_entries:
        ep = entry["price"]
        sz = entry["sz"]
        sl_p = entry["sl_price"]
        pnl_pct = (price / ep) - 1.0
        logger.info(f"  [多头检查] entry={ep:.2f} sz={sz}张 sl={sl_p:.2f} pnl={pnl_pct*100:.2f}% tp1_done={entry['tp1_done']}")

    for entry in short_entries:
        ep = entry["price"]
        sz = entry["sz"]
        sl_p = entry["sl_price"]
        pnl_pct = (ep / price) - 1.0
        logger.info(f"  [空头检查] entry={ep:.2f} sz={sz}张 sl={sl_p:.2f} pnl={pnl_pct*100:.2f}% tp1_done={entry['tp1_done']}")

    logger.info("高频追踪雷达已启动！(每10秒后台静默巡航，触发止损/开仓时才会发声)\n")

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # 1. 10秒高频任务：价格更新与追踪止损
            run_high_freq_tasks(client, state)
            
            # 2. 小时级任务：只在每小时的 59分50秒 之后执行一次
            last_hour = state.get("last_entry_check_hour", -1)
            if now.minute == 59 and now.second >= 50 and last_hour != now.hour:
                run_hourly_tasks(client, state, engine)
                state.set("last_entry_check_hour", now.hour)
                state.save()
                
        except KeyboardInterrupt:
            logger.info("手动停止")
            break
        except Exception as e:
            logger.error(f"执行异常: {e}")
            logger.error(traceback.format_exc())

        # 每 10 秒循环一次
        time.sleep(10)


if __name__ == "__main__":
    main()
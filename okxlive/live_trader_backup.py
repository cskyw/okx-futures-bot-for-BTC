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
from datetime import datetime, timezone

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


# ==================== 启动时持仓同步 ====================

def sync_positions_from_okx(client: OKXClient, state: StateManager, price: float):
    """
    程序启动时从 OKX 拉取真实持仓，与本地 state 做三向对比：

    情况 A: OKX 有仓 & state 也有 → 以 OKX 张数为准更新 state（防止部分平仓未保存）
    情况 B: OKX 有仓 & state 没有 → 从 OKX 持仓重建 entry（用均价/止损保守估算）
    情况 C: OKX 无仓 & state 有   → state 里是幽灵仓位，清除
    情况 D: 都没有                 → 正常空仓
    """
    logger.info("—— 启动同步：从 OKX 读取真实持仓 ——")
    real = client.get_positions(CONFIG["inst_id"])
    logger.info(f"  OKX 实际持仓: long={real['long']} short={real['short']}")

    state_long  = state.get("long_entries",  [])
    state_short = state.get("short_entries", [])
    logger.info(f"  本地 state : long={len(state_long)}笔  short={len(state_short)}笔")

    changed = False

    # ===== 多头同步 =====
    okx_long = real["long"]   # None 或 { sz, avgPx, upl, lever }

    if okx_long and okx_long["sz"] > 0:
        okx_sz    = okx_long["sz"]
        okx_avgpx = okx_long["avgPx"]

        if state_long:
            # 情况 A：OKX 有，state 也有 —— 校正张数
            total_state_sz = sum(e["sz"] for e in state_long)
            if abs(total_state_sz - okx_sz) > 0.5:
                logger.warning(
                    f"  [多头] state 张数({total_state_sz}) ≠ OKX({okx_sz})，"
                    f"按比例缩放 state entry"
                )
                ratio = okx_sz / total_state_sz
                for e in state_long:
                    e["sz"] = max(1, round(e["sz"] * ratio))
                state.set("long_entries", state_long)
                changed = True
            else:
                logger.info(f"  [多头] state 与 OKX 一致，无需修改")
        else:
            # 情况 B：OKX 有，state 没有 —— 重建 entry
            logger.warning(
                f"  [多头] OKX 有持仓 {okx_sz}张 均价={okx_avgpx:.2f}，"
                f"但 state 为空，自动重建 entry"
            )
            sl_price = okx_avgpx * (1 - CONFIG["sl_pct"])
            entry = {
                "price":     okx_avgpx,
                "sz":        int(okx_sz),
                "tp1_done":  False,
                "sl_price":  sl_price,
                "direction": "long",
                "open_time": "recovered",
                "margin":    0,       # 无法恢复原始保证金，置 0
                "lever":     okx_long["lever"],
                "recovered": True,    # 标记为恢复仓位
            }
            state.set("long_entries", [entry])
            changed = True
            logger.info(
                f"  [多头] 重建完成: sz={int(okx_sz)}张 avgPx={okx_avgpx:.2f} "
                f"sl={sl_price:.2f}"
            )
    else:
        if state_long:
            # 情况 C：OKX 无仓，state 有 —— 清除幽灵仓位
            logger.warning(
                f"  [多头] OKX 无持仓，但 state 有 {len(state_long)} 笔记录，"
                f"清除幽灵仓位"
            )
            state.set("long_entries", [])
            changed = True
        else:
            logger.info("  [多头] OKX 无仓，state 也为空，正常")

    # ===== 空头同步 =====
    okx_short = real["short"]

    if okx_short and okx_short["sz"] > 0:
        okx_sz    = okx_short["sz"]
        okx_avgpx = okx_short["avgPx"]

        if state_short:
            total_state_sz = sum(e["sz"] for e in state_short)
            if abs(total_state_sz - okx_sz) > 0.5:
                logger.warning(
                    f"  [空头] state 张数({total_state_sz}) ≠ OKX({okx_sz})，"
                    f"按比例缩放 state entry"
                )
                ratio = okx_sz / total_state_sz
                for e in state_short:
                    e["sz"] = max(1, round(e["sz"] * ratio))
                state.set("short_entries", state_short)
                changed = True
            else:
                logger.info(f"  [空头] state 与 OKX 一致，无需修改")
        else:
            logger.warning(
                f"  [空头] OKX 有持仓 {okx_sz}张 均价={okx_avgpx:.2f}，"
                f"但 state 为空，自动重建 entry"
            )
            sl_price = okx_avgpx * (1 + CONFIG["sl_pct"])
            entry = {
                "price":     okx_avgpx,
                "sz":        int(okx_sz),
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
            logger.info(
                f"  [空头] 重建完成: sz={int(okx_sz)}张 avgPx={okx_avgpx:.2f} "
                f"sl={sl_price:.2f}"
            )
    else:
        if state_short:
            logger.warning(
                f"  [空头] OKX 无持仓，但 state 有 {len(state_short)} 笔记录，"
                f"清除幽灵仓位"
            )
            state.set("short_entries", [])
            changed = True
        else:
            logger.info("  [空头] OKX 无仓，state 也为空，正常")

    if changed:
        state.save()
        logger.info("  同步完成，state 已更新")
    else:
        logger.info("  同步完成，state 无需更新")
    logger.info("—— 持仓同步结束 ——\n")


# ==================== 止盈止损管理 ====================

def manage_long_entries(client: OKXClient, state: StateManager, price: float) -> bool:
    """
    管理所有多头仓位的止盈止损
    返回 True 表示本次执行了操作（主循环应 return，等下次再开仓）
    """
    entries     = state.get("long_entries", [])
    new_entries = []
    acted       = False

    for i, entry in enumerate(entries):
        ep      = entry["price"]
        sz      = entry["sz"]          # 张数
        tp1done = entry["tp1_done"]
        sl_p    = entry["sl_price"]
        pnl_pct = (price / ep) - 1.0

        logger.info(
            f"  [多头检查] entry={ep:.2f} sz={sz}张 sl={sl_p:.2f} "
            f"pnl={pnl_pct*100:.2f}% tp1_done={tp1done}"
        )

        # ---- TP1 ----
        if not tp1done and pnl_pct >= CONFIG["tp1_pct"]:
            close_sz = max(1, int(sz * CONFIG["tp1_sell_prop"]))
            logger.info(f"  [LONG TP1] 触发! 平 {close_sz}张")
            ok = client.close_long(CONFIG["inst_id"], close_sz, CONFIG["td_mode"])
            if ok:
                entry["tp1_done"] = True
                entry["sz"]       = sz - close_sz
                entry["sl_price"] = ep          # 保本移损
                if entry["sz"] > 0:
                    new_entries.append(entry)
                logger.info(f"  [LONG TP1] 完成，剩余 {entry['sz']}张，止损移至 {ep:.2f}")
            else:
                logger.error("  [LONG TP1] 下单失败，保留原 entry")
                new_entries.append(entry)
            # 追加本次未处理的后续 entry
            new_entries += entries[i+1:]
            state.set("long_entries", new_entries)
            state.save()
            return True

        # ---- SL ----
        if price <= sl_p:
            logger.info(f"  [LONG SL] 触发! entry={ep:.2f} sl={sl_p:.2f} 全平 {sz}张")
            ok = client.close_long(CONFIG["inst_id"], sz, CONFIG["td_mode"])
            if ok:
                logger.info(f"  [LONG SL] 完成")
            else:
                logger.error("  [LONG SL] 下单失败，保留原 entry")
                new_entries.append(entry)
            new_entries += entries[i+1:]
            state.set("long_entries", new_entries)
            state.save()
            return True

        # ---- TP2 ----
        if tp1done and pnl_pct >= CONFIG["tp2_pct"]:
            logger.info(f"  [LONG TP2] 触发! 全平 {sz}张")
            ok = client.close_long(CONFIG["inst_id"], sz, CONFIG["td_mode"])
            if ok:
                state.inc("completed_long_trades")
                logger.info(f"  [LONG TP2] 完成，累计完成多头: {state.get('completed_long_trades')}次")
            else:
                logger.error("  [LONG TP2] 下单失败，保留原 entry")
                new_entries.append(entry)
            new_entries += entries[i+1:]
            state.set("long_entries", new_entries)
            state.save()
            return True

        new_entries.append(entry)

    state.set("long_entries", new_entries)
    return acted


def manage_short_entries(client: OKXClient, state: StateManager, price: float) -> bool:
    """管理所有空头仓位的止盈止损"""
    entries     = state.get("short_entries", [])
    new_entries = []
    acted       = False

    for i, entry in enumerate(entries):
        ep      = entry["price"]
        sz      = entry["sz"]
        tp1done = entry["tp1_done"]
        sl_p    = entry["sl_price"]
        pnl_pct = (ep / price) - 1.0

        logger.info(
            f"  [空头检查] entry={ep:.2f} sz={sz}张 sl={sl_p:.2f} "
            f"pnl={pnl_pct*100:.2f}% tp1_done={tp1done}"
        )

        # ---- TP1 ----
        if not tp1done and pnl_pct >= CONFIG["tp1_pct"]:
            close_sz = max(1, int(sz * CONFIG["tp1_sell_prop"]))
            logger.info(f"  [SHORT TP1] 触发! 平 {close_sz}张")
            ok = client.close_short(CONFIG["inst_id"], close_sz, CONFIG["td_mode"])
            if ok:
                entry["tp1_done"] = True
                entry["sz"]       = sz - close_sz
                entry["sl_price"] = ep
                if entry["sz"] > 0:
                    new_entries.append(entry)
                logger.info(f"  [SHORT TP1] 完成，剩余 {entry['sz']}张，止损移至 {ep:.2f}")
            else:
                logger.error("  [SHORT TP1] 下单失败，保留原 entry")
                new_entries.append(entry)
            new_entries += entries[i+1:]
            state.set("short_entries", new_entries)
            state.save()
            return True

        # ---- SL ----
        if price >= sl_p:
            logger.info(f"  [SHORT SL] 触发! entry={ep:.2f} sl={sl_p:.2f} 全平 {sz}张")
            ok = client.close_short(CONFIG["inst_id"], sz, CONFIG["td_mode"])
            if ok:
                logger.info(f"  [SHORT SL] 完成")
            else:
                logger.error("  [SHORT SL] 下单失败，保留原 entry")
                new_entries.append(entry)
            new_entries += entries[i+1:]
            state.set("short_entries", new_entries)
            state.save()
            return True

        # ---- TP2 ----
        if tp1done and pnl_pct >= CONFIG["tp2_pct"]:
            logger.info(f"  [SHORT TP2] 触发! 全平 {sz}张")
            ok = client.close_short(CONFIG["inst_id"], sz, CONFIG["td_mode"])
            if ok:
                state.inc("completed_short_trades")
                logger.info(f"  [SHORT TP2] 完成，累计完成空头: {state.get('completed_short_trades')}次")
            else:
                logger.error("  [SHORT TP2] 下单失败，保留原 entry")
                new_entries.append(entry)
            new_entries += entries[i+1:]
            state.set("short_entries", new_entries)
            state.save()
            return True

        new_entries.append(entry)

    state.set("short_entries", new_entries)
    return acted


# ==================== 主逻辑 ====================

def run_once():
    logger.info("====== 开始执行策略检查 ======")

    client = OKXClient(
        api_key    = CONFIG["api_key"],
        secret_key = CONFIG["secret_key"],
        passphrase = CONFIG["passphrase"],
        simulated  = CONFIG.get("simulated", True),
    )
    state  = StateManager(path=CONFIG["state_file"])
    engine = StrategyEngine(CONFIG)

    # ---- 首次启动：设置杠杆 ----
    if not state.get("leverage_set"):
        logger.info(f"设置杠杆: {CONFIG['lever']}x ...")
        ok = client.set_leverage(CONFIG["inst_id"], CONFIG["lever"], CONFIG["td_mode"])
        if ok:
            state.set("leverage_set", True)
            state.save()
        else:
            logger.error("杠杆设置失败，本次跳过")
            return

    # ---- 拉取 K 线（先拿价格供同步使用）----
    klines = client.get_klines(
        instId = CONFIG["inst_id"],
        bar    = "1H",
        limit  = 300,
    )
    if not klines or len(klines) < 241:
        logger.warning(f"K 线不足({len(klines) if klines else 0})，跳过")
        return

    logger.info(f"K 线数量: {len(klines)}，最新时间: {datetime.fromtimestamp(klines[-1]['ts']/1000, tz=timezone.utc)}")

    # ---- 计算信号（先算出 price，供同步使用）----
    signals = engine.compute(klines)
    price   = signals["price"]
    logger.info(
        f"price={price:.2f} | MA5={signals['ma5']:.2f} | MA10={signals['ma10']:.2f} | "
        f"MA120={signals['ma120']:.2f} | cross5={signals['cross5']} | cross10={signals['cross10']}"
    )

    # ---- 每次启动都做一次持仓同步（防止程序崩溃/手动操作导致 state 与 OKX 不一致）----
    if not state.get("synced_this_run"):
        sync_positions_from_okx(client, state, price)
        state.set("synced_this_run", True)
        state.save()

    # ---- 账户信息 ----
    account      = client.get_account_balance(ccy=CONFIG["quote_ccy"])
    total_equity = account["totalEq"]
    avail_bal    = account["availBal"]
    logger.info(f"账户权益: {total_equity:.4f} USDT | 可用保证金: {avail_bal:.4f} USDT")

    if total_equity <= 0:
        logger.error("账户权益为 0，检查 API 配置")
        return

    # ---- 止盈止损管理（优先执行） ----
    acted_long  = manage_long_entries(client, state, price)
    acted_short = manage_short_entries(client, state, price)

    if acted_long or acted_short:
        logger.info("本次执行了止盈/止损操作，跳过开仓信号检测")
        state.save()
        return

    # ---- 开仓信号检测 ----
    cross5  = signals["cross5"]
    cross10 = signals["cross10"]
    ma120   = signals["ma120"]

    # 做多信号
    if cross5 > 0 and cross10 > 0 and price > ma120:
        margin     = total_equity * CONFIG["buy_pct"]   # 投入的保证金（USDT）
        lever      = CONFIG["lever"]
        notional   = margin * lever                     # 名义价值
        logger.info(
            f"[SIGNAL LONG] price={price:.2f} 保证金={margin:.2f}U "
            f"({lever}x) 名义={notional:.2f}U"
        )

        if avail_bal >= margin:
            sz = client.open_long(
                instId      = CONFIG["inst_id"],
                usdt_margin = margin,
                price       = price,
                ct_val      = CONFIG["ct_val"],
                lever       = lever,
                td_mode     = CONFIG["td_mode"],
                offset      = CONFIG.get("limit_offset", 0.001),  # 👈 新增
            )
            if sz:
                exec_price = client.get_last_fill_price(CONFIG["inst_id"]) or price
                sl_price   = exec_price * (1 - CONFIG["sl_pct"])
                entry = {
                    "price":     exec_price,
                    "sz":        sz,
                    "tp1_done":  False,
                    "sl_price":  sl_price,
                    "direction": "long",
                    "open_time": datetime.now(timezone.utc).isoformat(),
                    "margin":    margin,
                    "lever":     lever,
                }
                long_entries = state.get("long_entries", [])
                long_entries.append(entry)
                state.set("long_entries", long_entries)
                state.save()
                logger.info(
                    f"[LONG OPENED] exec={exec_price:.2f} sz={sz}张 "
                    f"sl={sl_price:.2f} ({CONFIG['sl_pct']*100:.0f}%下方)"
                )
        else:
            logger.warning(f"可用保证金不足: avail={avail_bal:.2f} need={margin:.2f}")

    # 做空信号
    elif cross5 < 0 and cross10 < 0 and price < ma120:
        margin   = total_equity * CONFIG["buy_pct"]
        lever    = CONFIG["lever"]
        notional = margin * lever
        logger.info(
            f"[SIGNAL SHORT] price={price:.2f} 保证金={margin:.2f}U "
            f"({lever}x) 名义={notional:.2f}U"
        )

        if avail_bal >= margin:
            sz = client.open_short(
                instId      = CONFIG["inst_id"],
                usdt_margin = margin,
                price       = price,
                ct_val      = CONFIG["ct_val"],
                lever       = lever,
                td_mode     = CONFIG["td_mode"],
                offset      = CONFIG.get("limit_offset", 0.001),  # 👈 新增
            )
            if sz:
                exec_price = client.get_last_fill_price(CONFIG["inst_id"]) or price
                sl_price   = exec_price * (1 + CONFIG["sl_pct"])
                entry = {
                    "price":     exec_price,
                    "sz":        sz,
                    "tp1_done":  False,
                    "sl_price":  sl_price,
                    "direction": "short",
                    "open_time": datetime.now(timezone.utc).isoformat(),
                    "margin":    margin,
                    "lever":     lever,
                }
                short_entries = state.get("short_entries", [])
                short_entries.append(entry)
                state.set("short_entries", short_entries)
                state.save()
                logger.info(
                    f"[SHORT OPENED] exec={exec_price:.2f} sz={sz}张 "
                    f"sl={sl_price:.2f} ({CONFIG['sl_pct']*100:.0f}%上方)"
                )
        else:
            logger.warning(f"可用保证金不足: avail={avail_bal:.2f} need={margin:.2f}")

    else:
        logger.info("无开仓信号")

    # 打印当前持仓摘要
    long_entries  = state.get("long_entries",  [])
    short_entries = state.get("short_entries", [])
    logger.info(
        f"当前持仓 | 多头: {len(long_entries)}笔 | 空头: {len(short_entries)}笔 | "
        f"完成多: {state.get('completed_long_trades',0)} 完成空: {state.get('completed_short_trades',0)}"
    )
    logger.info("====== 策略检查完成 ======\n")


# ==================== 程序入口 ====================

def main():
    mode = "模拟盘" if CONFIG.get("simulated") else "实盘"
    logger.info("=" * 50)
    logger.info(f"  OKX 合约实盘交易程序启动")
    logger.info(f"  交易对 : {CONFIG['inst_id']}")
    logger.info(f"  杠杆   : {CONFIG['lever']}x ({CONFIG['td_mode']})")
    logger.info(f"  模式   : {mode}")
    logger.info(f"  buy_pct: {CONFIG['buy_pct']*100:.0f}%  sl: {CONFIG['sl_pct']*100:.0f}%  tp1: {CONFIG['tp1_pct']*100:.0f}%  tp2: {CONFIG['tp2_pct']*100:.0f}%")
    logger.info("=" * 50)

    # 每次程序重启时重置同步标志，确保第一次 run_once 必定触发持仓同步
    _init_state = StateManager(path=CONFIG["state_file"])
    _init_state.set("synced_this_run", False)
    _init_state.save()
    logger.info("已重置持仓同步标志，本次启动将重新同步 OKX 持仓\n")

    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            logger.info("手动停止")
            break
        except Exception as e:
            logger.error(f"执行异常: {e}")
            logger.error(traceback.format_exc())

        now = datetime.now(timezone.utc)

        # 计算距离“下一个整点前5秒”的时间
        wait_s = 3600 - (now.minute * 60 + now.second) - 5

        # 如果已经错过本小时触发点，则顺延到下一小时
        if wait_s <= 0:
            wait_s += 3600

        wake = datetime.fromtimestamp(time.time() + wait_s, tz=timezone.utc)

        logger.info(
            f"下次执行: {wake.strftime('%Y-%m-%d %H:%M:%S UTC')} (等待 {int(wait_s)}s)\n"
        )

        time.sleep(wait_s)

if __name__ == "__main__":
    main()

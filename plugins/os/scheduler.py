"""定时调度 + AI 决策引擎 - 重构版"""
import os
import json
import asyncio
import time
import re
import copy
from pathlib import Path
from datetime import datetime, timedelta

ASSETS_DIR = str(Path(__file__).parent / "assets")
ACCOUNT_FILE = os.path.join(ASSETS_DIR, "account.json")
TRADES_FILE = os.path.join(ASSETS_DIR, "trades.json")
CONFIG_FILE = os.path.join(ASSETS_DIR, "config.json")
DIARY_DIR = os.path.join(ASSETS_DIR, "diary")
DECISIONS_DIR = os.path.join(ASSETS_DIR, "decisions")
ANALYSIS_DIR = os.path.join(ASSETS_DIR, "analysis")
SNAPSHOT_FILE = os.path.join(ASSETS_DIR, "latest_snapshot.json")

os.makedirs(DIARY_DIR, exist_ok=True)
os.makedirs(DECISIONS_DIR, exist_ok=True)
os.makedirs(ANALYSIS_DIR, exist_ok=True)

STOP_LOSS = -0.08
TAKE_PROFIT = 0.15
MAX_SINGLE_POSITION = 0.30
MAX_DAILY_TRADES = 3
COOL_OFF_DAYS = 3
FIRST_BUY_RATIO = 0.10

_last_decision_time = 0


def _load(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_account():
    account = _load(ACCOUNT_FILE)
    if not account:
        config = _load(CONFIG_FILE)
        if config:
            account = {
                "cash": config.get("initial_capital", 500000),
                "initial_capital": config.get("initial_capital", 500000),
                "holdings": {},
                "today_trade_count": 0,
                "total_trades": 0,
                "win_trades": 0,
                "lose_trades": 0,
                "consecutive_loses": 0,
                "last_trade_date": "",
                "cool_off_until": ""
            }
    return account


def save_account(d):
    _save(ACCOUNT_FILE, d)


def load_trades():
    return _load(TRADES_FILE) or []


def save_trades(d):
    _save(TRADES_FILE, d[-200:])


def load_config():
    return _load(CONFIG_FILE) or {
        "watchlist": [],
        "running": False,
        "interval": 900,
        "initial_capital": 500000,
        "risk": "稳健",
        "last_quote_time": ""
    }


def save_config(d):
    _save(CONFIG_FILE, d)


def get_today_str():
    return datetime.now().strftime("%Y-%m-%d")


def get_now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def push_ws(agent, data):
    import core
    try:
        await agent.system.call(core.Envelop(
            sender=f"applications/{Path(__file__).parent.name}/scheduler",
            receiver="os/_websocket",
            payload={"action": "push", "channel_id": "trader_dashboard", "data": data},
        ))
    except Exception as e:
        agent.log.error(f"[Scheduler] push_ws: {e}")


async def call_data(agent, action, **kw):
    import core
    r = await agent.system.call(core.Envelop(
        sender=f"applications/{Path(__file__).parent.name}/scheduler",
        receiver=f"applications/{Path(__file__).parent.name}/data",
        payload={"action": action, **kw},
    ))
    return r.payload if r else {}


def execute_trade(account, trades, code, action, price, shares, reason):
    """执行交易 - shares 必须是100的整数倍"""
    today = get_today_str()
    now = get_now_str()
    cash = account["cash"]
    holdings = account.get("holdings", {})

    if action == "buy":
        cost = shares * price
        if cost > cash:
            return None, "现金不足"
        
        account["cash"] = cash - cost
        
        if code not in holdings:
            holdings[code] = {"shares": 0, "cost": 0, "market_value": 0, "buy_date": today}
        
        h = holdings[code]
        old_cost = h["cost"] * h["shares"]
        h["shares"] += shares
        h["cost"] = (old_cost + cost) / h["shares"] if h["shares"] > 0 else price
        h["market_value"] = h["shares"] * price
        h["buy_date"] = today
        
        trade = {"date": today, "time": now, "code": code, "action": "buy", 
                 "price": price, "shares": shares, "amount": cost, "reason": reason}
        trades.append(trade)
        account["today_trade_count"] = account.get("today_trade_count", 0) + 1
        account["last_trade_date"] = today
        return trade, "成功"

    elif action == "sell":
        h = holdings.get(code)
        if not h or h["shares"] <= 0:
            return None, "无持仓"
        
        if h.get("buy_date") == today:
            return None, "T+1锁定，今日不能卖出"
        
        if shares > h["shares"]:
            shares = h["shares"]
        
        revenue = shares * price
        account["cash"] = cash + revenue
        h["shares"] -= shares
        
        if h["shares"] == 0:
            profit = revenue - h["cost"] * shares
            account["total_trades"] = account.get("total_trades", 0) + 1
            if profit > 0:
                account["win_trades"] = account.get("win_trades", 0) + 1
                account["consecutive_loses"] = 0
            else:
                account["lose_trades"] = account.get("lose_trades", 0) + 1
                account["consecutive_loses"] = account.get("consecutive_loses", 0) + 1
            del holdings[code]
        else:
            h["market_value"] = h["shares"] * price
        
        trade = {"date": today, "time": now, "code": code, "action": "sell",
                 "price": price, "shares": shares, "amount": revenue, "reason": reason}
        trades.append(trade)
        account["today_trade_count"] = account.get("today_trade_count", 0) + 1
        account["last_trade_date"] = today
        return trade, "成功"

    return None, "未知操作"


def check_stop_conditions(account, stock_data):
    """止损止盈检查 - 返回建议卖出的股票"""
    today = get_today_str()
    sell_suggestions = []
    
    for code, h in account.get("holdings", {}).items():
        if h.get("buy_date") == today:
            continue  # T+1 不能卖
        
        if code not in stock_data:
            continue
        
        price = stock_data[code]["price"]
        pct = (price - h["cost"]) / h["cost"]
        
        if pct <= STOP_LOSS:
            sell_suggestions.append({
                "code": code,
                "action": "sell",
                "shares": h["shares"],
                "reason": f"止损 {abs(pct)*100:.1f}%"
            })
        elif pct >= TAKE_PROFIT:
            sell_suggestions.append({
                "code": code,
                "action": "sell",
                "shares": int(h["shares"] * 0.5),
                "reason": f"止盈 +{pct*100:.1f}%"
            })
    
    return sell_suggestions


def build_snapshot(agent, stock_data, account, trades_today, analysis, actions):
    """构建快照 - 数据一致的核心"""
    total_value = account["cash"] + sum(h.get("market_value", 0) for h in account.get("holdings", {}).values())
    
    snapshot = {
        "time": get_now_str(),
        "account": {
            "cash": account["cash"],
            "market_value": total_value - account["cash"],
            "total": total_value,
            "initial_capital": account.get("initial_capital", 500000),
            "profit": total_value - account.get("initial_capital", 500000),
            "profit_pct": (total_value - account.get("initial_capital", 500000)) / account.get("initial_capital", 500000) * 100
        },
        "holdings": account.get("holdings", {}),
        "quotes": stock_data,
        "today_trades": trades_today,
        "analysis": analysis,
        "actions": actions
    }
    return snapshot


async def ai_think(agent, snapshot):
    """AI 决策 - 基于快照数据"""
    if not agent.llm:
        return None, []
    
    account_data = snapshot["account"]
    holdings = snapshot["holdings"]
    quotes = snapshot["quotes"]
    today_trades = snapshot.get("today_trades", [])
    
    now = datetime.now()
    remaining = 0
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        remaining = (15 - now.hour) * 60 - now.minute + 30
    
    # 构建 prompt
    prompt = f"""你是A股交易员。当前时间 {now.strftime('%H:%M:%S')}，距离收盘还有 {max(0, remaining)} 分钟。

【账户状态】
总资产: ¥{account_data['total']:,.0f}
现金: ¥{account_data['cash']:,.0f}
持仓市值: ¥{account_data['market_value']:,.0f}
今日盈亏: ¥{account_data['profit']:,.0f} ({account_data['profit_pct']:+.2f}%)

【今日已操作】
{json.dumps(today_trades, ensure_ascii=False, indent=2) if today_trades else '无'}

【持仓明细】
"""
    for code, h in holdings.items():
        q = quotes.get(code, {})
        buy_date = h.get("buy_date", "")
        t1_lock = "（T+1锁定，今日不可卖）" if buy_date == get_today_str() else ""
        pct = ((q.get('price', 0) - h['cost']) / h['cost'] * 100) if h['cost'] > 0 else 0
        prompt += f"""
{code}:
  持仓: {h['shares']}股, 成本¥{h['cost']:.2f}
  现价: ¥{q.get('price', 0):.2f}, 盈亏: {pct:+.2f}% {t1_lock}
  今日: 开盘{q.get('open', 0):.2f} 最高{q.get('high', 0):.2f} 最低{q.get('low', 0):.2f}
  成交量: {q.get('volume', 0):,}手
"""

    prompt += f"""

【自选股行情】
"""
    for code, q in quotes.items():
        if code not in holdings:
            prompt += f"{code}: ¥{q.get('price', 0):.2f} ({q.get('change_pct', 0):+.2f}%) 成交量{q.get('volume', 0):,}手\n"

    prompt += """
【交易规则】
- T+1：今天买入的股票今天不能卖
- 单只股票不超过总资产的30%
- 每次买卖至少100股（1手）
- 日交易次数不超过3次

【任务】
基于今天开盘到现在的走势，给出截至当前的操作建议。

输出格式：
## 盘面分析
...

## 逐只点评
...

## 操作建议
{"actions": [
    {"code": "600839", "action": "sell", "shares": 1000, "reason": "理由"},
    {"code": "000617", "action": "buy", "shares": 500, "reason": "理由"}
]}

注意：shares 必须是100的整数倍，action 只能是 buy 或 sell。
"""

    try:
        response = await agent.llm.chat([
            {"role": "system", "content": "你是中国A股交易员，必须用中文回复。输出Markdown分析报告，最后一行输出JSON。"},
            {"role": "user", "content": prompt}
        ])
        
        if not response or response.startswith("[LLM"):
            agent.log.error(f"[Scheduler] AI failed: {response}")
            return None, []
        
        # 解析 JSON actions
        actions = []
        import re
        json_pattern = r'\{[^{}]*"actions"\s*:\s*\[[^\]]*\][^{}]*\}'
        match = re.search(json_pattern, response, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
                actions = data.get("actions", [])
                # 过滤有效 action
                valid_actions = []
                for act in actions:
                    if act.get("code") and act.get("action") in ("buy", "sell"):
                        shares = act.get("shares", 0)
                        if shares > 0 and shares % 100 == 0:
                            valid_actions.append(act)
                        else:
                            agent.log.warning(f"[Scheduler] Invalid shares: {shares}")
                actions = valid_actions
                agent.log.info(f"[Scheduler] Parsed {len(actions)} actions")
                # 移除 JSON 部分
                response = response[:match.start()].strip() + response[match.end():].strip()
            except json.JSONDecodeError as e:
                agent.log.error(f"[Scheduler] JSON parse error: {e}")
        
        return response, actions
        
    except Exception as e:
        agent.log.error(f"[Scheduler] AI error: {e}")
        return None, []


async def run_decision_cycle(agent, mode="auto", force_refresh=False):
    """执行一次完整的决策周期"""
    global _last_decision_time
    
    config = load_config()
    if mode != "run_once" and not config.get("running"):
        return
    
    # 1. 获取最新行情
    watchlist = config.get("watchlist", [])
    if not watchlist:
        agent.log.warning("[Scheduler] Watchlist empty")
        return
    
    result = await call_data(agent, "batch_quote", codes=watchlist, force=force_refresh)
    stock_data = result.get("quotes", {})
    if not stock_data:
        agent.log.warning("[Scheduler] No stock data")
        return
    
    # 2. 加载账户和交易记录
    account = load_account()
    if not account:
        agent.log.error("[Scheduler] No account")
        return
    
    trades = load_trades()
    today = get_today_str()
    trades_today = [t for t in trades if t.get("date") == today]
    
    # 3. 更新持仓市值
    for code, h in account.get("holdings", {}).items():
        if code in stock_data:
            h["market_value"] = h["shares"] * stock_data[code]["price"]
    
    # 4. 止损止盈检查
    sell_suggestions = check_stop_conditions(account, stock_data)
    for suggestion in sell_suggestions:
        if account["today_trade_count"] >= MAX_DAILY_TRADES:
            break
        trade, msg = execute_trade(
            account, trades,
            suggestion["code"], suggestion["action"],
            stock_data[suggestion["code"]]["price"],
            suggestion["shares"], suggestion["reason"]
        )
        if trade:
            agent.log.info(f"[Scheduler] Auto {suggestion['action']}: {suggestion['code']} {suggestion['shares']}股 - {msg}")
    
    # 5. 构建快照
    snapshot = build_snapshot(agent, stock_data, account, trades_today, "", [])
    
    # 6. AI 决策
    analysis, ai_actions = await ai_think(agent, snapshot)
    
    # 7. 执行 AI 建议的交易
    for act in ai_actions:
        if account["today_trade_count"] >= MAX_DAILY_TRADES:
            agent.log.info("[Scheduler] Max daily trades reached")
            break
        
        code = act["code"]
        if code not in stock_data:
            agent.log.warning(f"[Scheduler] {code} not in stock_data")
            continue
        
        price = stock_data[code]["price"]
        shares = act.get("shares", 0)
        
        # 买入时检查仓位限制
        if act["action"] == "buy":
            total_value = account["cash"] + sum(h.get("market_value", 0) for h in account.get("holdings", {}).values())
            current_position = account.get("holdings", {}).get(code, {}).get("market_value", 0)
            if current_position + shares * price > total_value * MAX_SINGLE_POSITION:
                agent.log.info(f"[Scheduler] Position limit exceeded for {code}")
                continue
        
        trade, msg = execute_trade(
            account, trades,
            code, act["action"], price, shares, act.get("reason", "")
        )
        if trade:
            agent.log.info(f"[Scheduler] AI {act['action']}: {code} {shares}股 @{price} - {msg}")
            trades_today = [t for t in trades if t.get("date") == today]
    
    # 8. 保存数据
    save_account(account)
    save_trades(trades)
    
    # 9. 更新快照
    final_snapshot = build_snapshot(agent, stock_data, account, trades_today, analysis, ai_actions)
    _save(SNAPSHOT_FILE, final_snapshot)
    
    # 10. 推送 WebSocket
    await push_ws(agent, {
        "type": "status",
        "snapshot": final_snapshot,
        "last_update": get_now_str()
    })
    
    agent.log.info(f"[Scheduler] Cycle done, stocks={len(stock_data)}, trades={account['today_trade_count']}")
    _last_decision_time = time.time()


async def _loop(agent):
    """主循环"""
    config = load_config()
    config["running"] = True
    save_config(config)
    agent.log.info("[Scheduler] Loop started")
    
    while True:
        try:
            config = load_config()
            if not config.get("running"):
                await asyncio.sleep(5)
                continue
            
            now = datetime.now()
            if now.weekday() >= 5:
                await asyncio.sleep(3600)
                continue
            
            h, m = now.hour, now.minute
            is_trading = (9 <= h < 11) or (h == 11 and m <= 30) or (13 <= h < 15)
            
            if is_trading:
                interval = config.get("interval", 900)
                await run_decision_cycle(agent, "auto", force_refresh=True)
                await push_ws(agent, {"type": "next_update", "next_in": interval})
                await asyncio.sleep(interval)
            else:
                await asyncio.sleep(60)
        except Exception as e:
            agent.log.error(f"[Scheduler] Loop error: {e}")
            await asyncio.sleep(60)


async def execute(envelop, agent):
    action = envelop.payload.get("action", "start")
    
    if action == "start":
        if load_config().get("running"):
            envelop.payload = {"success": True, "message": "already running"}
            return envelop
        asyncio.create_task(_loop(agent))
        envelop.payload = {"success": True, "message": "started"}
        return envelop
        
    elif action == "stop":
        c = load_config()
        c["running"] = False
        save_config(c)
        envelop.payload = {"success": True}
        return envelop
        
    elif action == "run_once":
        mode = envelop.payload.get("mode", "short")
        agent.log.info(f"[Scheduler] run_once, mode={mode}")
        await run_decision_cycle(agent, "run_once", force_refresh=True)
        envelop.payload = {"success": True, "message": "done"}
        return envelop
        
    elif action == "get_snapshot":
        snap = _load(SNAPSHOT_FILE)
        envelop.payload = {"success": True, "snapshot": snap} if snap else {"success": False}
        return envelop
        
    else:
        envelop.payload = {"error": f"Unknown: {action}"}
        return envelop
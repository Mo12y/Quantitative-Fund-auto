"""盘中行情快照（数据源扩展计划书 阶段 5「补实时行情时滞」）。

问题
----
`fund_nav` / `index_daily` 都是 T+1 数据 —— 双源校验实测（2026-09-27）本地净值比
第二源**落后 4~6 天**。界面上"市场温度 / 板块排名"看到的可能是几天前的位置。
本模块用东财 push2 抓**指数盘中快照**，给界面一个"现在大盘在哪"的读数。

边界（必须声明，不得含糊）
--------------------------
- 只抓**指数级别**快照（回答"大盘现在什么位置"），**不做**基金盘中估值 ——
  那需要穿透持仓、且各家平台口径不一，成本高、可信度低。
- `f124` 是**行情时间戳**：据此标注"截至 HH:MM"，而不是假装是"现在"。
- A 股交易时段之外的快照是**最近收盘**，必须标明，不得当实时读。
- 出网失败 → **显式降级**（`available=False` + `reason`），不给假数据（铁律 5）。
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")     # 行情时间戳一律按北京时区解释（本项目踩过时区坑）

# 东财 secid：`<市场>.<代码>`，1=沪市 0=深市
SECIDS = {
    "000300": "1.000300",   # 沪深300
    "000905": "1.000905",   # 中证500
    "000016": "1.000016",   # 上证50
    "399006": "0.399006",   # 创业板指
}
NAMES = {"000300": "沪深300", "000905": "中证500", "000016": "上证50", "399006": "创业板指"}

PUSH2 = "https://push2.eastmoney.com/api/qt/ulist.np/get"


def is_trading_session(now: datetime | None = None) -> bool:
    """A 股交易时段内？（周一~周五 09:30-11:30 / 13:00-15:00，按北京时区）

    注意：不含节假日日历（那需要 `trade_calendar`）。本函数只用于**展示口径**
    （"盘中/非交易时段"），不用于交易决策 —— 交易日判定仍走 `trade_calendar`。
    """
    n = now or datetime.now(TZ)
    if n.tzinfo is None:
        n = n.replace(tzinfo=TZ)
    n = n.astimezone(TZ)
    if n.weekday() >= 5:
        return False
    t = n.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


def parse_ulist(payload: dict) -> list:
    """东财 `ulist.np/get` 响应 → `[{code, name, price, pct, amount, asof}]`（纯函数，可测）。

    与真实字段的对应（`fields=f2,f3,f4,f6,f12,f14,f124`）：
        f2=最新价 f3=涨跌幅% f4=涨跌额 f6=成交额 f12=代码 f14=名称 f124=时间戳(秒)
    缺失/停牌的条目（`f2` 为 `-` 或 None）**跳过**，不填 0 —— 0 会被读成"平盘"。
    """
    out = []
    for d in ((payload or {}).get("data") or {}).get("diff") or []:
        try:
            price = float(d.get("f2"))
        except (TypeError, ValueError):
            continue                                     # 停牌/无报价 → 跳过，不猜
        code = str(d.get("f12") or "")
        ts = d.get("f124")
        asof = None
        try:
            if ts:
                asof = datetime.fromtimestamp(int(ts), TZ).strftime("%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            asof = None
        try:
            pct = float(d.get("f3"))
        except (TypeError, ValueError):
            pct = None
        out.append({"code": code, "name": str(d.get("f14") or NAMES.get(code, code)),
                    "price": price, "pct": pct, "asof": asof})
    return out


def fetch(codes=None, timeout: int = 8) -> dict:
    """抓指数盘中快照。返回 `{available, asof, trading, quotes[], reason}`。

    **失败不抛异常**：返回 `available=False` + `reason`，供上层如实降级展示。
    """
    codes = list(codes or SECIDS.keys())
    secids = ",".join(SECIDS[c] for c in codes if c in SECIDS)
    if not secids:
        return {"available": False, "reason": "无可用指数代码", "quotes": []}
    try:
        import requests
        r = requests.get(PUSH2, params={"secids": secids, "fields": "f2,f3,f4,f6,f12,f14,f124",
                                        "fltt": "2", "invt": "2"},
                         timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://quote.eastmoney.com/"})
        r.raise_for_status()
        quotes = parse_ulist(r.json())
    except Exception as e:                                # noqa: BLE001
        return {"available": False, "reason": "行情抓取失败：%s" % str(e)[:80], "quotes": []}
    if not quotes:
        return {"available": False, "reason": "行情返回为空（接口形状可能变了）", "quotes": []}
    trading = is_trading_session()
    asof = max((q["asof"] for q in quotes if q.get("asof")), default=None)
    return {"available": True, "reason": None, "trading": trading, "asof": asof, "quotes": quotes}


def lag_note(local_latest_date: str, today: str = None) -> str:
    """"本地净值落后多少" 的一句话（阶段 5 要解决的"时滞"要能量化，不能只说"可能偏旧"）。"""
    from datetime import date as _d
    t = today or _d.today().isoformat()
    try:
        lag = (_d.fromisoformat(t) - _d.fromisoformat(str(local_latest_date))).days
    except (TypeError, ValueError):
        return "本地净值日期不可解析：%s" % local_latest_date
    if lag <= 0:
        return "本地净值已含今日（无时滞）"
    return "本地净值落后 %d 天（截至 %s）—— 盘中读数见上方指数快照" % (lag, local_latest_date)

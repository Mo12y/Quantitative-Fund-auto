"""Flask 仪表盘"""

import sys, os, json, threading, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Web 仪表盘固定离线(读本地 DB 快照)计算温度等，避免首屏每次都联网 akshare。
# 联网刷新请用 CLI：python src/main.py temp（此时不设此变量，默认 live）。
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from flask import Flask, jsonify, render_template, request
from flask.json.provider import DefaultJSONProvider
from src.data.database import Database
from src.analysis.fund_scorer import FundScreener
from src.analysis.thermometer import MarketThermometer
from src.analysis.portfolio import PortfolioTracker
from src.analysis.dca import DcaManager
from src.analysis.sentiment_monitor import SentimentMonitor
from src.analysis.rebalance_advisor import RebalanceAdvisor
from src.analysis.sector_analyzer import SectorAnalyzer
from src.analysis.historical_recommender import HistoricalRecommender
from src.analysis.investment_plan import get_plan, get_progress, ensure_seed
from src.analysis import fund_boards

app = Flask(__name__)

# 全局 JSON 安全化：任何 NaN/±Inf 转成 null，
# 防止 Flask 默认 allow_nan=True 吐出浏览器 JSON.parse 无法解析的 NaN/Infinity token。
def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    return obj


class _SafeJSONProvider(DefaultJSONProvider):
    def dumps(self, obj, **kwargs):
        return super().dumps(_sanitize(obj), **kwargs)


app.json = _SafeJSONProvider(app)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "fund_quant.db")
SECTORS_CACHE_FILE = os.path.join(BASE_DIR, "data", "sectors_cache.json")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 板块数据缓存（启动时预计算）
_sectors_cache = None
_sectors_lock = threading.Lock()
_sectors_computing = False        # single-flight：预计算与请求兜底只跑一次联网抓取
_sectors_done = threading.Event()
SECTORS_WARM_RETRY = 8.0          # 板块后台计算中，告诉前端多久后回来轮询

# 聚合仪表盘缓存 —— 首个请求全量计算，之后 TTL 内刷新秒回
_dash_cache = None
_dash_lock = threading.Lock()
_dash_computing = False          # single-flight：并发 cache miss 只算一次
_dash_done = threading.Event()
DASH_CACHE_TTL = 240.0          # 秒
DASH_WAIT = 30.0                # single-flight 等待上限：远小于 TTL，避免等待者白等 4 分钟

# 消息面缓存 / 扫描互斥 —— 联网扫描移到独立 /api/sentiment，不再阻塞整页
_senti_cache = None             # {at, data, status, error}
_senti_running = False
_senti_lock = threading.Lock()
SENTI_CACHE_TTL = 1800.0        # 成功后 30 分钟内不重复联网
SENTI_RESCAN_WAIT = 25.0        # 扫描中状态下，客户端重试间隔（秒）

# 通用“单槽 + 单飞 + TTL”缓存，供 /api/overview /api/funds /api/rebalance 等轻量端点复用
_slot_store = {}                 # key -> {"at": monotonic, "data": dict}
_slot_running = {}               # key -> threading.Event（正在计算）
_slot_lock = threading.Lock()
SLOT_TTL = 240.0                 # 秒
SLOT_WAIT = 30.0                 # single-flight 等待上限（同上）


SNAP_TTL = 6 * 3600.0        # SQLite 快照有效期（秒）；主要靠写操作显式失效


def _snap_read(key: str, ttl: float = SNAP_TTL):
    """读 SQLite 快照（短连接；不存在/过期/出错都返回 None）"""
    try:
        db = get_db()
        try:
            return db.get_analysis_snapshot(key, ttl)
        finally:
            db.close()
    except Exception:
        return None


def _snap_write(key: str, data) -> None:
    """写 SQLite 快照。**错误结果不落快照**，否则一次失败会被缓存 6 小时。"""
    if not data or (isinstance(data, dict) and data.get("error")):
        return
    try:
        db = get_db()
        try:
            db.set_analysis_snapshot(key, data)
        finally:
            db.close()
    except Exception:
        pass


def _cached_get(key: str, compute, ttl: float = SLOT_TTL, fresh: bool = False):
    """返回 (数据 dict, 来源标签)。三层：内存 TTL → SQLite 快照 → 现算(+single-flight)。

    第二层是关键：进程重启后第一次请求就能秒回（内存缓存做不到），
    写操作会同步失效快照（见 _invalidate_caches），所以不会读到旧账。
    fresh=True 时忽略前两层强制重算（用于“重算/刷新”按钮）。
    来源标签必须在 **回填缓存之前** 决定，否则重算完再问“是不是缓存命中”永远会答 cache。
    """
    if not fresh:
        with _slot_lock:
            c = _slot_store.get(key)
            if c and time.monotonic() - c["at"] < ttl:
                return c["data"], "cache"
        snap = _snap_read(key)
        if snap is not None:
            with _slot_lock:
                _slot_store[key] = {"at": time.monotonic(), "data": snap}
            return snap, "snapshot"

    with _slot_lock:
        mine = key not in _slot_running
        if mine:
            _slot_running[key] = threading.Event()   # unset => 正在计算
    if not mine:
        _slot_running[key].wait(timeout=min(ttl, SLOT_WAIT))
        with _slot_lock:
            if not fresh and key in _slot_store:
                return _slot_store[key]["data"], "cache"
        # 等待超时仍无结果 -> 自己接管计算
        with _slot_lock:
            _slot_running[key] = threading.Event()
    try:
        data = compute()
    except Exception:
        data = {}
    with _slot_lock:
        _slot_store[key] = {"at": time.monotonic(), "data": data}
        ev = _slot_running.pop(key, None)
        if ev:
            ev.set()
    _snap_write(key, data)          # 回填快照，供下次冷启动/重启
    return data, "live"


def _invalidate_caches(*slot_keys):
    """写操作成功后失效服务端缓存，避免下一次读仍返回写入前的旧账。

    按依赖失效，不做无差别清空：
    - 始终清聚合缓存 `_dash_cache` 与 `overview`（两者都含持仓/计划）；
    - 持仓/定投/计划变更 → 额外清 `rebalance`（它依赖持仓）；
    - 净值变更（nav/update）→ 额外清 `funds` 与 `funds_board_*`（它们依赖 fund_nav）。
    清空全部 slot 会让"记录一笔交易后就打开筛选池"退化成 5~8s 冷算，因此只清受影响的键。
    内存与 SQLite 快照必须一起清，否则端点会从快照把旧账读回来。
    """
    global _dash_cache
    with _dash_lock:
        _dash_cache = None
    board = any(str(k).startswith("funds_board_") for k in slot_keys)
    with _slot_lock:
        for k in ("overview",) + tuple(slot_keys):
            _slot_store.pop(k, None)
        if board:
            for k in [k for k in _slot_store if str(k).startswith("funds_board_")]:
                _slot_store.pop(k, None)
    try:
        db = get_db()
        try:
            db.clear_analysis_snapshots(keys=list(("overview", "__dash__") + tuple(slot_keys)))
            if board:
                db.clear_analysis_snapshots(prefix="funds_board_")
        finally:
            db.close()
    except Exception:
        pass



def get_db():
    """创建数据库连接（每个请求独立连接）"""
    return Database(DB_PATH)


@app.route("/")
def index():
    """仪表盘首页（渲染前端模板）。静态资源带 mtime 版本号，避免浏览器用旧缓存。"""
    try:
        v = int(max(os.path.getmtime(os.path.join(STATIC_DIR, f))
                    for f in ("app.css", "app.js")))
    except Exception:
        v = 0
    return render_template("dashboard.html", v=v)


def _all_plan():
    """投资计划进度（本地、快；计划可维护，读库并首次 seed）"""
    try:
        db = get_db()
        ensure_seed(db)
        plan, prog = get_plan(db), get_progress(db)
        db.close()
        return {
            "id": plan.get("id"),
            "name": plan["name"], "start_date": plan["start_date"],
            "total_capital": plan["total_capital"], "cash_reserve": plan["cash_reserve"],
            "goal": plan.get("goal"), "horizon": plan.get("horizon"),
            "risk_pref": plan.get("risk_pref"), "notes": plan.get("notes"),
            "total_invested": prog["total_invested"], "funds": prog["funds"],
        }
    except Exception as e:
        return {"error": str(e), "funds": []}


def _all_temp():
    """市场温度（本地，较慢，~9s）"""
    try:
        db = get_db()
        t = MarketThermometer(db).get_temperature()
        db.close()
        return t
    except Exception as e:
        return {"error": str(e)}


def _all_funds():
    """质量筛选池（本地，较慢）"""
    try:
        db = get_db()
        s = FundScreener(db)
        df = s.screen_funds(max_results=40)
        funds = []
        for _, row in df.iterrows():
            m = row.get("metrics", {})
            funds.append({
                "code": row["fund_code"],
                "name": row.get("fund_name", "") or "",
                "type": row.get("fund_type", ""),
                "fee": row.get("mgt_fee", 0) or 0,
                "risk": row["risk_label"],
                "momentum_3m": m.get("momentum_3m"),
                "max_dd_1y": m.get("max_drawdown_1y"),
                "sharpe": m.get("sharpe"),
                "ann_vol": m.get("ann_vol"),
                "reasons": row.get("risk_reasons", []),
                "nav_trend": _get_nav_trend(db, row["fund_code"]),
            })
        summary = s.get_pool_summary(df)
        db.close()
        return {"funds": funds, "summary": summary}
    except Exception as e:
        return {"error": str(e), "funds": [], "summary": {}}


def _holdings_payload(data: dict) -> list:
    """持仓明细 → 前端结构（含 T+1 状态与曲线）"""
    out = []
    for h in data.get("holdings_detail", []):
        out.append({
            "id": h.get("holding_id"), "code": h.get("fund_code"),
            "name": h.get("fund_name"), "buy_date": h.get("buy_date"),
            "buy_amount": h.get("buy_amount"), "current_value": h.get("current_value"),
            "shares": h.get("shares"),
            "pnl": h.get("pnl"), "pnl_pct": h.get("pnl_pct"),
            "days_held": h.get("days_held"),
            "status": h.get("status", "holding"),
            "is_pending": h.get("is_pending", False),
            "apply_date": h.get("apply_date"),
            "confirm_date": h.get("confirm_date"),
            "accrual_start": h.get("accrual_start"),
            "confirm_nav": h.get("confirm_nav"),
            "effective_date": h.get("effective_date"),
            "nav_missing": h.get("nav_missing", False),
            "current_nav": h.get("current_nav"),
            "nav_latest_date": h.get("nav_latest_date"),
            "replay_pct": h.get("replay_pct"),
            "pending_est_pct": h.get("pending_est_pct"),
            "curve": h.get("curve", []),
        })
    return out


def _portfolio_payload(data: dict) -> dict:
    r = data.get("realized") or {}
    return {
        "has_holdings": data.get("has_holdings", False),
        "total_invested": data.get("total_invested", 0),
        "total_market_value": data.get("total_market_value", 0),
        "total_pnl": data.get("total_pnl", 0),
        "total_return_pct": data.get("total_return_pct", 0),
        "realized": {
            "total_pnl": r.get("total_pnl", 0), "total_gross": r.get("total_gross", 0),
            "total_cost": r.get("total_cost", 0), "total_fee": r.get("total_fee", 0),
            "count": r.get("count", 0),
            "sales": [{
                "fund_code": s.get("fund_code"), "fund_name": s.get("fund_name"),
                "confirm_date": s.get("confirm_date"), "shares": s.get("shares"),
                "nav": s.get("nav"), "gross": s.get("gross"), "cost": s.get("cost"),
                "fee": s.get("fee"), "pnl": s.get("pnl"),
            } for s in (r.get("sales") or [])[:50]],
        },
        "alloc": data.get("asset_allocation", {}),
        "holdings": _holdings_payload(data),
    }


def _all_portfolio():
    """持仓（本地）"""
    try:
        db = get_db()
        data = PortfolioTracker(db).get_portfolio_summary()
        db.close()
        return _portfolio_payload(data)
    except Exception as e:
        return {"error": str(e), "has_holdings": False, "holdings": []}


def _all_rebalance():
    """调仓建议（本地，最慢，~17s，含温度/筛选重算）"""
    try:
        db = get_db()
        holdings = db.get_current_holdings()
        if not holdings:
            db.close()
            return {"need_rebalance": False, "instructions": [], "summary": {"verdict": "暂无持仓"}}
        advisor = RebalanceAdvisor(db)
        # 总资金口径 = 持仓市值 + 计划现金弹药（cash_reserve）。
        # 旧版用 total_invested * 1.1 拍脑袋估算，与计划卡数字互相打架。
        try:
            ensure_seed(db)
            plan = get_plan(db) or {}
            cash_reserve = float(plan.get("cash_reserve") or 0)
        except Exception:
            cash_reserve = 0.0
        rb = advisor.analyze(cash_reserve=cash_reserve)
        db.close()
        return {
            "need_rebalance": rb["need_rebalance"],
            "current_equity_pct": rb["current_equity_pct"],
            "target_equity_pct": rb["target_equity_pct"],
            "gap_pct": rb["gap_pct"],
            "summary": rb["summary"],
            "instructions": rb["instructions"],
        }
    except Exception as e:
        return {"error": str(e), "instructions": [], "summary": {"verdict": "分析失败"}}


def _compute_dashboard():
    """并行计算本地慢分析 + 快速投资计划。不含任何联网调用。"""
    workers = {"temp": _all_temp, "funds": _all_funds,
               "portfolio": _all_portfolio, "rebalance": _all_rebalance}
    out = {"plan": _all_plan()}
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut = {ex.submit(fn): key for key, fn in workers.items()}
        for f in fut:
            out[fut[f]] = f.result()
    out["stats"] = _overview_stats(out.get("portfolio") or {}, out.get("plan") or {})
    out["curve"] = _portfolio_curve()
    return out


@app.route("/api/all")
def api_all():
    """聚合 API：温度/基金池/持仓/调仓建议（本地并行 + TTL 缓存，秒级刷新）。
    消息面已独立到 /api/sentiment，避免联网阻塞整页。"""
    global _dash_cache, _dash_computing
    fresh = request.args.get("fresh") == "1"
    # 1) 新鲜缓存 -> 直接返回（fresh=1 时跳过，强制重算）
    if not fresh:
        with _dash_lock:
            if _dash_cache and time.monotonic() - _dash_cache["at"] < DASH_CACHE_TTL:
                return jsonify({"ok": True, "data": _dash_cache["data"], "source": "cache"})
        # 1.5) SQLite 快照：进程重启后第一次请求也能秒回
        snap = _snap_read("__dash__")
        if snap is not None:
            with _dash_lock:
                _dash_cache = {"at": time.monotonic(), "data": snap}
            return jsonify({"ok": True, "data": snap, "source": "snapshot"})

    # 2) single-flight：并发 cache miss 时只有第一个真算，其余等它算完复用
    with _dash_lock:
        if not _dash_computing:
            _dash_computing = True
            _dash_done.clear()
            mine = True
        else:
            mine = False
    if not mine:
        _dash_done.wait(timeout=DASH_WAIT)
        with _dash_lock:
            if _dash_cache and not fresh:
                return jsonify({"ok": True, "data": _dash_cache["data"], "source": "cache"})
        # 等待超时仍未就绪 -> 自己接管计算
        with _dash_lock:
            _dash_computing = True
            _dash_done.clear()
            mine = True

    try:
        data = _compute_dashboard()
    except Exception as e:
        # 顶层带 error：既告知前端，也让 _snap_write 拒绝把这个失败结果落成快照
        data = {"error": f"compute failed: {e}", "plan": [], "temp": {"error": "compute failed"},
                "funds": {"funds": [], "summary": {}},
                "portfolio": {"has_holdings": False, "holdings": []},
                "rebalance": {"instructions": [], "summary": {"verdict": "分析失败"}}}
    with _dash_lock:
        _dash_cache = {"at": time.monotonic(), "data": data}
        _dash_computing = False
        _dash_done.set()
    _snap_write("__dash__", data)
    return jsonify({"ok": True, "data": data, "source": "live"})


def _overview_compute() -> dict:
    """轻量总览：投资计划 + 温度 + 持仓（并行，约 2~3s），不含筛选/调仓等重计算。"""
    out = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        fs = {ex.submit(_all_plan): "plan",
              ex.submit(_all_temp): "temp",
              ex.submit(_all_portfolio): "portfolio"}
        for f in fs:
            out[fs[f]] = f.result()
    out["stats"] = _overview_stats(out.get("portfolio") or {}, out.get("plan") or {})
    out["curve"] = _portfolio_curve()
    return out


def _portfolio_curve() -> dict:
    """组合累计涨跌曲线（独立连接，供端点与总览复用）"""
    try:
        db = get_db()
        data = PortfolioTracker(db).get_portfolio_curve()
        db.close()
        return data
    except Exception as e:
        return {"error": str(e), "dates": [], "value": [], "cost": [], "pnl": [], "return_pct": []}


@app.route("/api/portfolio/curve")
def api_portfolio_curve():
    """组合整体累计收益/资产净值曲线（第 13 项）"""
    return jsonify({"ok": True, "data": _portfolio_curve()})


def _overview_stats(port: dict, plan: dict) -> dict:
    """总览增强指标：总资产 / 累计收益 / 收益率 / 持仓分布 / 行业占比"""
    mv = float(port.get("total_market_value") or 0)
    invested = float(port.get("total_invested") or 0)
    pnl = float(port.get("total_pnl") or 0)
    cash = float(plan.get("cash_reserve") or 0)
    holdings = port.get("holdings") or []
    realized = port.get("realized") or {}
    return {
        "total_assets": round(mv + cash, 2),          # 总资产 = 持仓市值 + 现金弹药
        "total_market_value": round(mv, 2),
        "total_invested": round(invested, 2),
        "total_pnl": round(pnl, 2),
        "realized_pnl": round(float(realized.get("total_pnl") or 0), 2),
        "realized_fee": round(float(realized.get("total_fee") or 0), 2),
        "realized_count": int(realized.get("count") or 0),
        "total_return_pct": round((pnl / invested * 100) if invested else 0, 2),
        "cash_reserve": round(cash, 2),
        "holding_count": len([h for h in holdings if h.get("status") != "sold"]),
        "pending_count": len([h for h in holdings if h.get("status") == "pending_confirm"]),
        "type_alloc": port.get("alloc") or {},
        "board_alloc": fund_boards.board_allocation(holdings),
    }


@app.route("/api/overview")
def api_overview():
    """轻量总览 —— 首屏快速渲染用；避免让用户等筛选池/调仓的重计算。"""
    data, source = _cached_get("overview", _overview_compute, fresh=request.args.get("fresh") == "1")
    return jsonify({"ok": True, "data": data, "source": source})


@app.route("/api/funds")
def api_funds():
    """基金筛选池 —— 重计算(~6s)，仅在用户打开筛选面板时触发并缓存。"""
    data, source = _cached_get("funds", _all_funds, fresh=request.args.get("fresh") == "1")
    return jsonify({"ok": True, "data": data, "source": source})


@app.route("/api/rebalance")
def api_rebalance():
    """调仓建议 —— 重计算，仅在用户打开持仓/调仓面板时触发并缓存。"""
    data, source = _cached_get("rebalance", _all_rebalance, fresh=request.args.get("fresh") == "1")
    return jsonify({"ok": True, "data": data, "source": source})


def _serialize_senti(sd: dict) -> dict:
    return {
        "all_clear": sd.get("all_clear", True),
        "signal_summary": sd.get("signal_summary", ""),
        "alerts": [{
            "level": a.level, "category": a.category,
            "title": a.title, "detail": a.detail, "timestamp": a.timestamp,
        } for a in sd.get("alerts", [])],
    }


@app.route("/api/sentiment")
def api_sentiment():
    """消息面 —— 独立端点，联网扫描放后台线程；首次返回 scanning，前台轮询。
    不阻塞仪表盘其它板块的加载。"""
    global _senti_cache, _senti_running
    with _senti_lock:
        c = _senti_cache
        running = _senti_running
    now = time.monotonic()
    # 命中成功的缓存 -> 直接返回
    if c and c["status"] == "ok" and now - c["at"] < SENTI_CACHE_TTL:
        return jsonify({"ok": True, "data": c["data"], "status": "ok", "cached": True})
    # 上次扫描失败（3 分钟内）-> 直接报错，避免前台轮询反复触发联网扫描
    if c and c["status"] == "error" and now - c["at"] < 180.0:
        return jsonify({"ok": False, "error": c.get("error") or "消息面扫描失败，请稍后重试"})
    # 正在后台扫描 -> 提示客户端稍后再轮询
    if running:
        prev = c["data"] if c and c["status"] == "ok" else None
        return jsonify({"ok": True, "data": prev, "status": "scanning"})

    # 启动后台扫描
    try:
        db = get_db()
        codes = [h["fund_code"] for h in db.get_current_holdings()]
        db.close()
    except Exception:
        codes = []
    with _senti_lock:
        if _senti_running:
            return jsonify({"ok": True, "data": None, "status": "scanning"})
        _senti_running = True

    def _background_scan():
        global _senti_cache, _senti_running
        try:
            sd = SentimentMonitor().full_scan(holding_codes=codes)
            data, status, err = _serialize_senti(sd), "ok", None
        except Exception as e:
            data, status, err = None, "error", str(e)
        with _senti_lock:
            _senti_cache = {"at": time.monotonic(), "data": data, "status": status, "error": err}
            _senti_running = False

    threading.Thread(target=_background_scan, daemon=True).start()
    return jsonify({"ok": True, "data": None, "status": "scanning", "retry_in": SENTI_RESCAN_WAIT})


def _get_nav_trend(db: Database, fund_code: str, days: int = 30) -> list:
    """获取最近N天的净值序列，用于前端迷你趋势图（SQL LIMIT，不再全量加载整段历史）"""
    try:
        navs = db.get_recent_fund_nav(fund_code, days)
    except Exception:
        return []
    if len(navs) < 5:
        return []
    out = []
    for r in navs:
        raw = r.get("unit_nav")
        if raw is None:          # unit_nav 允许 NULL，跳过避免 TypeError 拖垮整个基金区
            continue
        out.append({"date": str(r.get("nav_date", ""))[-5:],
                    "nav": round(float(raw), 4)})
    return out


# =================================================================
# 板块数据缓存
# =================================================================

def _format_sectors(result: dict) -> dict:
    """将 SectorAnalyzer 结果格式化为前端需要的精简结构"""
    sectors = []
    for s in result.get("all_sectors", []):
        sectors.append({
            "name": s["name"],
            "rank": s.get("rank", 0),
            "score": round(s.get("score", 0), 2),
            "ret_1m": s["ret_1m"],
            "ret_3m": s["ret_3m"],
            "ret_6m": s["ret_6m"],
            "volatility": s["volatility"],
            "max_dd_6m": s["max_dd_6m"],
            "ma_ratio": s["ma_ratio"],
        })

    return {
        "sectors": sectors,
        "momentum_leaders": [
            {"name": s["name"], "ret_1m": s["ret_1m"], "ret_3m": s["ret_3m"]}
            for s in result.get("momentum_leaders", [])[:5]
        ],
        "value_candidates": [
            {"name": s["name"], "ret_1m": s["ret_1m"], "ret_6m": s["ret_6m"], "max_dd": s["max_dd_6m"]}
            for s in result.get("value_candidates", [])[:5]
        ],
        "cached_at": time.strftime("%Y-%m-%d %H:%M"),
    }


def _save_sectors_cache(data: dict):
    """将板块数据写入文件缓存"""
    try:
        os.makedirs(os.path.dirname(SECTORS_CACHE_FILE), exist_ok=True)
        with open(SECTORS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
    except Exception:
        pass


def _start_sectors_warm():
    """single-flight 启动后台板块计算。已在算则不作声。"""
    global _sectors_computing
    with _sectors_lock:
        if _sectors_computing:
            return False
        _sectors_computing = True
        _sectors_done.clear()
    threading.Thread(target=_compute_sectors_bg, daemon=True).start()
    return True


def _compute_sectors_bg():
    """后台抓取并计算 31 行业板块 → 内存 + 文件 + SQLite 快照。"""
    global _sectors_cache, _sectors_computing
    try:
        result = SectorAnalyzer().analyze()
        if "error" not in result:
            data = _format_sectors(result)
            with _sectors_lock:
                _sectors_cache = data
            _save_sectors_cache(data)
            _snap_write("sectors", data)      # 重启后也能秒回
            print(f"  ✅ 板块数据已就绪 ({len(data['sectors'])}个行业)")
        else:
            print(f"  ⚠️ 板块计算失败: {result['error']}")
    except Exception as e:
        print(f"  ⚠️ 板块计算异常: {e}")
    finally:
        with _sectors_lock:
            _sectors_computing = False
        _sectors_done.set()


def _precompute_sectors():
    """启动时后台预热板块数据（已新鲜则跳过一次抓取）"""
    _start_sectors_warm()


@app.route("/api/sectors")
def api_sectors():
    """板块分析：内存 → 文件 → SQLite 快照 → **后台计算（不阻塞请求）**。

    联网抓 31 个行业可能耗时几十秒。原实现会在请求线程里等它算完（实测断网时
    卡 50s+ 都出不来），这里改成：冷启动只**启动后台任务**并立刻返回
    `status=warming`，前端按 retry_in 轮询 —— 与 /api/sentiment 同一套模式。
    """
    global _sectors_cache

    # 1) 内存缓存（锁内取引用，序列化在锁外）
    with _sectors_lock:
        cached = _sectors_cache
    if cached is not None:
        return jsonify({"ok": True, "data": cached, "source": "memory"})

    # 2) 文件缓存
    if os.path.exists(SECTORS_CACHE_FILE):
        try:
            with open(SECTORS_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            with _sectors_lock:
                if _sectors_cache is None:
                    _sectors_cache = cached
            return jsonify({"ok": True, "data": cached, "source": "file"})
        except Exception:
            pass

    # 3) SQLite 快照（跨重启）
    snap = _snap_read("sectors")
    if snap is not None:
        with _sectors_lock:
            if _sectors_cache is None:
                _sectors_cache = snap
        return jsonify({"ok": True, "data": snap, "source": "snapshot"})

    # 4) 都没有 → 后台算，立刻返回，让前端轮询（绝不阻塞请求线程）
    _start_sectors_warm()
    return jsonify({"ok": True, "data": None, "status": "warming",
                    "retry_in": SECTORS_WARM_RETRY})


@app.route("/api/recommend")
def api_recommend():
    """历史验证推荐 — 独立端点, 加载约15-20秒"""
    try:
        db = get_db()
        hr = HistoricalRecommender(db)
        result = hr.recommend(lookback_years=1.5)
        db.close()

        # 精简输出
        return jsonify({"ok": True, "data": {
            "stats": result.get("stats", {}),
            "proven_winners": result.get("proven_winners", [])[:15],
            "current_picks": result.get("current_picks", [])[:10],
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _clean_recs(df, keep=None, scale_pct=()):
    """CSV 行 -> 前端安全记录列表：
    - 只保留指定列（缺失列自动跳过）
    - 指定的百分比列 ×100（呈现更直观）
    - NaN / inf 统一转 None，避免 jsonify 输出浏览器无法解析的 NaN token
    """
    cols = [c for c in (keep or list(df.columns)) if c in df.columns]
    sub = df[cols].copy()
    for c in scale_pct:
        if c in sub.columns:
            sub[c] = sub[c] * 100
    sub = sub.round(4)
    # NaN / ±inf -> None（避免 jsonify 输出浏览器无法解析的 NaN / Infinity token）
    sub = sub.replace([float("inf"), float("-inf")], float("nan"))
    sub = sub.astype(object).where(sub.notna(), None)
    return sub.to_dict("records")


@app.route("/api/quant_models")
def api_quant_models():
    """量化模型结果 — vol 预测 / 回撤预警 / 组合模拟（读取离线分析 CSV，秒级响应）"""
    import pandas as pd
    BASE = os.path.join(BASE_DIR, "data")
    out = {"vol": [], "drawdown": [], "portfolio": [], "signals": [], "reports": {}}

    # vol 多模型对比（保留原始量纲）
    try:
        df = pd.read_csv(os.path.join(BASE, "vol_results", "vol_model_comparison.csv"))
        cols = ["model", "ic_mean", "icir", "qlike", "mz_beta",
                "dm_p_vs_base", "perm_p_value", "perm_delta"]
        out["vol"] = _clean_recs(df, cols)
    except Exception:
        pass

    # 回撤预警（召回/精确/F1/漏报为百分制）
    try:
        df = pd.read_csv(os.path.join(BASE, "drawdown_results", "drawdown_main_results.csv"))
        cols = ["threshold", "model", "auc", "recall_pos", "precision_pos",
                "f1", "brier", "miss_rate"]
        out["drawdown"] = _clean_recs(df, cols, scale_pct=("recall_pos", "precision_pos", "f1", "miss_rate"))
    except Exception:
        pass

    # 组合模拟指标（收益/波动/回撤/仓位/换手为百分制）
    try:
        df = pd.read_csv(os.path.join(BASE, "portfolio_results", "portfolio_metrics.csv"))
        pct = ["total_return", "annual_return", "annual_volatility", "max_drawdown",
               "avg_position", "avg_turnover"]
        out["portfolio"] = _clean_recs(df, scale_pct=pct)
    except Exception:
        pass

    # 月度信号（OOS 全序列，用于前端折线图；pos 与 vol 为百分制）
    try:
        df = pd.read_csv(os.path.join(BASE, "portfolio_results", "monthly_signals.csv"))
        df["month"] = df["month"].astype(str).str[:7]
        cols = ["month", "pred_vol", "dd_warning_frac", "vol_target_pos", "combined_pos", "dd_triggered"]
        out["signals"] = _clean_recs(df, cols,
                                     scale_pct=("pred_vol", "dd_warning_frac", "vol_target_pos", "combined_pos"))
    except Exception:
        pass

    # 报告存在性
    for name in ["vol_model_comparison_report.md", "drawdown_warning_report.md",
                 "portfolio_simulation_report.md", "vol_final_review.md",
                 "drawdown_final_review.md", "portfolio_final_review.md"]:
        p = os.path.join(BASE_DIR, "docs", name)
        out["reports"][name] = os.path.exists(p)

    return jsonify({"ok": True, "data": out})


# =================================================================
# Web 交互操作：持仓加仓/减仓/改/删 + 定投管理
# （与 CLI 走同一套 PortfolioTracker / DcaManager / Database 逻辑）
# =================================================================

def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def _pf(db: Database) -> PortfolioTracker:
    return PortfolioTracker(db)


def _api_req() -> dict:
    return request.get_json(silent=True) or {}


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else {}


@app.route("/api/holdings", methods=["POST"])
def api_holdings_action():
    """持仓写操作：action = buy|sell|update|delete。
    body: {action, code?, name?, date?, amount?, id?, notes?}"""
    q = _api_req()
    action = (q.get("action") or "").strip().lower()
    db = get_db()
    try:
        if action == "buy":
            code = str(q.get("code") or "").strip()
            amount = float(q.get("amount") or 0)
            if not code:
                return jsonify({"ok": False, "error": "缺少基金代码"})
            if amount <= 0:
                return jsonify({"ok": False, "error": "买入金额需大于 0"})
            date_ = (str(q.get("date") or "").strip()) or _today()
            name = (str(q.get("name") or "").strip()) or (db.get_fund_name(code) or code)
            notes = (str(q.get("notes") or "").strip()) or ""
            after = bool(q.get("after_cutoff"))
            _pf(db).add_buy_transaction(code, name, date_, amount, notes, after_cutoff=after)
            return jsonify({"ok": True, "message": f"已记录买入：{date_} {name}({code}) ¥{amount:,.2f}"
                                                   + ("（15:00 后提交，按下一交易日确认）" if after else "")})

        if action == "sell":
            hid = int(q.get("id") or 0)
            amount = float(q.get("amount") or 0)
            if not hid:
                return jsonify({"ok": False, "error": "缺少持仓ID"})
            if amount <= 0:
                return jsonify({"ok": False, "error": "卖出金额需大于 0"})
            date_ = (str(q.get("date") or "").strip()) or _today()
            _pf(db).record_sell(hid, date_, amount)
            return jsonify({"ok": True, "message": f"已记录卖出：持仓ID={hid} {date_} ¥{amount:,.0f}"})

        if action == "update":
            hid = int(q.get("id") or 0)
            fields = {}
            raw_amt = q.get("amount")
            if raw_amt not in (None, ""):
                amt = float(raw_amt)
                if amt <= 0:
                    return jsonify({"ok": False, "error": "金额需大于 0"})
                fields["buy_amount"] = amt
            raw_date = q.get("date")
            if raw_date:
                fields["buy_date"] = str(raw_date).strip()
            if not fields:
                return jsonify({"ok": False, "error": "未提供要修改的字段"})
            if "buy_amount" in fields:
                row = db.conn.cursor().execute("SELECT buy_nav FROM holdings WHERE id=?", (hid,)).fetchone()
                if not row:
                    return jsonify({"ok": False, "error": f"未找到持仓ID={hid}"})
                bnav = row["buy_nav"]
                if bnav and bnav > 0:
                    fields["shares"] = round(fields["buy_amount"] / bnav, 2)
            ok = db.update_holding(hid, **fields)
            return jsonify({"ok": ok, "message": f"已更新持仓ID={hid}" if ok else f"未找到持仓ID={hid}"})

        if action == "delete":
            hid = int(q.get("id") or 0)
            ok = db.delete_holding(hid)
            return jsonify({"ok": ok, "message": f"已删除持仓ID={hid}" if ok else f"未找到持仓ID={hid}"})

        return jsonify({"ok": False, "error": f"未知操作: {action}"})
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "参数格式错误（金额/ID 需为数字）"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"操作失败: {e}"})
    finally:
        db.close()
        _invalidate_caches("rebalance")     # 持仓变了 → 聚合缓存/总览/调仓建议都失效


def _dca_serialize(plans, db=None) -> list:
    from datetime import date
    from src.analysis import trade_rules
    today = date.today().isoformat()
    out = []
    for p in plans:
        due = p.get("next_run_date") or p.get("start_date")
        row = {
            "id": p["id"], "fund_code": p["fund_code"], "fund_name": p["fund_name"],
            "amount_per_period": p["amount_per_period"], "frequency": p["frequency"],
            "start_date": p["start_date"], "next_run_date": due,
            "total_periods": p.get("total_periods", 0), "total_amount": p.get("total_amount", 0),
            "status": p.get("status", "active"),
            "due": bool(due) and str(due) <= today,
            "last_synced_at": p.get("last_synced_at"),
        }
        # 期次对账（第 7 项）：应投 / 已投 / 待补录
        if db is not None:
            try:
                rows = db.get_dca_periods(p["id"])
                if rows:
                    exp = len(rows)
                    done = len([r for r in rows if r["status"] == "executed"])
                    # 已标已执行但没挂买入凭证的历史期次：只提示、不静默翻回 pending
                    unlinked = len([r for r in rows if r["status"] == "executed"
                                    and r["holding_id"] is None])
                    pend = len([r for r in rows if r["status"] == "pending"
                                and str(r["planned_date"]) <= today])
                else:
                    exp = len(trade_rules.period_dates(p["start_date"], p.get("frequency", "weekly"),
                                                       today, db.get_trade_date_set()))
                    done = int(p.get("total_periods") or 0)
                    unlinked = 0
                    pend = max(0, exp - done)
                row.update({"expected_periods": exp, "executed_periods": done,
                            "unlinked_periods": unlinked, "pending_periods": pend})
            except Exception:
                pass
        out.append(row)
    return out


@app.route("/api/reconcile", methods=["POST"])
def api_reconcile():
    """幂等对账：结算到期的待确认买入/卖出（显式触发，不再挂在 GET 上）。

    返回 settled 数量；只有真的改了持仓才失效缓存，避免无谓的重算。
    """
    db = get_db()
    try:
        r = PortfolioTracker(db).reconcile()
        changed = bool(r.get("settled_buys") or r.get("settled_sells"))
        if changed:
            _invalidate_caches("rebalance")
        return jsonify({"ok": True, "data": r, "changed": changed})
    except Exception as e:
        return jsonify({"ok": False, "error": f"对账失败: {e}"})
    finally:
        db.close()


@app.route("/api/holdings/refresh", methods=["POST"])
def api_holdings_refresh():
    """逐日重放刷新：对账到期确认 + 按生效日净值→最新净值 重算持仓与曲线。
    返回 portfolio + 净值时效信息（供前端提示“更新净值”）。"""
    db = get_db()
    try:
        t = PortfolioTracker(db)
        try:
            t.reconcile()
        except Exception:
            pass
        data = t.get_portfolio_summary()
        latest = db.get_latest_nav_date()
        today = _today()
        stale = bool(latest) and str(latest) < today
        return jsonify({"ok": True, "data": {
            "portfolio": _portfolio_payload(data),
            "nav_latest_date": latest,
            "today": today,
            "stale": stale,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": f"刷新失败: {e}"})
    finally:
        db.close()
        _invalidate_caches("rebalance")     # reconcile 可能已改持仓（T+1 到日结算）


@app.route("/api/nav/update", methods=["POST"])
def api_nav_update():
    """更新净值：只拉取当前持仓涉及的基金（数量少，较快）。
    body: {codes?: [...]}（缺省=当前持仓基金）"""
    from src.data.collector import DataCollector
    q = _api_req()
    db = get_db()
    try:
        codes = q.get("codes")
        if not codes:
            codes = sorted({h["fund_code"] for h in db.get_current_holdings()})
        if not codes:
            return jsonify({"ok": False, "error": "当前没有持仓基金需要更新"})
        col = DataCollector(db)
        ok_n, fail = 0, []
        for code in codes:
            try:
                df = col.collect_fund_nav(code)
                if df is not None and not df.empty:
                    col.save_fund_nav_batch(code, df)
                    ok_n += 1
                else:
                    fail.append(code)
            except Exception:
                fail.append(code)
        latest = db.get_latest_nav_date()
        return jsonify({"ok": ok_n > 0, "message": f"已更新 {ok_n}/{len(codes)} 只基金净值（最新 {latest}）",
                        "failed": fail})
    except Exception as e:
        return jsonify({"ok": False, "error": f"更新净值失败: {e}"})
    finally:
        db.close()
        # 净值变了 → 依赖 fund_nav 的筛选池/板块总榜也失效
        _invalidate_caches("funds", "funds_board_all", "rebalance")


@app.route("/api/plan", methods=["GET", "POST"])
def api_plan():
    """投资计划：GET 读取（计划+进度）；POST 维护
    action = update | add_item | update_item | delete_item | delete_plan"""
    db = get_db()
    try:
        ensure_seed(db)
        if request.method == "GET":
            return jsonify({"ok": True, "data": {"plan": _all_plan()}})

        q = _api_req()
        action = (q.get("action") or "").strip().lower()
        plan = db.get_active_plan()
        if not plan:
            return jsonify({"ok": False, "error": "当前没有生效计划"})
        pid = plan["id"]

        if action == "update":
            fields = {}
            for k in ("name", "goal", "total_capital", "cash_reserve", "start_date",
                      "horizon", "risk_pref", "notes"):
                if q.get(k) not in (None, ""):
                    fields[k] = float(q[k]) if k in ("total_capital", "cash_reserve") else str(q[k]).strip()
            if not fields:
                return jsonify({"ok": False, "error": "未提供要修改的字段"})
            ok = db.update_plan(pid, **fields)
            return jsonify({"ok": ok, "message": "计划已更新" if ok else "更新失败"})

        if action == "add_item":
            code = str(q.get("code") or "").strip()
            if not code:
                return jsonify({"ok": False, "error": "缺少基金代码"})
            name = (str(q.get("name") or "").strip()) or (db.get_fund_name(code) or code)
            db.add_plan_item({
                "plan_id": pid, "fund_code": code, "fund_name": name,
                "role": str(q.get("role") or "").strip() or None,
                "target_amount": float(q["target_amount"]) if q.get("target_amount") else None,
                "target_pct": float(q["target_pct"]) if q.get("target_pct") else None,
                "cadence": str(q.get("cadence") or "").strip() or None,
                "notes": str(q.get("notes") or "").strip() or None,
                "tranches": "[]",
            })
            return jsonify({"ok": True, "message": f"已添加计划基金：{name}({code})"})

        if action == "update_item":
            iid = int(q.get("item_id") or 0)
            fields = {}
            if q.get("target_amount") not in (None, ""):
                fields["target_amount"] = float(q["target_amount"])
            if q.get("target_pct") not in (None, ""):
                fields["target_pct"] = float(q["target_pct"])
            for k in ("role", "cadence", "notes", "fund_name"):
                if q.get(k) not in (None, ""):
                    fields[k] = str(q[k]).strip()
            if not fields:
                return jsonify({"ok": False, "error": "未提供要修改的字段"})
            ok = db.update_plan_item(iid, **fields)
            return jsonify({"ok": ok, "message": "计划条目已更新" if ok else "未找到该条目"})

        if action == "delete_item":
            iid = int(q.get("item_id") or 0)
            ok = db.delete_plan_item(iid)
            return jsonify({"ok": ok, "message": "已删除计划条目" if ok else "未找到该条目"})

        if action == "delete_plan":
            ok = db.delete_plan(pid)
            return jsonify({"ok": ok, "message": "计划已删除" if ok else "删除失败"})

        return jsonify({"ok": False, "error": f"未知操作: {action}"})
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "参数格式错误（金额需为数字）"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"操作失败: {e}"})
    finally:
        db.close()
        if request.method == "POST":
            _invalidate_caches("rebalance")     # 计划变了 → 总览/调仓建议失效（GET 读不失效）


@app.route("/api/funds/board")
def api_funds_board():
    """筛选池总榜（第 8 项）：按行业板块分类，每板块前 N（默认 20），不截断。"""
    size = max(1, min(100, int(request.args.get("size", 20))))
    limit = max(50, min(600, int(request.args.get("limit", 300))))
    key = f"funds_board_{size}_{limit}"
    data, source = _cached_get(key, lambda: _compute_board_pool(size, limit), ttl=600.0)
    return jsonify({"ok": True, "data": data, "source": source})


def _compute_board_pool(size: int, limit: int) -> dict:
    """按板块把筛选池分组（每板块取前 size 只，组内稳健优先→夏普→动量）"""
    try:
        db = get_db()
        s = FundScreener(db)
        df = s.screen_funds(max_results=limit)
        funds = []
        for _, row in df.iterrows():
            m = row.get("metrics", {}) or {}
            funds.append({
                "code": row["fund_code"], "name": row.get("fund_name", "") or "",
                "type": row.get("fund_type", ""), "risk": row["risk_label"],
                "fee": row.get("mgt_fee", 0) or 0,
                "momentum_3m": m.get("momentum_3m"), "max_dd_1y": m.get("max_drawdown_1y"),
                "sharpe": m.get("sharpe"), "ann_vol": m.get("ann_vol"),
                "nav_trend": _get_nav_trend(db, row["fund_code"]),
            })
        db.close()
    except Exception as e:
        return {"boards": [], "total_funds": 0, "size": size, "error": str(e)}

    groups = {}
    for f in funds:
        b = fund_boards.classify(f.get("name") or f.get("code"))
        groups.setdefault(b, []).append(f)
    for g in groups.values():
        g.sort(key=lambda x: (0 if "稳健" in (x.get("risk") or "") else 1,
                              -(x.get("sharpe") or 0), -(x.get("momentum_3m") or 0)))
    boards = [{"board": b, "total": len(g), "funds": g[:size]}
              for b, g in sorted(groups.items(), key=lambda kv: -len(kv[1]))]
    return {"boards": boards, "total_funds": len(funds), "size": size}


@app.route("/api/dca", methods=["GET", "POST"])
def api_dca():
    """定投管理。GET = 列表（含期次对账）；POST action = add|run|sync|backfill|pause|resume|delete。"""
    db = get_db()
    try:
        if request.method == "GET":
            plans = db.get_dca_plans()
            return jsonify({"ok": True, "data": _dca_serialize(plans, db)})

        q = _api_req()
        action = (q.get("action") or "").strip().lower()
        mgr = DcaManager(db)

        if action == "sync":
            r = mgr.sync_all()
            msgs = [f"{a['fund_name']} 自动补录第{a['period']}期 {a['date']} ¥{a['amount']:,.2f}"
                    for a in r.get("auto_executed", [])]
            msg = ("已同步定投期次" + ("；" + "；".join(msgs) if msgs else "（无新到期期次）")
                   + (f"；仍有 {r['pending_total']} 期待补录" if r.get("pending_total") else ""))
            return jsonify({"ok": True, "message": msg, "data": r})

        if action == "backfill":
            pid = int(q.get("id") or 0)
            r = mgr.backfill(pid)
            if r.get("error"):
                return jsonify({"ok": False, "error": r["error"]})
            return jsonify({"ok": True, "message": f"已补录 {r['count']} 期定投", "data": r})

        if action == "add":
            code = str(q.get("code") or "").strip()
            amount = float(q.get("amount") or 0)
            if not code:
                return jsonify({"ok": False, "error": "缺少基金代码"})
            if amount <= 0:
                return jsonify({"ok": False, "error": "每期金额需大于 0"})
            freq = (str(q.get("frequency") or "").strip() or "weekly").lower()
            if freq not in ("daily", "weekly", "biweekly", "monthly"):
                freq = "weekly"
            start = (str(q.get("date") or "").strip()) or _today()
            name = (str(q.get("name") or "").strip()) or (db.get_fund_name(code) or code)
            db.add_dca_plan({
                "fund_code": code, "fund_name": name,
                "amount_per_period": amount, "frequency": freq,
                "start_date": start,
                "next_run_date": DcaManager.next_run_date(freq, start),
            })
            # 建好期次表（应投/待补录立即可见）
            try:
                newp = max(db.get_dca_plans(), key=lambda x: x["id"])
                mgr.sync_plan(newp)
            except Exception:
                pass
            return jsonify({"ok": True, "message": f"已添加定投：{name}({code}) 每期¥{amount:,.2f}/{freq}"})

        if action == "run":
            plan_id = q.get("id")
            plans = mgr.get_status()
            targets = [p for p in plans if p["due"]]
            if plan_id not in (None, "", "all"):
                targets = [p for p in targets if str(p["id"]) == str(plan_id)]
            if not targets:
                return jsonify({"ok": False, "message": "当前没有到期的定投计划"})
            results = []
            for p in targets:
                r = mgr.execute_installment(p["id"])
                if r.get("ok"):
                    results.append(f"{p['fund_name']} 第{r['period']}期 ¥{r['amount']:,.0f}（下期 {r['next_run_date']}）")
                else:
                    results.append(f"{p['fund_name']}: {r.get('error')}")
            return jsonify({"ok": True, "message": "执行成功：" + "；".join(results)})

        pid = int(q.get("id") or 0)
        if action == "pause":
            ok = db.update_dca_plan(pid, status="paused")
        elif action == "resume":
            ok = db.update_dca_plan(pid, status="active")
        elif action == "delete":
            ok = db.delete_dca_plan(pid)
        else:
            return jsonify({"ok": False, "error": f"未知操作: {action}"})
        return jsonify({"ok": ok, "message": f"已完成定投操作 ID={pid}" if ok else f"未找到定投 ID={pid}"})
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "参数格式错误"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"操作失败: {e}"})
    finally:
        db.close()
        if request.method == "POST":
            _invalidate_caches("rebalance")     # 定投执行/补录会新增持仓（GET 读不失效）



def _precompute_snapshots():
    """启动时后台预热快照：快照还新鲜就跳过，不与首屏请求抢资源。"""
    jobs = [("overview", _overview_compute), ("funds", _all_funds),
            ("rebalance", _all_rebalance), ("__dash__", _compute_dashboard)]
    for key, fn in jobs:
        try:
            db = get_db()
            try:
                fresh_enough = db.get_analysis_snapshot(key, SNAP_TTL) is not None
            finally:
                db.close()
            if fresh_enough:
                continue
            _cached_get(key, fn)
        except Exception:
            continue


def main():
    """启动 Flask 仪表盘（http://localhost:5020）"""
    print("=" * 50)
    print("🚀 量化基金仪表盘")
    print("   http://localhost:5020")
    print("=" * 50)

    # 预计算板块数据（后台线程，不阻塞启动）
    threading.Thread(target=_precompute_sectors, daemon=True).start()
    # 预热分析快照（同上；已新鲜则跳过）
    threading.Thread(target=_precompute_snapshots, daemon=True).start()

    # 只监听回环地址：本系统无鉴权且管的是真实资金记录，绝不能被同网段其它设备读写。
    # threaded=True：允许 /api/all 全量计算时，量化模型/板块/消息面卡片仍并发加载
    app.run(host="127.0.0.1", port=5020, debug=False, threaded=True)


if __name__ == "__main__":
    main()

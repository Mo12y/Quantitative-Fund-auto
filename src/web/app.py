"""Flask 仪表盘"""

import sys, os, threading, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Web 仪表盘固定离线(读本地 DB 快照)计算温度等，避免首屏每次都联网 akshare。
# 联网刷新请用 CLI：python src/main.py temp（此时不设此变量，默认 live）。
os.environ.setdefault("QFA_MARKET_LIVE", "0")

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory
from flask.json.provider import DefaultJSONProvider
from src.data.database import Database
from src.analysis.fund_classify import type_bucket
# 2026-10-16 T2.2（删壳）：`FundScreener` / `MarketThermometer` / `SentimentMonitor` /
# `RebalanceAdvisor` / `SectorAnalyzer` / `HistoricalRecommender` 6 个「占位降级类」
# **已删除**（连同消费它们的 7 个死路由）。旧命题模块仍在 `legacy/`（`git mv` 归档，非删除）。
from src.analysis.portfolio import PortfolioTracker
from src.analysis import ledger_write
from src.analysis.dca import DcaManager
from src.analysis.investment_plan import get_plan, get_progress, ensure_seed
from src.analysis import fund_boards
from src.analysis import peer_percentile
from src.analysis import behavior_profile, breakeven, counterfactual, fund_fee  # 决策台（T2.1）
from src.analysis import user_constraint, user_profile
from src.analysis import nav_series, nav_metrics
from src.analysis import portfolio_overlap

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
# ⚠️ 2026-10-06 B-6：旧前端的 `app.js` / `app.css` 已删除（连同 `_archive/dashboard.html`
# 与 `scripts/check_rise_fall.py`）。原来那个指向 `src/web/static/` 的 `STATIC_DIR` 常量
# **从未被任何路由使用**（静态文件一直由 Flask 的默认 `/static/<path>` 提供），故一并删掉。
# ⚠️ `src/web/static/favicon.svg` **保留** —— 新前端的 `frontend/index.html` 引用了
# `/static/favicon.svg`（Flask 默认静态路由提供），删了就是控制台一条 404。
# 新前端（React）构建产物；由 `/` 路由托管（B-5 切换后旧仪表盘退役）
FRONTEND_DIST = os.path.join(BASE_DIR, "frontend", "dist")

# 聚合仪表盘缓存 —— 首个请求全量计算，之后 TTL 内刷新秒回
_dash_cache = None
_dash_lock = threading.Lock()
_dash_computing = False          # single-flight：并发 cache miss 只算一次
_dash_done = threading.Event()
DASH_CACHE_TTL = 240.0          # 秒
DASH_WAIT = 30.0                # single-flight 等待上限：远小于 TTL，避免等待者白等 4 分钟

# 通用“单槽 + 单飞 + TTL”缓存，供 /api/overview 等轻量端点复用
# （T2.2：原复用方 /api/funds、/api/rebalance、/api/sectors、/api/sentiment 已删）
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
    """应用首页 —— **新前端（React 构建产物）**。

    方案 B（2026-10-04 用户拍板、B-5 切换）：新前端挂 `/`，旧仪表盘退役。
    产物由 `frontend/` 构建（`npm run build`，vite `base='/'`）；
    未构建时给**明确提示**（503 + 怎么修），而不是让浏览器拿到一个 404 空白页。
    """
    if not os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
        return jsonify({"ok": False,
                        "error": "前端尚未构建：在 frontend/ 下执行 npm run build 后重启"}), 503
    return send_from_directory(FRONTEND_DIST, "index.html")


@app.route("/assets/<path:filename>")
def frontend_assets(filename):
    """新前端的静态资源。

    ⚠️ 必须有这条路由：vite `base='/'` 后，`index.html` 里引用的是 `/assets/index-xxx.js`
    （不是 `/v2/assets/...`）—— 少了它页面会白屏（只有 HTML、没有 JS）。
    """
    return send_from_directory(os.path.join(FRONTEND_DIST, "assets"), filename)


@app.route("/v2")
@app.route("/v2/")
@app.route("/v2/<path:filename>")
def legacy_v2_redirect(filename=None):
    """旧地址 `/v2*` → **301 到 `/`**（B-5 切换后旧路径不再服务）。

    `<path>` 部分映射到 `/#/<path>` 以**保留深链**（新前端是 hash 路由）。
    ⚠️ 用 301 而非 302：让浏览器与书签把新地址记下来，之后不再多一跳。

    ⚠️ 2026-10-06 B-6 后：旧前端的源文件（`app.js` / `app.css` / `_archive/dashboard.html`）
    **已删除**，所以"旧标签页仍能刷新"这件事已不成立 —— 但**本重定向仍然保留**：
    它是给**书签 / 外部链接**用的（那些人需要被送到新地址），与旧文件在不在无关。
    """
    if filename:
        return redirect("/#/" + filename, code=301)
    return redirect("/", code=301)


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


def _nav_spans(codes: list) -> dict:
    """一次查询拿到一批基金的净值**日历跨度（年）**与最后净值日。

    为什么要跨度：B2 要求"存续不足 3 年"的基金**显式声明**不给百分位，
    而判断依据是日历跨度（不是点数 —— 定开基金点数少但跨度够，见 B3）。
    """
    out = {}
    if not codes:
        return out
    try:
        db = get_db()
        try:
            CH = 400
            from datetime import date as _d
            for i in range(0, len(codes), CH):
                chunk = codes[i:i + CH]
                q = ("SELECT fund_code, MIN(nav_date), MAX(nav_date) FROM fund_nav "
                     "WHERE fund_code IN (%s) GROUP BY fund_code" % ",".join("?" * len(chunk)))
                for code, d0, d1 in db.conn.execute(q, chunk):
                    try:
                        span = (_d.fromisoformat(str(d1)[:10]) - _d.fromisoformat(str(d0)[:10])).days / 365.25
                    except Exception:
                        span = None
                    out[code] = {"span_years": span, "nav_asof": str(d1)[:10] if d1 else None}
        finally:
            db.close()
    except Exception:
        pass
    return out


def _peer_block(cache: dict, code: str, fund_type: str, metrics: dict, span_info: dict) -> dict:
    """B2/B4 的「同侪参照系」块 —— 三个端点共用，保证口径一致。

    返回（**只增不减**，向后兼容）：
        group / group_n / percentiles / nav_asof / insufficient_data / reason

    纪律（任务书 B2）：样本不足**必须显式声明**，`percentiles` 为 None 且带 reason。
    宁可显示"数据不足"，也不要显示一个具体数字 —— 本项目已有"五个维度全缺仍显示
    市场温度 50.0° 适中"的反面教材。
    """
    span = (span_info or {}).get("span_years")
    nav_asof = (span_info or {}).get("nav_asof")
    block = {"group": None, "group_n": None, "percentiles": None, "nav_asof": nav_asof,
             "insufficient_data": True, "reason": None, "percentiles_scale": None}

    group = peer_percentile.group_of(fund_type)
    block["group"] = group
    if cache is None:
        block["reason"] = "参照系未构建（缺 data/peer_distributions.json）"
        return block
    if group is None:
        block["reason"] = "类型未映射到参照系组"
        return block
    e = peer_percentile.group_entry(cache, group)
    block["group_n"] = (e or {}).get("n")
    # R3：存续不足 3 年 → 参照系根本不收录它，不给百分位
    if span is not None and span < peer_percentile.MIN_SPAN_YEARS:
        block["reason"] = "存续不足3年（%.1f 年）" % span
        return block
    pcts, missing = {}, []
    for metric in ("momentum_3m", "max_drawdown_1y", "sharpe", "annual_return", "ann_vol"):
        v = (metrics or {}).get(metric)
        if v is None:
            continue
        d = peer_percentile.describe(cache, group, metric, v, nav_asof)
        if d.get("insufficient_data"):
            missing.append(metric)
        elif d.get("percentile") is not None:
            p = d["percentile"]
            # 按方向翻转到"**越大越好**"的统一口径（与参照系的 DIRECTION 一致）：
            #   回撤/波动越小越好 → 100-p；动量 direction=None（中性）→ 保持原始**位置**
            dirn = peer_percentile.DIRECTION.get(metric)
            pcts[metric] = round(100.0 - p, 1) if dirn is False else p
    if not pcts:
        block["reason"] = "该基金无可用指标（缺失：%s）" % ("、".join(missing) or "全部")
        return block
    block["percentiles"] = pcts
    block["insufficient_data"] = False
    # 前端口径提示：momentum 是"位置"不是"优劣"（C2），必须显式说清
    block["percentiles_scale"] = "0-100，越大越好；momentum_3m 例外=同类位置（越大=涨得越多，不代表更好）"
    if missing:
        block["reason"] = "部分指标缺失，未纳入分位：%s" % "、".join(missing)
    return block


def _user_constraints(db, codes=None):
    """装配 B 层运行时数据：持仓（库内）+ 画像（本地配置）+ 相关性（候选×持仓）→ `(built, ctx, meta)`。

    只读；读持仓失败 → `(None, None, {"error": ...})`（调用方**不得**当成"无约束"，
    必须显式声明，否则"没算"会被当成"通过"）。

    codes：本次要评估的候选代码（给了才计算相关性重叠 —— 只有 `corr_overlap`
    约束需要它；没给则该约束评估时为 skipped）。
    """
    try:
        holdings = [dict(r) for r in db.conn.execute(
            "SELECT * FROM holdings WHERE status IN ('holding','pending_confirm')")]
    except Exception as e:
        return None, None, {"error": "持仓读取失败，未应用用户约束：%s" % str(e)[:60]}
    profile, profile_note = user_profile.load_profile()
    built = user_constraint.build_constraints_from_user(profile, holdings)
    ctx = {"holdings": holdings, "profile": profile}
    meta = {"profile_note": profile_note, "holdings_n": len(holdings), "overlap_note": None}
    if codes:
        held = [h.get("fund_code") for h in holdings if h.get("fund_code")]
        try:
            ctx["overlap"] = portfolio_overlap.overlap_map(db.conn, list(codes), held)
            note = "相关性：%d/%d 只可计算（近 1 年周收益）" % (len(ctx["overlap"]), len(codes))
        except Exception as e:
            ctx["overlap"] = {}
            note = "相关性计算失败：%s" % str(e)[:60]
        # 类对基线（相对模式的门限底数）：缺文件 → None，约束评估时**声明"未评估"**（不猜）
        ctx["overlap_baseline"] = portfolio_overlap.load_baselines()
        if ctx["overlap_baseline"]:
            note += "；类对基线 as_of=%s" % ctx["overlap_baseline"].get("as_of")
        else:
            note += "；类对基线未生成（跑 scripts/build_overlap_baselines.py；相对模式将声明未评估）"
        meta["overlap_note"] = note
    return built, ctx, meta


def _review_block(built, res, meta, before_n: int) -> dict:
    """§4.6 可解释性块（三个接入点共用同一结构）"""
    return {
        "applied": [{"kind": c.kind, "params": c.params, "source": c.source,
                     "description": c.description} for c in built.constraints],
        "note": built.note,
        "profile_note": meta.get("profile_note"),
        "holdings_n": meta.get("holdings_n"),
        "overlap_note": meta.get("overlap_note"),
        "counts": {"before": before_n, "kept": len(res.kept),
                   "dropped": len(res.dropped), "skipped": len(res.skipped)},
        "dropped": [{"code": p.get("code"), "name": p.get("name"),
                     "reasons": p.get("dropped_reasons", [])} for p in res.dropped],
        "skipped": [{"code": p.get("code"), "name": p.get("name"),
                     "reasons": p.get("skipped_reasons", [])} for p in res.skipped],
    }


def _apply_user_constraints(db, picks: list) -> tuple:
    """设计稿 §4.4 第 3 层：推荐 = 第 1 层（同类百分位）∩ 第 2 层（用户约束）。

    用于"给出一个列表"的场合（`/api/recommend` 的候选、调仓顾问的买入候选池）：
    返回 `(kept_picks, review_block)` —— **未通过/未评估的不在 kept 里**，
    但必须通过 review 显式回报（不静默丢弃）。**失败不阻断主流程**。
    """
    built, ctx, meta = _user_constraints(db, [p.get("code") for p in (picks or [])])
    if built is None:
        return list(picks), {"error": meta["error"]}
    res = user_constraint.apply_constraints(picks, built.constraints, ctx)
    return res.kept, _review_block(built, res, meta, len(picks))


# 2026-10-16 T2.2（删壳）：原 `_annotate_user_constraints`（筛选池"只标注不剔除"）/
# `_with_pool_constraints` / `_with_board_constraints` 已删除 —— 它们的唯一消费方
# （/api/funds、/api/funds/board）已随旧命题删除。用户约束体系本身**保留**：
# `_user_constraints` / `_review_block` / `_apply_user_constraints` 仍被
# /api/explain 与调仓外场景使用（见 `src/analysis/user_constraint.py`）。


# ══════════════════════════════════════════════════════════════════════
# 数据链路下钻（/api/explain）—— 结论卡那五格，点开每一格都是**真实产物**
#
# 为什么要有这个端点：用户是**真金白银在实盘**，不是拿虚拟样本做演示 —— 所以
# 「采集 → 清洗 → 特征 → 建模 → 评估」不能只是文案，每一格都必须能指回
# **账户/库里的具体数字或文件**（哪张表多少行、哪只基金哪一天、哪个脚本哪份报告）。
#
# 纪律：本端点**业务只读**（不写账本/行情等业务表、不联网）—— 唯一例外是
# `_cached_get` 的缓存层会**回填 SQLite 快照**（那是 cache 机制，不是业务写）；
# 联网类核验（如双源校验）如实标 `available=False` + 原因，**不拿旧值冒充新值**；
# 策略层的负结论**如实引用文档口径**并写明来源，不与账户的真实收益混为一谈。
# ══════════════════════════════════════════════════════════════════════
EXPLAIN_TTL = 1800.0


def _explain_holdings(db, summary) -> list:
    """持仓（按市值降序、**按基金去重**）—— 结构以 `get_portfolio_summary()` 实际返回为准：
    `holdings_detail`（**不是** `holdings`：那是 `_portfolio_payload` 归一化后的键）。
    同一只基金可能有多条**批次记录**（小额定投/补仓），这里把多批次的市值**合并**成一行
    （smoke 实测 018392 出现两次 → 会误导"你有两只黄金"）。"""
    agg: dict = {}
    for h in ((summary or {}).get("holdings_detail") or []):
        code = str(h.get("fund_code") or "")
        v = h.get("current_value")
        if v is None and h.get("shares"):
            nav = db.get_latest_fund_nav(h.get("fund_code")) or {}
            v = float(h.get("shares") or 0) * float(nav.get("unit_nav") or 0)
        agg[code] = agg.get(code, {"code": code, "name": h.get("fund_name") or "", "value": 0.0})
        agg[code]["value"] += float(v or 0)
    out = sorted(agg.values(), key=lambda x: -x["value"])
    return [{"code": x["code"], "name": x["name"], "value": round(x["value"], 2)} for x in out]


def _explain_collect(db, holds) -> dict:
    """采集：这一格回答「你的数据是哪来的、新到什么程度」"""
    c = db.conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM fund_nav").fetchone()[0]
    funds = c.execute("SELECT COUNT(DISTINCT fund_code) FROM fund_nav").fetchone()[0]
    last = c.execute("SELECT MAX(nav_date) FROM fund_nav").fetchone()[0]
    rows = []
    for h in holds[:8]:
        r = c.execute("SELECT COUNT(*), MIN(nav_date), MAX(nav_date) FROM fund_nav WHERE fund_code=?",
                      (h["code"],)).fetchone()
        rows.append({"label": "%s %s" % (h["code"], h["name"]),
                     "value": "%s 行 · %s ~ %s" % (f"{r[0]:,}", r[1], r[2]),
                     "tone": "flat"})
    val_at = c.execute("SELECT MAX(updated_at) FROM index_valuation").fetchone()[0]
    hl = c.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
    tx = c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return {"key": "collect", "title": "采集",
            "headline": "净值 %s 行 · %s 只 · 最新 %s" % (f"{total:,}", f"{funds:,}", last),
            "rows": rows,
            "artifacts": [{"name": "fund_nav", "detail": "本库主表 · 逐只如上"},
                          {"name": "index_valuation", "detail": "估值快照 · 截至 %s" % val_at},
                          {"name": "holdings / transactions",
                           "detail": "你的账本 %d 笔持仓 · %d 笔流水" % (hl, tx)}]}


def _explain_clean(db, holds) -> dict:
    """清洗：这一格回答「数据可信度」——做**本地可验证**的自检（联网双源校验按需另跑）"""
    c = db.conn.cursor()
    rows = []
    for h in holds[:8]:
        r = c.execute("""SELECT COUNT(*),
                                SUM(CASE WHEN unit_nav IS NULL AND acc_nav IS NULL THEN 1 ELSE 0 END),
                                SUM(CASE WHEN COALESCE(acc_nav, unit_nav) <= 0 THEN 1 ELSE 0 END),
                                COUNT(DISTINCT nav_date)
                         FROM fund_nav WHERE fund_code=?""", (h["code"],)).fetchone()
        n, nulls, zeros, distinct = r[0], r[1] or 0, r[2] or 0, r[3] or 0
        dup = n - distinct
        bad = nulls + zeros + dup
        rows.append({"label": "%s %s" % (h["code"], h["name"]),
                     "value": ("干净（%s 天）" % f"{n:,}") if bad == 0
                              else "异常 %d 处（空 %d · 非正 %d · 重复日期 %d）" % (bad, nulls, zeros, dup),
                     "tone": "flat" if bad == 0 else "fall"})
    big = c.execute("SELECT COUNT(*) FROM fund_info WHERE fund_size > 1000").fetchone()[0]
    return {"key": "clean", "title": "清洗",
            "headline": "本地自检：口径与完整性 · 巨值异常 %d 条（万元/亿元事故后应为 0）" % big,
            "rows": rows,
            "artifacts": [{"name": "scripts/verify_nav_dual_source.py",
                           "detail": "双源校验（联网·按需跑）—— 上次实测 016453/007029 严重差 0"},
                          {"name": "scripts/check_fund_size_scale.py",
                           "detail": "规模单位自检 · 当前 >1000 亿 = %d 条" % big}]}


def _explain_feature(db, holds) -> dict:
    """特征：这一格回答「你的每只基金在同类里排第几」"""
    cache = peer_percentile.load_cache()
    spans = _nav_spans([h["code"] for h in holds])
    rows = []
    for h in holds[:8]:
        info = db.get_fund_info(h["code"]) or {}
        ft = info.get("fund_type") or ""
        rec = db.conn.execute(
            "SELECT nav_date, %s AS v FROM fund_nav WHERE fund_code=? "
            "AND nav_date >= date('now','localtime','-1095 days') ORDER BY nav_date"
            % nav_series.valuation_nav_sql(), (h["code"],)).fetchall()
        vals = [float(x[1]) for x in rec if x[1] is not None]
        dates = [str(x[0]) for x in rec if x[1] is not None]
        if not vals:
            rows.append({"label": "%s %s" % (h["code"], h["name"]), "value": "无净值 → 不评估", "tone": "flat"})
            continue
        m = nav_metrics.compute(vals, dates)
        pb = _peer_block(cache, h["code"], ft, m, spans.get(h["code"]) or {})
        p = (pb or {}).get("percentiles") or {}
        seg = " · ".join("%s P%s" % (k, round(v)) for k, v in
                         (("夏普", p.get("sharpe")), ("回撤", p.get("max_drawdown_1y")),
                          ("年化", p.get("annual_return"))) if v is not None)
        rows.append({"label": "%s %s" % (h["code"], h["name"]),
                     "value": "%s · 组内 %s 只 · %s" % ((pb or {}).get("group") or ft or "—",
                                                        f"{(pb or {}).get('group_n') or 0:,}", seg or "样本不足"),
                     "tone": "flat"})
    return {"key": "feature", "title": "特征",
            "headline": "同类分位（不是全市场排名）· 6 组 · 组内 n=644~3,544",
            "rows": rows,
            "artifacts": [{"name": "data/peer_distributions.json",
                           "detail": "参照系分位网格（as_of 见文件）"},
                          {"name": "src/analysis/nav_metrics.py",
                           "detail": "夏普/回撤/波动/索提诺/卡玛（3 年窗口 · 口径见模块 docstring）"}]}


def _scope_text(scope) -> str:
    """把温度适用边界（thermometer.TEMPERATURE_SCOPE 的结构）渲染成**中文短句**。

    ⚠️ 原来这里写的是 `str(t.get("scope"))[:80]`，于是界面上出现
    `适用边界 = {'applies_to': ['A 股权益（股票型 / 混合型 / 指数型及其联接）'], 'not_applicable_to': ['债券型', '货币型…`
    —— 既是 **Python 字面量直接上屏**（把内部结构泄漏给用户），又被 80 字**截断**
    （`not_applicable_to` 后半段直接丢掉）。这正是 `docs/前端重构计划书.md` §9.2 记的缺口。
    """
    if not isinstance(scope, dict):
        return str(scope or "—")

    def _cat(v):
        if isinstance(v, (list, tuple)):
            return " / ".join(str(x) for x in v if x)
        return str(v or "")

    parts = []
    if _cat(scope.get("applies_to")):
        parts.append("适用 %s" % _cat(scope.get("applies_to")))
    if _cat(scope.get("not_applicable_to")):
        parts.append("不适用 %s" % _cat(scope.get("not_applicable_to")))
    return "；".join(parts) or "—"


def _explain_model(db, holds, temp) -> dict:
    """建模：这一格回答「今天这条结论由什么规则触发」——键名以 thermometer 实际返回为准"""
    t = temp or {}
    rows = []
    if t.get("temperature") is not None:
        rows.append({"label": "温度", "value": "%s°（%s）→ 目标权益仓位 %s%%"
                     % (round(t.get("temperature")), t.get("level_desc") or t.get("level") or "—",
                        round(t.get("target_equity_pct")) if t.get("target_equity_pct") is not None else "—"),
                     "tone": "flat"})
        if t.get("scope"):
            rows.append({"label": "适用边界", "value": _scope_text(t.get("scope")), "tone": "flat"})
    try:
        summary = PortfolioTracker(db).get_portfolio_summary()
        alloc = summary.get("asset_allocation") or {}
        # 权益占比按**口径 SSOT**（fund_scorer.type_bucket）归类，不自己写关键词
        eq = sum(float(v or 0) for k, v in alloc.items() if type_bucket(k) == "equity")
        if alloc:
            rows.append({"label": "你当前权益",
                         "value": "%.1f%%（SSOT 口径 `type_bucket`：QDII海外/指数型-其他[含黄金]按权益归类；"
                                  "分类型 %s）"
                                  % (eq, "、".join("%s %.1f%%" % (k, v) for k, v in alloc.items())),
                         "tone": "flat"})
    except Exception as e:
        rows.append({"label": "你当前权益", "value": "算不了：%s" % str(e)[:60], "tone": "flat"})
    kept, review = _apply_user_constraints(db, [{"code": h["code"], "name": h["name"]} for h in holds])
    cr = review or {}
    rows.append({"label": "用户约束", "value": "%d 条生效：%s" % (
        len(cr.get("applied") or []),
        "；".join(a.get("description") or "" for a in (cr.get("applied") or [])) or "无（画像为空）"),
        "tone": "flat"})
    return {"key": "model", "title": "建模",
            "headline": "温度 → 目标仓位；用户约束 4 类；ML 三模型 + GARCH 基线（仅作风险提示）",
            "rows": rows,
            "artifacts": [{"name": "src/analysis/thermometer.py",
                           "detail": "三因子温度（PE/PB/ERP 分位，读 index_valuation）"},
                          {"name": "src/analysis/user_constraint.py",
                           "detail": "B 层约束（持仓重叠/类型/风险/相关性）"},
                          {"name": "config/user_profile.local.yaml", "detail": "你的画像（本地·不进版本库）"}]}


def _explain_eval(db, summary, holds) -> dict:
    """评估：这一格分两半 —— **你的真实战绩** + **策略的诚实结论**（不许混在一起）"""
    rows = []
    s = summary or {}
    r = s.get("realized") or {}
    # 已实现 = 买卖价差 + 分红（设计稿 §6/§7：两者分开存，展示相加才是总已实现）
    realized_total = float(r.get("total_pnl") or 0) + float(r.get("dividend_total") or 0)
    xirr = None
    try:
        xirr = (PortfolioTracker(db).get_xirr() or {}).get("xirr")
    except Exception:
        pass
    rows.append({"label": "你的账面（真金白银）",
                 "value": "市值 ¥%s · 投入 ¥%s · 未实现 %+.2f · 已实现 %+.2f（%d 笔卖出）"
                          % (f"{float(s.get('total_market_value') or 0):,.2f}",
                             f"{float(s.get('total_invested') or 0):,.2f}",
                             float(s.get("total_pnl") or 0), realized_total,
                             int(r.get("count") or 0)),
                 "tone": "flat"})
    if xirr is not None:
        rows.append({"label": "资金加权年化", "value": "XIRR %+.2f%%" % (float(xirr) * 100),
                     "tone": "fall" if float(xirr) < 0 else "rise"})
    rows.append({"label": "策略样本外（51 窗口）",
                 "value": "S1 − 等权 = −0.09pp · α t=−1.26（不显著）· BH p=0.61 → 无证据优于等权持有",
                 "tone": "flat"})
    return {"key": "eval", "title": "评估",
            "headline": "结论：没有证据能选出未来赢家 —— 所以上面给的是「温度+硬约束」，不是预测",
            "rows": rows,
            "artifacts": [{"name": "docs/基金推荐系统_ML可行性研究.md",
                           "detail": "§2.1b 三次实证与局限（幸存者偏差/多重检验）"},
                          {"name": "src/analysis/oos_validate.py",
                           "detail": "CLI: python src/main.py oos（可复现）"}]}


def _all_explain() -> dict:
    """数据链路下钻（缓存 30 分钟；只读）"""
    try:
        db = get_db()
        try:
            summary = PortfolioTracker(db).get_portfolio_summary()
            holds = _explain_holdings(db, summary)
            # T2.2：温度模块已归档 → 传 None，`_explain_model` 会自动跳过"温度"行
            stages = [_explain_collect(db, holds), _explain_clean(db, holds),
                      _explain_feature(db, holds), _explain_model(db, holds, None),
                      _explain_eval(db, summary, holds)]
        finally:
            db.close()
        return {"asof": _today(), "holdings_n": len(holds),
                "account": _portfolio_payload(summary), "stages": stages}
    except Exception as e:
        return {"error": "链路下钻失败：%s" % str(e)[:120], "stages": []}


# 2026-10-16 T2.2（删壳）：原 `_slot_ready` / `_start_funds_warm`（筛选池后台预热）/
# `_all_funds` 已删除 —— 它们只服务 /api/funds、/api/funds/board 与旧聚合预热，
# 随死路由一并移除（旧命题模块仍在 `legacy/`）。


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
            "legacy_rule_deviation": h.get("legacy_rule_deviation"),
            "is_pending": h.get("is_pending", False),
            "apply_date": h.get("apply_date"),
            "confirm_date": h.get("confirm_date"),
            "accrual_start": h.get("accrual_start"),
            "confirm_nav": h.get("confirm_nav"),
            "effective_date": h.get("effective_date"),
            "nav_missing": h.get("nav_missing", False),
            "current_nav": h.get("current_nav"),
            "nav_latest_date": h.get("nav_latest_date"),
            # replay_pct = 净值比值法（基金净值涨跌），**不等于** pnl_pct（金额法，"我赚了多少"）。
            # 两者差一个"份额四舍五入到 2 位"的楔子（约 0.1%）。前端当前只显示 pnl_pct；
            # 若要把本字段露出，必须显式标注为「基金净值涨跌（不含份额舍入）」（F-05 口径纪律）。
            "replay_pct": h.get("replay_pct"),
            "pending_est_pct": h.get("pending_est_pct"),
            # 分红（设计稿 §6 前端部分）：现金余额 + 策略，供持仓行展示与切换
            "cash_balance": h.get("cash_balance", 0) or 0,
            "dividend_policy": h.get("dividend_policy") or "reinvest",
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
            # 分红收益（设计稿 §6/§7 第 6 步）：与买卖价差**分开列**，前端相加才是总已实现。
            # 缺失 → 前端「已实现收益」整块漏掉分红（只分红账户显示 ¥0.00）。
            "dividend_total": r.get("dividend_total", 0),
            "dividend_count": r.get("dividend_count", 0),
            "dividends": [{
                "date": d.get("date"), "fund_code": d.get("fund_code"),
                "fund_name": d.get("fund_name"), "mode": d.get("mode"),
                "per_share": d.get("per_share"), "shares": d.get("shares"),
                "nav": d.get("nav"), "amount": d.get("amount"),
            } for d in (r.get("dividends") or [])[:50]],
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


# 2026-10-16 T2.2（删壳）：原 `_all_rebalance`（调仓建议，最慢 ~17s，含温度/筛选重算）
# 已删除 —— 它由已归档的 `RebalanceAdvisor` 占位类驱动，且其消费路由 /api/rebalance 已删。
# 旧命题模块仍在 `legacy/`。


def _compute_dashboard():
    """聚合总览（T2.2 瘦身）：内容 = `_overview_compute`（投资计划 + 持仓 + 统计 + 曲线）。

    原实现并行计算 temperature / funds / rebalance 三块重活（funds 冷算 ~110s）——
    三块均已随旧命题删除；现聚合内容与 overview 相同，但**保留本函数名与 `__dash__`
    缓存槽**（/api/all 的缓存/快照协议不动，前端只读字段不受影响）。
    """
    return _overview_compute()


@app.route("/api/all")
def api_all():
    """聚合 API：投资计划 + 持仓（本地并行 + TTL 缓存；T2.2 后内容 = `_overview_compute`）。

    历史：原含 温度 / 基金池 / 调仓建议 / 消息面 —— 均已随旧命题删除；
    保留独立 `__dash__` 缓存槽与快照协议（向后兼容，前端只读字段不受影响）。
    """
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

        # 1.6) T2.2：聚合内容已是轻量 overview（秒级）→ 冷启动直接落入下方同步计算，
        # 不再需要 warming 协议（原"筛选池冷算 ~110s 会拖垮请求"的前提已不存在）。

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
        data = {"error": f"compute failed: {e}", "plan": []}
    with _dash_lock:
        _dash_cache = {"at": time.monotonic(), "data": data}
        _dash_computing = False
        _dash_done.set()
    _snap_write("__dash__", data)
    return jsonify({"ok": True, "data": data, "source": "live"})


def _overview_compute() -> dict:
    """轻量总览：投资计划 + 持仓（并行，约 1~2s）。

    T2.2：温度模块已归档 → 原 `temp` 字段删除（前端不应再依赖该键）。
    """
    out = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        fs = {ex.submit(_all_plan): "plan",
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


# =================================================================
# 决策台 API（T2.1，2026-10-16）
# 契约：`docs/05-重构/01_决策支持系统_执行计划书.md` §3（GET、只读、模块返回**原样透传**）
# 只调既有分析模块，不重写任何计算；"样本不足 / 数据缺失"由模块自己声明，一律如实透传。
# =================================================================

def _decision_fund_rows(db):
    """决策台共用：当前持仓（status != 'sold'）去重基金 + fund_info 字段。"""
    return db.conn.execute(
        "SELECT DISTINCT h.fund_code, f.fund_name, f.fund_type,"
        " f.mgt_fee, f.custodian_fee, f.sales_service_fee"
        " FROM holdings h LEFT JOIN fund_info f ON f.fund_code = h.fund_code"
        " WHERE COALESCE(h.status, '') != 'sold' ORDER BY h.fund_code").fetchall()


def _fund_nav_metrics(conn, code):
    """单只基金的风险指标：净值序列 → `nav_metrics.compute`（点数不足返回 None）。"""
    series = behavior_profile.nav_series(conn, code)
    return nav_metrics.compute([v for _, v in series], [d for d, _ in series])


def _fund_ter(row) -> "tuple":
    """单只基金的 TER（fund_fee.compute_ter 的单一实现，不重算）。"""
    return fund_fee.compute_ter({"mgt_fee": row["mgt_fee"],
                                 "custodian_fee": row["custodian_fee"],
                                 "sales_service_fee": row["sales_service_fee"]})


@app.route("/api/behavior")
def api_behavior():
    """行为镜（契约 §3.2）：追高 / 割肉 / 卖飞 / 频率。只读。"""
    db = get_db()
    try:
        data = behavior_profile.analyze(db)
    except Exception as e:
        data = {"error": str(e)}
    finally:
        db.close()
    return jsonify({"ok": True, "data": data})


@app.route("/api/counterfactual")
def api_counterfactual():
    """反事实归因（契约 §3.3）：compare + same_fund + sell_rules 三合一。只读。"""
    db = get_db()
    try:
        data = {
            "compare": counterfactual.compare(db),
            "same_fund": counterfactual.same_fund_counterfactual(db),
            "sell_rules": counterfactual.simulate_sell_rules(db),
        }
    except Exception as e:
        data = {"error": str(e)}
    finally:
        db.close()
    return jsonify({"ok": True, "data": data})


@app.route("/api/breakeven")
def api_breakeven():
    """回本门槛 / 费率漏损（契约 §3.4）：每笔持仓"要涨多少才不亏"。只读。"""
    db = get_db()
    try:
        data = breakeven.analyze(db)
    except Exception as e:
        data = {"error": str(e)}
    finally:
        db.close()
    return jsonify({"ok": True, "data": data})


@app.route("/api/risk")
def api_risk():
    """持仓风险刻画（契约 §3.5）：波动 / 回撤 / 夏普等，**按需现算**（持仓个位数基金，毫秒级）。"""
    db = get_db()
    try:
        funds = []
        for r in _decision_fund_rows(db):
            m = _fund_nav_metrics(db.conn, r["fund_code"])
            if m is None:
                funds.append({"fund_code": r["fund_code"], "fund_name": r["fund_name"],
                              "metrics": None, "insufficient": True,
                              "reason": "净值点不足 %d" % nav_metrics.MIN_POINTS})
            else:
                funds.append({"fund_code": r["fund_code"], "fund_name": r["fund_name"],
                              "metrics": m, "insufficient": False, "reason": None})
        data = {"funds": funds}
    except Exception as e:
        data = {"error": str(e)}
    finally:
        db.close()
    return jsonify({"ok": True, "data": data})


def _peer_unavailable() -> dict:
    """参照系未构建时的统一声明（**不猜**，与 peer_percentile 的 load_cache 契约一致）。"""
    return {"error": "同类参照系未构建，请先跑 build_distributions"}


@app.route("/api/peer")
def api_peer():
    """同类分位（契约 §3.6）：sharpe / max_drawdown_1y / ter 三个分位（原始分位，越大=值越大）。"""
    db = get_db()
    try:
        cache = peer_percentile.load_cache()
        if cache is None:
            return jsonify({"ok": True, "data": _peer_unavailable()})
        nav_asof = db.get_latest_nav_date()
        funds = []
        for r in _decision_fund_rows(db):
            code, name = r["fund_code"], r["fund_name"]
            group = peer_percentile.group_of(r["fund_type"])
            if not group:
                funds.append({"fund_code": code, "fund_name": name, "group": None,
                              "sharpe": None, "max_drawdown_1y": None, "ter": None,
                              "insufficient_data": True, "reason": "类型未映射到参照系组"})
                continue
            m = _fund_nav_metrics(db.conn, code) or {}
            ter, _missing = _fund_ter(r)
            funds.append({
                "fund_code": code, "fund_name": name, "group": group,
                "sharpe": peer_percentile.describe(cache, group, "sharpe",
                                                   m.get("sharpe"), nav_asof),
                "max_drawdown_1y": peer_percentile.describe(cache, group, "max_drawdown_1y",
                                                            m.get("max_drawdown_1y"), nav_asof),
                "ter": peer_percentile.describe(cache, group, "ter", ter, nav_asof),
            })
        data = {"asof": nav_asof, "funds": funds}
    except Exception as e:
        data = {"error": str(e)}
    finally:
        db.close()
    return jsonify({"ok": True, "data": data})


@app.route("/api/exclusion")
def api_exclusion():
    """排雷（契约 §3.7）：两个可辩护判据 —— 同类夏普垫底（≤P10）/ TER 偏高（≥P90）。

    ⚠️ 判据所需的任一指标不可用 → 该基金进 `unrated`（**不判、不保清白**），
    绝不放行成 clean（判据看不见数据必须显式声明）。
    """
    db = get_db()
    try:
        cache = peer_percentile.load_cache()
        if cache is None:
            return jsonify({"ok": True, "data": _peer_unavailable()})
        nav_asof = db.get_latest_nav_date()
        excluded, clean, unrated = [], [], []
        for r in _decision_fund_rows(db):
            code, name = r["fund_code"], r["fund_name"]
            group = peer_percentile.group_of(r["fund_type"])
            if not group:
                unrated.append({"fund_code": code, "reason": "类型未映射到参照系组"})
                continue
            entry = peer_percentile.group_entry(cache, group)
            if not entry or entry.get("quality_level") == "insufficient":
                unrated.append({"fund_code": code, "reason": "组内样本不足"})
                continue
            m = _fund_nav_metrics(db.conn, code) or {}
            ter, _missing = _fund_ter(r)
            sh = peer_percentile.describe(cache, group, "sharpe", m.get("sharpe"), nav_asof)
            te = peer_percentile.describe(cache, group, "ter", ter, nav_asof)
            if sh.get("insufficient_data"):
                unrated.append({"fund_code": code,
                                "reason": "夏普分位不可用（%s）" % sh.get("reason")})
                continue
            if te.get("insufficient_data"):
                unrated.append({"fund_code": code,
                                "reason": "TER 分位不可用（%s）" % te.get("reason")})
                continue
            sp, tp = sh.get("percentile"), te.get("percentile")
            reasons = []
            if sp is not None and sp <= 10:
                reasons.append("同类夏普分位 ≤10（垫底）")
            if tp is not None and tp >= 90:
                reasons.append("同类 TER 分位 ≥90（费率偏高）")
            if reasons:
                excluded.append({"fund_code": code, "fund_name": name, "reasons": reasons,
                                 "sharpe_percentile": sp, "ter_percentile": tp, "ter": ter})
            else:
                clean.append({"fund_code": code, "fund_name": name,
                              "sharpe_percentile": sp, "ter_percentile": tp})
        data = {"asof": nav_asof, "excluded": excluded, "clean": clean, "unrated": unrated}
    except Exception as e:
        data = {"error": str(e)}
    finally:
        db.close()
    return jsonify({"ok": True, "data": data})


@app.route("/api/calendar")
def api_calendar():
    """交易日历查询（《前端优化设计方案》§246 原标注"未实现"）。

    只读。**刷新仍走 CLI** `python src/main.py calendar` —— 抓交易所日历要出网，
    不适合放进请求路径（那会让页面等一个外部依赖）。
    """
    db = get_db()
    try:
        start = request.args.get("start")
        end = request.args.get("end")
        dates = db.get_trade_dates(start, end)
        return jsonify({"ok": True, "data": {
            "count": len(dates), "dates": dates, "start": start, "end": end,
            "refresh_cmd": "python src/main.py calendar",
        }})
    finally:
        db.close()


@app.route("/api/market/live")
def api_market_live():
    """指数盘中快照 + 本地净值时滞（数据源扩展计划书 阶段 5「补实时行情时滞」）。

    为什么需要：`fund_nav`/`index_daily` 是 T+1，双源校验实测本地落后 4~6 天，
    界面上看到的"市场位置"可能是几天前的。这里给一个**当下**的大盘读数。

    降级策略：出网失败**不报 500** —— 返回 `ok=True` + `available=False` + `reason`，
    由前端如实展示"盘中行情不可用"，而不是让整个页面挂掉或拿旧值冒充实时。
    """
    from src.data import live_quote
    db = get_db()
    try:
        latest = db.get_latest_nav_date()
    finally:
        db.close()
    out = live_quote.fetch()
    out["local_nav_latest"] = latest
    out["lag_note"] = live_quote.lag_note(latest) if latest else "本地无净值记录"
    return jsonify({"ok": True, "data": out})


def _overview_stats(port: dict, plan: dict) -> dict:
    """总览增强指标：总资产 / 累计收益 / 收益率 / 持仓分布 / 行业占比

    总资产口径（2026-09-20 改）：**只算当前在仓资产**（实时按最新净值），
    不再叠加投资计划的 `cash_reserve`。原因：plan.cash_reserve 是"计划出来的"
    数字，与实盘无关 —— 空库时会把某个写死的计划金额当成用户的资产
    （黑箱验收审计 F-01）。计划口径的现金弹药仍在 `cash_reserve` 字段里，
    供计划卡/调仓建议使用，但**不参与总资产**。
    """
    mv = float(port.get("total_market_value") or 0)
    invested = float(port.get("total_invested") or 0)
    pnl = float(port.get("total_pnl") or 0)
    cash = float(plan.get("cash_reserve") or 0)
    holdings = port.get("holdings") or []
    realized = port.get("realized") or {}
    # 已下单未确认（在途）的申购金额 —— 单独展示，不计入"在仓总资产"
    pending_amt = round(sum(float(h.get("buy_amount") or 0)
                            for h in holdings if h.get("status") == "pending_confirm"), 2)
    return {
        "total_assets": round(mv, 2),                 # 总资产 = 当前在仓市值（实时）
        "total_market_value": round(mv, 2),
        "total_invested": round(invested, 2),
        "total_pnl": round(pnl, 2),
        "realized_pnl": round(float(realized.get("total_pnl") or 0), 2),
        "realized_fee": round(float(realized.get("total_fee") or 0), 2),
        "realized_count": int(realized.get("count") or 0),
        # 分红收益（设计稿 §6/§7 第 6 步）：与买卖价差**分开给**，前端相加才是"真·总已实现"。
        # 只给合计的话，KPI 上的"已实现收益"会把分红整块漏掉（用户少看到一笔真钱）。
        "realized_dividend": round(float(realized.get("dividend_total") or 0), 2),
        "realized_dividend_count": int(realized.get("dividend_count") or 0),
        "total_return_pct": round((pnl / invested * 100) if invested else 0, 2),
        "cash_reserve": round(cash, 2),
        "pending_amount": pending_amt,
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


# 2026-10-16 T2.2（删壳）：原 /api/funds（质量筛选池）、/api/rebalance（调仓建议）、
# /api/sentiment（消息面）三个死路由 + 其辅助（`_serialize_senti`、`_get_nav_trend`）
# 已删除 —— 它们由已归档的占位类驱动（fund_scorer / rebalance_advisor / sentiment_monitor）。
# 旧命题模块仍在 `legacy/`。


# 2026-10-16 T2.2（删壳）：原「板块数据缓存」整组（`_format_sectors` / `_save_sectors_cache` /
# `_start_sectors_warm` / `_compute_sectors_bg` / `_precompute_sectors` / `/api/sectors`）与
# `/api/recommend`（历史回测推荐）已删除 —— 它们由已归档的占位类驱动
# （sector_analyzer / historical_recommender），且属旧命题产品面。
# 旧命题模块仍在 `legacy/`；`_clean_recs` 保留（/api/research 仍用它读离线 CSV）。


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


@app.route("/api/research")
def api_research():
    """研究快照（2026-10-16 T2.1：由 `/api/quant_models` 改名）——
    静态读取离线分析 CSV（vol 预测 / 回撤预警 / 组合模拟）的**离线快照**，非实时（契约 §3.8）。"""
    import pandas as pd
    BASE = os.path.join(BASE_DIR, "data")
    out = {"snapshot": True,
           "note": "离线快照（2026-09-11 生成），非实时；展示波动 / 回撤 / 组合模型的研究结果",
           "vol": [], "drawdown": [], "portfolio": [], "signals": [], "reports": {}}

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
                return jsonify({"ok": False, "error": "缺少基金代码"}), 400
            if amount <= 0:
                return jsonify({"ok": False, "error": "买入金额需大于 0"}), 400
            # 代码存在性校验（黑箱验收审计 F-04）：未收录的代码一律拒绝。
            # 旧实现只判"非空 + 金额>0"，导致打错的代码（如 999999）被**静默
            # 写成一笔真持仓**并计入总资产，界面还不给任何提示。
            # 若确实是库内没有的新基金，先跑 `python src/main.py collect` 收录。
            if not db.get_fund_info(code):
                return jsonify({"ok": False,
                                "error": f"库内没有基金代码 {code} —— 请核对代码；"
                                         f"若是新基金，先运行 python src/main.py collect 收录后再录入"}), 400
            date_ = (str(q.get("date") or "").strip()) or _today()
            name = (str(q.get("name") or "").strip()) or (db.get_fund_name(code) or code)
            notes = (str(q.get("notes") or "").strip()) or ""
            after = bool(q.get("after_cutoff"))
            _pf(db).add_buy_transaction(code, name, date_, amount, notes, after_cutoff=after)
            return jsonify({"ok": True, "commit_id": ledger_write.latest_commit_id(db),
                            "message": f"已记录买入：{date_} {name}({code}) ¥{amount:,.2f}"
                                       + ("（15:00 后提交，按下一交易日确认）" if after else "")})

        if action == "sell":
            hid = int(q.get("id") or 0)
            amount = float(q.get("amount") or 0)
            if not hid:
                return jsonify({"ok": False, "error": "缺少持仓ID"}), 400
            if amount <= 0:
                return jsonify({"ok": False, "error": "卖出金额需大于 0"}), 400
            date_ = (str(q.get("date") or "").strip()) or _today()
            _pf(db).record_sell(hid, date_, amount)
            return jsonify({"ok": True, "commit_id": ledger_write.latest_commit_id(db),
                            "message": f"已记录卖出：持仓ID={hid} {date_} ¥{amount:,.0f}"})

        if action == "update":
            hid = int(q.get("id") or 0)
            if not hid:
                return jsonify({"ok": False, "error": "缺少持仓ID"}), 400
            amt = None
            raw_amt = q.get("amount")
            if raw_amt not in (None, ""):
                amt = float(raw_amt)
                if amt <= 0:
                    return jsonify({"ok": False, "error": "金额需大于 0"}), 400
            date_ = None
            raw_date = q.get("date")
            if raw_date:
                date_ = str(raw_date).strip()
            if amt is None and not date_:
                return jsonify({"ok": False, "error": "未提供要修改的字段"}), 400
            # 口径缺陷① 已修在门面：改 buy_amount 会同步 transactions 的 amount/fee/shares
            ok = ledger_write.update_holding(db, hid, buy_amount=amt, buy_date=date_)
            return jsonify({"ok": ok, "commit_id": ledger_write.latest_commit_id(db) if ok else None,
                            "message": f"已更新持仓ID={hid}" if ok else f"未找到持仓ID={hid}"})

        if action == "delete":
            hid = int(q.get("id") or 0)
            # 口径缺陷② 已修在门面：备份 holdings + transactions 两张表，并连带删流水
            ok = ledger_write.delete_holding(db, hid)
            return jsonify({"ok": ok, "commit_id": ledger_write.latest_commit_id(db) if ok else None,
                            "message": f"已删除持仓ID={hid}" if ok else f"未找到持仓ID={hid}"})

        if action == "dividend_policy":
            # 分红策略切换（设计稿 §7 第 6 步）：reinvest（红利再投，默认）| cash（现金分红）
            # 已收进门面（原为绕开锁与事务的裸 SQL，见 ledger_write.set_dividend_policy）
            hid = int(q.get("id") or 0)
            code = str(q.get("code") or "").strip()
            pol = str(q.get("policy") or "").strip().lower()
            try:
                changed = ledger_write.set_dividend_policy(db, code=code or None,
                                                           holding_id=hid or None, policy=pol)
            except ValueError as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            if not changed:
                return jsonify({"ok": False, "error": "未找到对应持仓（需提供 code 或 id）"}), 400
            label = "红利再投" if pol == "reinvest" else "现金分红"
            return jsonify({"ok": True, "changed": changed,
                            "commit_id": ledger_write.latest_commit_id(db),
                            "message": (f"{code} 的 {changed} 笔已改为{label}" if code
                                        else f"持仓ID={hid} 分红方式已改为{label}")})

        return jsonify({"ok": False, "error": "未知操作: %s" % (action or "(空)")}), 400
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "参数格式错误（金额/ID 需为数字）"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"操作失败: {e}"}), 500
    finally:
        db.close()
        _invalidate_caches("rebalance")     # 持仓变了 → 聚合缓存/总览/调仓建议都失效


@app.route("/api/holdings/rollback", methods=["POST"])
def api_holdings_rollback():
    """撤销最近一次写账本的操作（B-4b-1）。

    body: `{commit_id}`。
    只允许撤销**最近 `ledger_write.COMMIT_RETENTION` 条**内的提交，且不能重复撤销 ——
    撤销是"手滑兜底"，不是版本管理（允许回滚很久以前的，会把之后所有正确操作一起抹掉）。
    失败用 400 + 中文原因（界面直接显示）。
    """
    q = _api_req()
    cid = int(q.get("commit_id") or 0)
    if not cid:
        return jsonify({"ok": False, "error": "缺少 commit_id"}), 400
    db = get_db()
    try:
        r = ledger_write.rollback(db, cid)
        return jsonify({"ok": True, "data": r,
                        "message": "已撤销：%s" % (r.get("label") or "该次操作")})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": "撤销失败: %s" % e}), 500
    finally:
        db.close()
        _invalidate_caches("rebalance")


@app.route("/api/holdings/commits")
def api_holdings_commits():
    """最近的可撤销操作（只读）。前端写操作成功后用它取 `commit_id`。"""
    db = get_db()
    try:
        return jsonify({"ok": True, "data": {
            "retention": ledger_write.COMMIT_RETENTION,
            "commits": ledger_write.recent_commits(db, 10),
        }})
    finally:
        db.close()


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
        # 分红自动落账（设计稿 §7 第 4 步）：对账/新净值入库后顺带跑一次。
        # · 幂等（幂等键 `(holding_id, apply_date, kind)`）→ 可反复调用，不会重复入账；
        # · 只落**通过安全底线**的除息日，存疑的一律不落、只回报（用户 2026-09-16 决定：
        #   自动落账、不设人工确认 —— 所以"拒收条件"就是唯一的安全阀）。
        try:
            from src.analysis.dividend import auto_post_all
            div = auto_post_all(db)
        except Exception as e:
            div = {"posted": [], "suspects": [], "posted_n": 0, "posted_amount": 0.0,
                   "error": str(e)}
        changed = bool(r.get("settled_buys") or r.get("settled_sells") or div.get("posted_n"))
        if changed:
            _invalidate_caches("rebalance")
        return jsonify({"ok": True, "data": {**r, "dividend": div}, "changed": changed})
    except Exception as e:
        return jsonify({"ok": False, "error": f"对账失败: {e}"}), 500
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
        _invalidate_caches()     # T2.2：原参数 funds/funds_board_* 随死路由删除；净值变了 → 清聚合缓存


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
                return jsonify({"ok": False, "error": "未提供要修改的字段"}), 400
            ok = db.update_plan(pid, **fields)
            return jsonify({"ok": ok, "message": "计划已更新" if ok else "更新失败"})

        if action == "add_item":
            code = str(q.get("code") or "").strip()
            if not code:
                return jsonify({"ok": False, "error": "缺少基金代码"}), 400
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
                return jsonify({"ok": False, "error": "未提供要修改的字段"}), 400
            ok = db.update_plan_item(iid, **fields)
            return jsonify({"ok": ok, "message": "计划条目已更新" if ok else "未找到该条目"})

        if action == "delete_item":
            iid = int(q.get("item_id") or 0)
            ok = db.delete_plan_item(iid)
            return jsonify({"ok": ok, "message": "已删除计划条目" if ok else "未找到该条目"})

        if action == "delete_plan":
            ok = db.delete_plan(pid)
            return jsonify({"ok": ok, "message": "计划已删除" if ok else "删除失败"})

        return jsonify({"ok": False, "error": "未知操作: %s" % (action or "(空)")}), 400
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "参数格式错误（金额需为数字）"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"操作失败: {e}"}), 500
    finally:
        db.close()
        if request.method == "POST":
            _invalidate_caches("rebalance")     # 计划变了 → 总览/调仓建议失效（GET 读不失效）


# 2026-10-16 T2.2（删壳）：原 /api/funds/board（筛选池板块总榜）已删除。


@app.route("/api/explain")
def api_explain():
    """数据链路下钻：结论卡那五格 → 每格指回账户/库里的**真实产物**（只读，缓存 30 分钟）"""
    fresh = request.args.get("fresh") == "1"
    data, source = _cached_get("explain", _all_explain, ttl=EXPLAIN_TTL, fresh=fresh)
    return jsonify({"ok": True, "data": data, "source": source})


# 2026-10-16 T2.2（删壳）：原 `_compute_board_pool`（筛选池板块总榜）已删除 ——
# 它由占位 FundScreener 驱动，属旧命题产品面。


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
                return jsonify({"ok": False, "error": "缺少基金代码"}), 400
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
            return jsonify({"ok": False, "error": "未知操作: %s" % (action or "(空)")}), 400
        return jsonify({"ok": ok, "message": f"已完成定投操作 ID={pid}" if ok else f"未找到定投 ID={pid}"})
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "参数格式错误"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"操作失败: {e}"}), 500
    finally:
        db.close()
        if request.method == "POST":
            _invalidate_caches("rebalance")     # 定投执行/补录会新增持仓（GET 读不失效）



def _precompute_snapshots():
    """启动时后台预热快照：快照还新鲜就跳过，不与首屏请求抢资源。

    T2.2：原 jobs 还含 funds / rebalance（筛选池等重算，~110s）—— 随旧命题删除；
    只剩 overview，顺带把 `__dash__`（内容 = overview）也预热。
    """
    jobs = [("overview", _overview_compute), ("__dash__", _compute_dashboard)]
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


def _resolve_port() -> int:
    """解析监听端口（E-07：原先硬编码 5020 → 开不了第二个实例）。

    优先级：`--port=N` > 第 2 个位置参数（数字） > 环境变量 `QFA_PORT` > 5020。
    三种写法都支持，便于同时跑两份（例如把另一份指到 5021 做对照）。
    """
    import sys as _sys
    for a in _sys.argv[1:]:
        if a.startswith("--port="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                break
    if len(_sys.argv) > 2 and str(_sys.argv[2]).isdigit():
        return int(_sys.argv[2])
    try:
        return int(os.environ.get("QFA_PORT") or 5020)
    except ValueError:
        return 5020


def main():
    """启动 Flask 仪表盘（默认 http://localhost:5020）"""
    port = _resolve_port()
    if port != 5020:
        print("（端口来自 QFA_PORT / 命令行参数，已非默认 5020）")
    print("=" * 50)
    print("🚀 基金决策台")
    print("   http://localhost:%d" % port)
    print("=" * 50)

    # 预热分析快照（后台线程；已新鲜则跳过。T2.2：板块预热已随旧命题删除）
    threading.Thread(target=_precompute_snapshots, daemon=True).start()

    # 只监听回环地址：本系统无鉴权且管的是真实资金记录，绝不能被同网段其它设备读写。
    # threaded=True：允许 /api/all 计算时其它轻量端点并发加载
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

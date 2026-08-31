"""Flask 仪表盘"""

import sys, os, json, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from flask import Flask, jsonify, render_template
from src.data.database import Database
from src.analysis.fund_scorer import FundScreener
from src.analysis.thermometer import MarketThermometer
from src.analysis.portfolio import PortfolioTracker
from src.analysis.sentiment_monitor import SentimentMonitor
from src.analysis.rebalance_advisor import RebalanceAdvisor
from src.analysis.sector_analyzer import SectorAnalyzer
from src.analysis.historical_recommender import HistoricalRecommender
from src.analysis.investment_plan import get_plan, get_progress

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "fund_quant.db")
SECTORS_CACHE_FILE = os.path.join(BASE_DIR, "data", "sectors_cache.json")

# 板块数据缓存（启动时预计算）
_sectors_cache = None
_sectors_lock = threading.Lock()


def get_db():
    """创建数据库连接（每个请求独立连接）"""
    return Database(DB_PATH)


@app.route("/")
def index():
    """仪表盘首页（渲染前端模板）"""
    return render_template("dashboard.html")


@app.route("/api/all")
def api_all():
    """聚合 API：温度/基金池/持仓/消息面/调仓建议一次性返回"""
    db = get_db()
    result = {"temp": None, "funds": None, "portfolio": None, "sentiment": None, "plan": None}

    # 投资计划进度
    try:
        plan = get_plan()
        prog = get_progress(db)
        result["plan"] = {
            "name": plan["name"],
            "start_date": plan["start_date"],
            "total_capital": plan["total_capital"],
            "cash_reserve": plan["cash_reserve"],
            "total_invested": prog["total_invested"],
            "funds": prog["funds"],
        }
    except Exception as e:
        result["plan"] = {"error": str(e), "funds": []}

    # 温度
    try:
        t = MarketThermometer(db)
        result["temp"] = t.get_temperature()
    except Exception as e:
        result["temp"] = {"error": str(e)}

    # 基金筛选
    try:
        s = FundScreener(db)
        df = s.screen_funds(max_results=40)
        funds = []
        for _, row in df.iterrows():
            m = row.get("metrics", {})
            nav_trend = _get_nav_trend(db, row["fund_code"])
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
                "nav_trend": nav_trend,
            })
        summary = s.get_pool_summary(df)
        result["funds"] = {"funds": funds, "summary": summary}
    except Exception as e:
        result["funds"] = {"error": str(e), "funds": [], "summary": {}}

    # 持仓
    try:
        p = PortfolioTracker(db)
        data = p.get_portfolio_summary()
        result["portfolio"] = {
            "has_holdings": data.get("has_holdings", False),
            "total_invested": data.get("total_invested", 0),
            "total_market_value": data.get("total_market_value", 0),
            "total_pnl": data.get("total_pnl", 0),
            "total_return_pct": data.get("total_return_pct", 0),
            "alloc": data.get("asset_allocation", {}),
            "holdings": [{
                "id": h.get("holding_id"), "code": h.get("fund_code"),
                "name": h.get("fund_name"), "buy_date": h.get("buy_date"),
                "buy_amount": h.get("buy_amount"),
                "current_value": h.get("current_value"),
                "pnl": h.get("pnl"), "pnl_pct": h.get("pnl_pct"),
                "days_held": h.get("days_held"),
            } for h in data.get("holdings_detail", [])],
        }
    except Exception as e:
        result["portfolio"] = {"error": str(e), "has_holdings": False, "holdings": []}

    db.close()

    # 消息面
    try:
        m = SentimentMonitor()
        codes = [h["code"] for h in result.get("portfolio", {}).get("holdings", [])]
        sd = m.full_scan(holding_codes=codes)
        result["sentiment"] = {
            "all_clear": sd.get("all_clear", True),
            "signal_summary": sd.get("signal_summary", ""),
            "alerts": [{
                "level": a.level, "category": a.category,
                "title": a.title, "detail": a.detail,
                "timestamp": a.timestamp,
            } for a in sd.get("alerts", [])],
        }
    except Exception as e:
        result["sentiment"] = {"error": str(e), "all_clear": True, "alerts": [], "signal_summary": "暂不可用"}

    # 调仓建议
    try:
        holdings = result.get("portfolio", {}).get("holdings", [])
        if holdings:
            db2 = get_db()
            advisor = RebalanceAdvisor(db2)
            total_invested = sum(h["buy_amount"] for h in holdings)
            rb = advisor.analyze(total_capital=total_invested * 1.1)
            result["rebalance"] = {
                "need_rebalance": rb["need_rebalance"],
                "current_equity_pct": rb["current_equity_pct"],
                "target_equity_pct": rb["target_equity_pct"],
                "gap_pct": rb["gap_pct"],
                "summary": rb["summary"],
                "instructions": rb["instructions"],
            }
            db2.close()
        else:
            result["rebalance"] = {"need_rebalance": False, "instructions": [], "summary": {"verdict": "暂无持仓"}}
    except Exception as e:
        result["rebalance"] = {"error": str(e), "instructions": [], "summary": {"verdict": "分析失败"}}

    return jsonify({"ok": True, "data": result})


def _get_nav_trend(db: Database, fund_code: str, days: int = 30) -> list:
    """获取最近N天的净值序列，用于前端迷你趋势图"""
    navs = db.get_fund_nav(fund_code)
    if not navs or len(navs) < 5:
        return []
    recent = navs[-days:]
    return [
        {"date": str(r.get("nav_date", ""))[-5:],  # MM-DD
         "nav": round(float(r.get("unit_nav", 0)), 4)}
        for r in recent
    ]


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


def _precompute_sectors():
    """后台预计算板块数据（启动时调用）"""
    global _sectors_cache
    print("  ⏳ 预计算板块排名（后台约8秒）...")
    try:
        a = SectorAnalyzer()
        result = a.analyze()
        if "error" not in result:
            data = _format_sectors(result)
            with _sectors_lock:
                _sectors_cache = data
            _save_sectors_cache(data)
            print(f"  ✅ 板块数据已就绪 ({len(data['sectors'])}个行业)")
        else:
            print(f"  ⚠️ 板块预计算失败: {result['error']}")
    except Exception as e:
        print(f"  ⚠️ 板块预计算异常: {e}")


@app.route("/api/sectors")
def api_sectors():
    """板块分析 — 从缓存读取，秒级响应"""
    global _sectors_cache

    # 优先用内存缓存
    with _sectors_lock:
        if _sectors_cache is not None:
            return jsonify({"ok": True, "data": _sectors_cache, "source": "memory"})

    # 次优先读文件缓存
    if os.path.exists(SECTORS_CACHE_FILE):
        try:
            with open(SECTORS_CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            with _sectors_lock:
                _sectors_cache = cached
            return jsonify({"ok": True, "data": cached, "source": "file"})
        except Exception:
            pass

    # 兜底：实时计算（慢，但至少不空白）
    try:
        a = SectorAnalyzer()
        result = a.analyze()
        if "error" in result:
            return jsonify({"ok": False, "error": result["error"]})

        data = _format_sectors(result)
        with _sectors_lock:
            _sectors_cache = data
        # 异步写文件
        threading.Thread(target=_save_sectors_cache, args=(data,), daemon=True).start()
        return jsonify({"ok": True, "data": data, "source": "live"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


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


def main():
    """启动 Flask 仪表盘（http://localhost:5020）"""
    print("=" * 50)
    print("🚀 量化基金仪表盘")
    print("   http://localhost:5020")
    print("=" * 50)

    # 预计算板块数据（后台线程，不阻塞启动）
    threading.Thread(target=_precompute_sectors, daemon=True).start()

    app.run(host="0.0.0.0", port=5020, debug=False)


if __name__ == "__main__":
    main()

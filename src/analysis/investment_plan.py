"""
投资计划跟踪模块。

计划来源（F-01 外置后）：数据库 investment_plans 表 → 本地配置
`config/investment_plan.local.yaml` → 内置示例（见文件下方 PLAN_NAME 区块）。

说明:
- 2026-09-01 剔除纳斯达克（QDII 限购且分散效果不佳）：
  已买入的 70 元作为既有持仓持有，不再加仓，额度转入现金底仓（150 → 280）。
- 设计原则:
  - 市场温度 60° 偏热 → 不一次性买满，分4笔建仓
  - 黄金(与股市低相关)先全买，科技(回撤32%)最后加
  - 每只基金的目标金额/比例 + 分笔建议，对照实际持仓算进度
"""

import json
import os
from typing import Optional

from ..data.database import Database

# =================================================================
# 计划定义
# =================================================================

# =================================================================
# 计划定义 —— **已外置到本地配置**（F-01，2026-09-25）
# -----------------------------------------------------------------
# 读取优先级：① 数据库 investment_plans 表（Web 上维护，见 get_plan）
#             ② 本地配置 config/investment_plan.local.yaml（**不入版本库**）
#             ③ 内置示例计划（SAMPLE_*）—— 仅前两者都没有时使用
#
# 为什么要外置：原先把计划（真实基金代码 / 金额 / 日期）硬编码在本文件里，
# 属于个人数据、不该进版本库（README §数据与隐私 有同样建议）。
# 新用户请复制 config/investment_plan.local.example.yaml →
# config/investment_plan.local.yaml 后填自己的计划（该文件已 gitignore）。
# =================================================================

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_PLAN_PATH = os.path.join(ROOT_DIR, "config", "investment_plan.local.yaml")
EXAMPLE_PLAN_PATH = os.path.join(ROOT_DIR, "config", "investment_plan.local.example.yaml")


def _load_local_plan() -> Optional[dict]:
    """读本地个人计划；文件不存在或解析失败一律返回 None（不抛，退到示例计划）。"""
    if not os.path.exists(LOCAL_PLAN_PATH):
        return None
    try:
        import yaml
        with open(LOCAL_PLAN_PATH, encoding="utf-8") as f:
            d = yaml.safe_load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


# 内置**示例**计划：只在"无数据库计划、且无本地配置"时使用。
# ⚠️ 这是占位示例，不是任何人的真实计划；别把它当成默认投资建议。
SAMPLE_PLAN_NAME = "示例计划（未配置）"
SAMPLE_START_DATE = "2026-01-01"
SAMPLE_TOTAL_CAPITAL = 1000.0
SAMPLE_CASH_RESERVE = 0.0
SAMPLE_FUNDS = [
    {
        "code": "000001", "name": "示例基金（请替换）", "role": "示例角色",
        "target_amount": 1000.0, "target_pct": 100.0,
        "tranches": [{"date": "2026-01-01", "amount": 1000.0}],
    },
]

_local_plan = _load_local_plan()
#: True = 当前用的是**内置示例**（既没配本地文件、DB 里也没计划）→ 调用方应显式声明
IS_SAMPLE_PLAN = _local_plan is None

PLAN_NAME = (_local_plan or {}).get("plan_name") or SAMPLE_PLAN_NAME
START_DATE = (_local_plan or {}).get("start_date") or SAMPLE_START_DATE
TOTAL_CAPITAL = (_local_plan or {}).get("total_capital") or SAMPLE_TOTAL_CAPITAL
CASH_RESERVE = (_local_plan or {}).get("cash_reserve") or SAMPLE_CASH_RESERVE
FUNDS = (_local_plan or {}).get("funds") or SAMPLE_FUNDS

# 温度联动规则
TEMP_RULES = {
    "cold": "温度<40°：把现金280元优先补进中证500和银河创新（补足目标后剩余留存）",
    "cool": "温度<55°：可提前执行下一笔建仓",
    "normal": "温度55-75°：按计划节奏正常定投",
    "hot": "温度>75°：暂停加仓，考虑减配银河创新",
}


# =================================================================
# 进度计算
# =================================================================

def _fallback_plan() -> dict:
    """硬编码计划（仅用于首次 seed 或库不可用时的兜底）"""
    return {
        "name": PLAN_NAME,
        "start_date": START_DATE,
        "total_capital": TOTAL_CAPITAL,
        "cash_reserve": CASH_RESERVE,
        "funds": FUNDS,
        "temp_rules": TEMP_RULES,
    }


def ensure_seed(db: Database) -> Optional[int]:
    """把硬编码计划一次性写入库（若库中已有计划则不动作）"""
    try:
        return db.seed_plan_if_empty(
            {"name": PLAN_NAME, "goal": "分4笔建仓，温度联动加减仓", "total_capital": TOTAL_CAPITAL,
             "cash_reserve": CASH_RESERVE, "start_date": START_DATE, "horizon": "6个月",
             "risk_pref": "稳健偏平衡", "notes": "2026-09-01 剔除纳斯达克，额度转入现金底仓"},
            [{"fund_code": f["code"], "fund_name": f["name"], "role": f.get("role"),
              "target_amount": f.get("target_amount"), "target_pct": f.get("target_pct"),
              "dca_daily": f.get("dca_daily"), "tranches": json.dumps(f.get("tranches", []), ensure_ascii=False)}
             for f in FUNDS])
    except Exception:
        return None


def get_plan(db: Database = None) -> dict:
    """返回计划定义：优先读库（可维护），无则回退硬编码常量。"""
    if db is not None:
        try:
            p = db.get_active_plan()
            if p:
                funds = []
                for it in p.get("items", []):
                    try:
                        tr = json.loads(it.get("tranches") or "[]")
                    except Exception:
                        tr = []
                    funds.append({
                        "code": it.get("fund_code"), "name": it.get("fund_name"),
                        "role": it.get("role"), "target_amount": it.get("target_amount"),
                        "target_pct": it.get("target_pct"), "tranches": tr,
                        "dca_daily": it.get("dca_daily"), "item_id": it.get("id"),
                    })
                return {
                    "id": p["id"], "name": p.get("name"), "goal": p.get("goal"),
                    "start_date": p.get("start_date"), "total_capital": p.get("total_capital"),
                    "cash_reserve": p.get("cash_reserve"), "horizon": p.get("horizon"),
                    "risk_pref": p.get("risk_pref"), "notes": p.get("notes"),
                    "funds": funds, "temp_rules": TEMP_RULES,
                }
        except Exception:
            pass
    return _fallback_plan()


def get_invested_by_code(db: Database) -> dict:
    """按基金代码汇总已投金额（含待确认买入；未确认不代表钱没花）"""
    cur = db.conn.cursor()
    cur.execute("""
        SELECT fund_code, SUM(buy_amount) as invested
        FROM holdings WHERE status IN ('holding','pending_confirm')
        GROUP BY fund_code
    """)
    return {r["fund_code"]: r["invested"] for r in cur.fetchall()}


def get_progress(db: Database) -> dict:
    """
    对照计划计算当前进度。

    Returns:
        dict: {funds: [{code,name,role,target,invested,remaining,pct,next}], ...}
    """
    plan = get_plan(db)
    plan_funds = plan.get("funds") or FUNDS
    cash_reserve = plan.get("cash_reserve") or CASH_RESERVE
    total_capital = plan.get("total_capital") or TOTAL_CAPITAL
    invested_map = get_invested_by_code(db)

    funds_progress = []
    total_invested = 0.0

    for f in plan_funds:
        target = f.get("target_amount") or 0.0
        invested = invested_map.get(f.get("code"), 0.0)
        remaining = max(0.0, target - invested)
        pct = min(100.0, invested / target * 100) if target > 0 else 0

        # 找下一笔建议（还没执行到的 tranche）
        dca_daily = f.get("dca_daily")
        if dca_daily and remaining > 0:
            # 每日限购基金：每天只能买 dca_daily 元
            days_left = int(round(remaining / dca_daily))
            next_tranche = {
                "date": "每日",
                "amount": dca_daily,
                "note": f"限购，每日定投¥{dca_daily:.0f}，还需约{days_left}个交易日",
            }
        else:
            next_tranche = None
            cumulative = 0.0
            for t in (f.get("tranches") or []):
                cumulative += t.get("amount", 0)
                if invested < cumulative - 0.01:  # 还没买够这一笔
                    next_tranche = {"date": t.get("date"), "amount": round(cumulative - invested, 0)}
                    break

        total_invested += invested
        funds_progress.append({
            "code": f.get("code"),
            "name": f.get("name"),
            "role": f.get("role"),
            "target": target,
            "target_pct": f.get("target_pct"),
            "invested": round(invested, 2),
            "remaining": round(remaining, 2),
            "progress_pct": round(pct, 1),
            "next": next_tranche,
        })

    return {
        "funds": funds_progress,
        "total_invested": round(total_invested, 2),
        "total_target": total_capital,
        "cash_reserve": cash_reserve,
        "remaining_total": round(total_capital - total_invested - cash_reserve, 2),
    }

"""交易成本口径 —— 单一实现（数据源扩展计划书 §8.5「成本模型是 C 类份额特化」）。

问题（原文）
------------
回测/模拟用全局常数 `ONE_WAY_COST = 0.5%`（自称"场外 C 类/ETF 联接口径"），
对 A 类（前端 1~1.5%）、货币（0）、场内 ETF（佣金）、FOF（双重收费）都不成立。

实测（2026-09-27，21,324 只有真实 `fund_info.purchase_fee`，单位 %）
------------------------------------------------------------------
| 类型 | 真实前端申购费中位 | 0.5% 假设 | 偏差 |
|---|---|---|---|
| 指数 | 0.030% | 0.5% | **高估 17×** |
| 债券 | 0.050% | 0.5% | **高估 10×** |
| FOF  | 0.080% | 0.5% | 高估 6× |
| 混合 | 0.100% | 0.5% | 高估 5× |
| 股票 | 0.150% | 0.5% | 高估 3.3× |
| QDII | 0.150% | 0.5% | 高估 3.3× |
| 货币 | 0.000% | 0.5% | — |

按份额类别：**C 类中位 0.000%**（用户 100% 持仓为 C 类）、A 类中位 0.120%、E/Y/I 类 0.000%。
即 `ONE_WAY_COST` 对**所有类型都是高估**（保守缓冲，不是实际成本）。

本模块的定位
------------
给出**按真实费率**的成本口径，供需要精确成本的场景使用。
`factor_test.ONE_WAY_COST` / `vol_predictor.ONE_WAY_COST` 两个全局常数**保留不动**
（改数值会翻动全部历史回测/模拟报告），但在原处标注实测偏差 + "仅作保守缓冲"的定位。

口径边界（重要，避免双算）
--------------------------
- 买入显性成本 = **前端申购费**（`fund_info.purchase_fee`）
- 卖出显性成本 = **赎回费**（`portfolio.redeem_fee_rate`：<7 天 1.5%，≥7 天按基金自身档位）
- **不含**管理费/托管费/销售服务费 —— 它们**已从每日净值里扣除**，再计一次就是双算
  （这正是计划书阶段 0 T0-0 修掉的那个 bug）
- **不含**冲击成本/买卖价差 —— 场外按净值成交，无此成本
"""
from __future__ import annotations

from .portfolio import share_class

# ── 兜底值（只在"既没采到真实费率、又推断不出份额类别"时使用）──────────────
# 取实测 A 类中位 0.120% 与未识别类 0.150% 中**偏保守**者，宁可高估不可低估。
DEFAULT_PURCHASE_FEE_PCT = 0.15

# 全局保守缓冲（与 ONE_WAY_COST 同值，供"完全无数据"场景显式引用）
ASSUMED_ONE_WAY_PCT = 0.5

# 不收前端申购费的份额类别（改收销售服务费，已从净值扣）
NO_FRONT_FEE_CLASSES = frozenset({"C", "E", "I", "Y"})


def _num_pct(v):
    """把 `fund_info.purchase_fee` 解析成数值（单位 %）。解析不出返回 None。

    阶段 1 采集后该字段是**数值**（`0.15` 表示 0.15%），但历史数据里可能存在字符串
    （如 `"0.15%"`）→ 一并容错；`'---'` / 空 / 非法 → None（**不猜**）。
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "").replace("（每年）", "").strip()
    if not s or s in ("---", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def purchase_cost_pct(info: dict) -> tuple:
    """前端申购费率 `(百分数, 依据说明)`。

    优先级：**实测 `purchase_fee`** → 按份额类别（C/E/I/Y = 0）→ 保守默认。
    依据说明会如实写明用了哪一档（缺失不得静默，铁律 5）。
    """
    info = info or {}
    v = _num_pct(info.get("purchase_fee"))
    if v is not None:
        return v, "实测 purchase_fee=%s%%" % (("%g" % v))
    cls = share_class(info.get("fund_name") or "")
    if cls in NO_FRONT_FEE_CLASSES:
        return 0.0, "%s 类份额无前端申购费（采集缺失，按份额类别推断）" % cls
    return DEFAULT_PURCHASE_FEE_PCT, "无采集费率、份额类别=%s → 保守默认 %g%%" % (
        cls or "未知", DEFAULT_PURCHASE_FEE_PCT)


def redeem_cost_pct(info: dict, held_days: int) -> tuple:
    """赎回费率 `(百分数, 依据说明)`。真源是 `portfolio.redeem_fee_rate`（不在此重写）。"""
    from .portfolio import redeem_fee_rate          # 局部导入避免模块级循环
    r = redeem_fee_rate((info or {}).get("redeem_fee"), int(held_days))
    if held_days < 7:
        why = "持有 %d 天 <7 天 → 惩罚档（基金自身档位与 1.5%% 孰高）" % held_days
        return round(r * 100, 4), why
    return round(r * 100, 4), "持有 %d 天 ≥7 天 → 按基金档位（多数为 0）" % held_days


def one_way_cost_pct(info: dict, held_days: int = 365) -> tuple:
    """一次完整往返（买入 + 持有 `held_days` 后卖出）的**单边平均**成本 `(百分数, 依据)`。

    单边定义：往返成本 ÷ 2。用于替代 `ONE_WAY_COST` 式"每单位换手成本"。
    """
    buy, why_b = purchase_cost_pct(info)
    sell, why_s = redeem_cost_pct(info, held_days)
    return round((buy + sell) / 2.0, 4), "%s；%s" % (why_b, why_s)


def assumption_note() -> str:
    """给报告用的一句话：说明全局 0.5% 假设的定位（供前端/报告如实标注）。"""
    return ("回测/模拟统一按单边 %.1f%% 计成本 —— 那是**保守缓冲**（场外 C 类口径），"
            "实测真实前端申购费中位仅 0.03%%~0.15%%（C 类 0%%），故回测净收益被**系统性低估**、"
            "结论偏保守。需精确成本时用 `trading_cost.one_way_cost_pct(fund_info, 持有天数)`。"
            % ASSUMED_ONE_WAY_PCT)

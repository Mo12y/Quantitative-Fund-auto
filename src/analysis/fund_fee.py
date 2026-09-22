"""TER（总运作费率）—— **单一实现**。

口径（晨星 / 美国 SEC 的 Total Expense Ratio）：
    **TER = 管理费 + 托管费 + 销售服务费 + 其他运作支出**
（来源：晨星中国《如何衡量基金费在投资成本中的比重》；该文明确**批评**只披露
"管理费+托管费"的做法 —— 见 docs/参照系接入执行报告 §8.5）

为什么值得单独成模块：
1. 晨星 2016 landmark 研究（Russel Kinnel）测过的**所有变量里，费率对未来业绩的预测力最强**；
   2025 复现（20 年）显示"最便宜组→最贵组"几乎是一条完美阶梯。所以这一维不能算错。
2. 它与"管理费"不是一回事：`fund_info.mgt_fee` 历史上装的是**申购手续费**（已正名，见
   scripts/migrate_mgt_fee_to_purchase_fee.py）。申购费受**平台折扣**影响、是**交易费用**，
   不属于运作费用，**不得混进 TER**。

缺失处理（铁律 5）：
- 管理费 / 托管费是**每只基金都必须有**的运作费用 → 缺任一项则 **TER 不可算**（返回 None + 原因），
  绝不用 0 顶替（0 会把 TER 系统性算低，这正是"托管费恒为 0"踩过的坑）。
- 销售服务费是**可选的**（A 类通常不收，源里标 "---"）→ 缺失视为 0，因为源已明确表示"没有"。
"""
from __future__ import annotations

TER_FIELDS = ("mgt_fee", "custodian_fee", "sales_service_fee")

# 缺任一项就不可算（必收项）
REQUIRED = ("mgt_fee", "custodian_fee")

# 给人看的字段名（machine 名 → 中文）
FIELD_LABELS = {"mgt_fee": "管理费", "custodian_fee": "托管费", "sales_service_fee": "销售服务费"}


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def missing_text(missing) -> str:
    return "、".join(FIELD_LABELS.get(m, m) for m in missing)


def compute_ter(info: dict) -> tuple:
    """从 fund_info 行计算 TER。返回 **(ter, missing_list)**。

    · ter is None  → 数据不完整，`missing` 列出缺哪几项（调用方必须**显式声明**）
    · 销售服务费缺 → 计入 0（源里 "---" = 确实没有），不列为 missing
    """
    if not info:
        return None, list(REQUIRED)
    vals = {f: _num(info.get(f)) for f in TER_FIELDS}
    missing = [f for f in REQUIRED if vals[f] is None]
    if missing:
        return None, missing
    ter = vals["mgt_fee"] + vals["custodian_fee"] + (vals["sales_service_fee"] or 0.0)
    return round(ter, 4), []


def ter_text(info: dict) -> str:
    """给人看的 TER 文本（缺失时说明缺什么，不显示 0）。"""
    ter, missing = compute_ter(info)
    if ter is None:
        return "TER 不可算（缺 %s）" % missing_text(missing)
    return "TER %.2f%%" % ter

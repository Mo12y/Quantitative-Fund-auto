"""
基金 → 行业板块 关键词映射（轻量、无额外数据源）。

用途：
1. 总览页“行业占比”；
2. 筛选池按板块分榜（每板块前 N）。

说明：这是**基于基金名称关键词**的近似归类（设计决策，见 docs/前端优化设计方案.md §8），
未命中的基金归入“其他/宽基”。后续可替换为持仓/指数成分归类而不影响调用方。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

# 板块 -> 关键词（顺序即匹配优先级：越具体的板块放越前）
BOARDS: List[Tuple[str, Tuple[str, ...]]] = [
    ("半导体芯片", ("半导体", "芯片", "集成电路", "IC", "存储")),
    ("人工智能", ("人工智能", "AI", "算力", "算法", "云计算", "大数据")),
    ("通信", ("通信", "5G", "光模块", "光通信", "运营商")),
    ("电网电力", ("电网", "电力", "特高压", "储能", "绿电")),
    ("机器人智造", ("机器人", "智能制造", "工业母机", "机械", "高端制造")),
    ("新能源车", ("新能源车", "新能源汽车", "汽车", "锂电池", "电池", "智能汽车")),
    ("光伏风电", ("光伏", "风电", "太阳能", "清洁能源")),
    ("军工国防", ("军工", "国防", "航天", "航空")),
    ("医药医疗", ("医药", "医疗", "生物", "创新药", "医疗器械", "疫苗", "健康")),
    ("消费食品", ("消费", "食品", "饮料", "白酒", "家电", "旅游", "零售", "农业", "养殖", "畜牧")),
    ("金融地产", ("金融", "银行", "证券", "券商", "保险", "地产", "房地产")),
    # 黄金/避险 必须排在"周期资源"之前：两者都含"黄金"关键词，按顺序先命中者归属。
    # 旧顺序下"周期资源"在前，任何含"黄金"的基金都被归到周期资源，黄金板块几乎不可达。
    ("黄金对冲", ("黄金", "贵金属", "金ETF", "上海金")),
    ("周期资源", ("有色", "煤炭", "钢铁", "石油", "化工", "材料", "资源")),
    ("传媒游戏", ("传媒", "游戏", "文化", "影视", "元宇宙")),
    ("科技综合", ("科技", "信息技术", "计算机", "软件", "电子", "创新")),
    ("宽基指数", ("沪深300", "中证500", "中证1000", "上证50", "创业板", "科创", "MSCI",
                  "A50", "A100", "全指", "红利", "价值", "成长", "均衡")),
]

_OTHER = "其他"
#: 债务 / 货币类的固定板块名（**结构化字段派生**，不属于 `BOARDS` 的名称关键词表）
BOND_BOARD = "债券固收"
CASH_BOARD = "货币现金"

#: 一级分类：`fund_type` 的**前缀**（结构化字段）
_MONEY_TYPE_PREFIX = ("货币型",)
_DEBT_TYPE_PREFIX = ("债券型", "指数型-固收", "混合型-偏债", "QDII-纯债", "QDII-混合债")

#: 名称判据：识别债务/货币类。**必须在权益主题之前判** ——
#: 这类基金的名字里常含权益关键词：「中债0-3年**政策性金融**债」「**银行**间中高等级信用债」
#: 「中银**证券**安进债券」，纯子串匹配会把它们误判成权益主题。
#: 2026-10-04 实测：9,082 只债务类里 **199 只**被误分进权益主题（**177 只落「金融地产」**）。
_DEBT_KW = ("债", "固收", "货币", "利率", "存单", "短融", "票据", "存款", "现金", "理财")


def classify(name: str, fund_type: str | None = None) -> str:
    """按**类型字段 ∪ 名称**判断板块；未命中返回「其他」。

    判据是「**任一命中即认**」，不是"类型优先"——因为**两个信号都会漏**，实测（2026-10-04）：

    | 漏法 | 实例 | 只数 |
    |---|---|---|
    | 名称漏 | `QDII-纯债` / `QDII-混合债`（名字里没「债」字） | 77 |
    | **类型漏** | `指数型-股票`（中证国债类 ETF）、`FOF-稳健型`（名字带「债」） | 179 |

    所以只信一个都会误分：只用名称 → 199 只落权益主题；只信类型 → **260 只**（更差）。
    取并集后 → **0 只**。

    ⚠️ 并集**不会误伤权益**：「银行ETF」「证券公司指数」这类名字里没有债务关键词，
    仍正常归「金融地产」（有专测钉住）。
    """
    t = str(fund_type or "").strip()
    s = str(name or "")

    if t.startswith(_MONEY_TYPE_PREFIX) or "货币" in s:
        return CASH_BOARD
    if t.startswith(_DEBT_TYPE_PREFIX) or any(k in s for k in _DEBT_KW):
        return BOND_BOARD

    for board, kws in BOARDS:
        for kw in kws:
            if kw in s:
                return board
    return _OTHER


def board_of_funds(holdings: Iterable[dict], name_key: str = "fund_name",
                   type_key: str = "fund_type") -> Dict[str, float]:
    """按板块统计**金额**（传入带金额的记录，金额键自动尝试 amount/current_value/buy_amount）。

    类型字段自动尝试 `type_key` 与 `"type"`（Web 层的池子把类型放在 `"type"` 键下）；
    `holdings` 表（`SELECT *`）没有类型列 → 此时 `classify` 走名称兜底。
    """
    out: Dict[str, float] = {}
    for h in holdings or []:
        amt = h.get("amount")
        if amt is None:
            amt = h.get("current_value")
        if amt is None:
            amt = h.get("buy_amount") or 0
        ftype = h.get(type_key) or h.get("type") or ""
        board = classify(h.get(name_key) or h.get("name") or "", ftype)
        out[board] = out.get(board, 0.0) + float(amt or 0)
    return out


def board_allocation(holdings: Iterable[dict], name_key: str = "fund_name",
                     type_key: str = "fund_type") -> Dict[str, float]:
    """按板块统计**占比(%)**（金额占比，保留 1 位小数）。"""
    amts = board_of_funds(holdings, name_key, type_key)
    total = sum(amts.values())
    if total <= 0:
        return {}
    return {k: round(v / total * 100, 1) for k, v in sorted(amts.items(), key=lambda x: -x[1])}

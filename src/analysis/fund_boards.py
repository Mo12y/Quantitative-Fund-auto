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


def classify(name: str) -> str:
    """按基金名称判断板块；未命中返回“其他”。"""
    s = str(name or "")
    for board, kws in BOARDS:
        for kw in kws:
            if kw in s:
                return board
    return _OTHER


def board_of_funds(holdings: Iterable[dict], name_key: str = "fund_name") -> Dict[str, float]:
    """按板块统计**金额**（传入带金额的记录，金额键自动尝试 amount/current_value/buy_amount）。"""
    out: Dict[str, float] = {}
    for h in holdings or []:
        amt = h.get("amount")
        if amt is None:
            amt = h.get("current_value")
        if amt is None:
            amt = h.get("buy_amount") or 0
        board = classify(h.get(name_key) or h.get("name") or "")
        out[board] = out.get(board, 0.0) + float(amt or 0)
    return out


def board_allocation(holdings: Iterable[dict], name_key: str = "fund_name") -> Dict[str, float]:
    """按板块统计**占比(%)**（金额占比，保留 1 位小数）。"""
    amts = board_of_funds(holdings, name_key)
    total = sum(amts.values())
    if total <= 0:
        return {}
    return {k: round(v / total * 100, 1) for k, v in sorted(amts.items(), key=lambda x: -x[1])}

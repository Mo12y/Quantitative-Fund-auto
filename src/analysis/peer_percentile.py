"""同类百分位参照系 —— **可复用的单一实现**（任务书 B1）。

为什么有这个模块
----------------
原先「组内分布 → 每只基金的百分位」整套逻辑内嵌在 `scripts/fund_percentile.py`
的 `main()` 里，只有脚本能用。而批次 A 的 `momentum_warning` 要改成「组内 P90」、
批次 B 的 API 要吐 `percentiles`/`group`/`group_n`/`nav_asof` —— 两处都需要同一份计算。
按 SSOT 铁律（本项目已因「两处各写一遍」栽过跟头），把它下沉到这里，
脚本与 API 都从这里取，**不许再各自实现一次**。

口径来源
--------
全部来自 `scripts/calibrate_thresholds.py`（项目声明的 SSOT）：
`TYPE_GROUPS / TYPE2GROUP / METRICS / LABEL / grade / load_navs / metrics`。
本模块只做**组装与查询**，不重新定义分组、不重写净值口径、不重写指标公式。

重量级 vs 轻量级
----------------
`build_distributions()` 要扫全库净值（万级基金，分钟量级），**不能**放在请求路径里。
所以提供：
  · `build_distributions()` → 重算（脚本用）
  · `save_cache()/load_cache()` → 落 JSON（`data/peer_distributions.json`，运行时产物）
  · `threshold(cache, group, metric, pct)` / `percentile(cache, ...)` → 纯查表（API 用）
没有缓存时**不猜**：返回 None，由调用方如实声明「参照系未构建」（铁律 5）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_SCRIPTS, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── SSOT 直引（不重写）──────────────────────────────────────────────
from calibrate_thresholds import (          # noqa: E402
    DB_PATH, LABEL, METRICS, MIN_DAYS_SPARSE, MIN_N_FULL, MIN_SPAN_YEARS,
    TYPE2GROUP, TYPE_GROUPS, grade, load_navs, metrics,
)

CACHE_PATH = os.path.join(_ROOT, "data", "peer_distributions.json")
CACHE_VERSION = 1
DEFAULT_MIN_DAYS = 756                       # ≈3 年，与 fund_percentile 默认一致

# 方向：True = 越大越好，False = 越小越好，None = 中性（只作"位置"展示）
# （原在 scripts/fund_percentile.py，现为 SSOT 一处）
DIRECTION = {
    "annual_return": True,
    "ann_vol": False,
    "max_drawdown_1y": False,
    "momentum_3m": None,
    "sharpe": True,
}

# 综合分只用**相互独立**的两维（实测 夏普↔回撤 r=-0.04；收益↔夏普 r=0.97 冗余）
WEIGHT = {"sharpe": 0.6, "max_drawdown_1y": 0.4}
DISPLAY_ONLY = ["annual_return", "ann_vol", "momentum_3m"]

# 落缓存与查表用的分位网格
GRID = (10, 25, 50, 75, 90, 95)


def group_of(fund_type: str) -> str | None:
    """基金类型 → 参照系组别。未映射返回 None（调用方**必须**显式声明，不得静默丢弃）。"""
    return TYPE2GROUP.get(str(fund_type or ""))


def group_names() -> list:
    return [g for g, _ in TYPE_GROUPS]


def pct_of(arr, v, higher_better) -> float | None:
    """v 在 arr 中的百分位（0-100）。higher_better=False 时反转，使"好"=高分。"""
    if arr.size == 0 or not np.isfinite(v):
        return None
    p = float((arr <= v).mean() * 100)
    return p if higher_better else 100.0 - p


def build_group_arrays(min_days: int = DEFAULT_MIN_DAYS, db_path: str = None,
                       conn=None) -> tuple:
    """扫库、按组装原始指标数组。**唯一的"分组 + 指标"组装点**。

    Returns (groups, unmapped, conn_owned)
      groups   = {group: [ {metric: value, "_code":.., "_name":.., "_type":..}, ... ]}
      unmapped = {fund_type: count}   ← 调用方**必须打印**，不得静默丢弃
      conn_owned = 是否由本函数创建连接（True 需调用方收益后关闭；惰性流迭代完才能关）
    """
    owned = conn is None
    if owned:
        conn = sqlite3.connect("file:%s?mode=ro" % (db_path or DB_PATH), uri=True)
    fi = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_type from fund_info")}
    nm = {r[0]: (r[1] or "") for r in conn.execute("select fund_code, fund_name from fund_info")}
    navs = load_navs(conn, min_days)
    # 注意：navs 是惰性流，迭代期间不能 conn.close()

    groups, unmapped = {}, {}
    for code, (d, v) in navs.items():
        g = group_of(fi.get(code, ""))
        if not g:
            t = fi.get(code, "") or "(未知类型)"
            unmapped[t] = unmapped.get(t, 0) + 1
            continue
        m = metrics(v, d)
        if m:
            m["_code"] = code
            m["_name"] = nm.get(code, "")
            m["_type"] = fi.get(code, "")
            groups.setdefault(g, []).append(m)
    return groups, unmapped, (conn if owned else None)


def build_distributions(min_days: int = DEFAULT_MIN_DAYS, db_path: str = None) -> dict:
    """按组装分布 → 落缓存用的分位网格。**重**，只在脚本/离线刷新时调用。

    Returns {generated_at, min_days, unmapped_types, groups:{g:{n,quality_level,grid}}}
    """
    groups, unmapped, conn = build_group_arrays(min_days, db_path)
    if conn is not None:
        conn.close()

    out = {"generated_at": time.time(), "min_days": int(min_days),
           "unmapped_types": unmapped, "groups": {}}
    for g, rows in groups.items():
        n = len(rows)
        lvl, _ = grade(n)
        grid = {}
        for k in METRICS:
            arr = np.array([r[k] for r in rows], dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size == 0:
                continue
            grid[k] = {("p%d" % q): float(np.percentile(arr, q)) for q in GRID}
        out["groups"][g] = {"n": n, "quality_level": lvl, "grid": grid}
    return out


def save_cache(dist: dict, path: str = None) -> str:
    p = path or CACHE_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    payload = dict(dist)
    payload["version"] = CACHE_VERSION
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return p


def load_cache(path: str = None) -> dict | None:
    """读缓存。不存在/版本不符/损坏 → None（**不猜**，由调用方声明）。"""
    p = path or CACHE_PATH
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if d.get("version") != CACHE_VERSION or "groups" not in d:
        return None
    return d


def group_entry(cache: dict, group: str) -> dict | None:
    return ((cache or {}).get("groups") or {}).get(group)


def threshold(cache: dict, group: str, metric: str, pct: int) -> float | None:
    """查「某组某指标的 P{pct}」。样本等级不足（insufficient）时返回 None。"""
    e = group_entry(cache, group)
    if not e or e.get("quality_level") == "insufficient":
        return None
    g = (e.get("grid") or {}).get(metric)
    if not g:
        return None
    return g.get("p%d" % int(pct))


def percentile(cache: dict, group: str, metric: str, value) -> float | None:
    """用分位网格做**近似**定位（区间内线性插值）。

    注意：这是查表近似，不是精确分位（精确分位需全量样本）。
    需要精确值时用 `build_distributions` 的原始数组，或跑 `scripts/fund_percentile.py`。
    """
    e = group_entry(cache, group)
    if not e or e.get("quality_level") == "insufficient":
        return None
    g = (e.get("grid") or {}).get(metric)
    if not g or value is None or not np.isfinite(value):
        return None
    xs = [(float(g["p%d" % q]), float(q)) for q in GRID if ("p%d" % q) in g]
    if not xs:
        return None
    xs.sort()
    if value <= xs[0][0]:
        return xs[0][1]
    if value >= xs[-1][0]:
        return xs[-1][1]
    for (x0, y0), (x1, y1) in zip(xs, xs[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return None


def describe(cache: dict, group: str, metric: str, value, nav_asof: str = None) -> dict:
    """B2 的输出形态：百分位 + 组别 + 样本量 + 数据日期 + **样本是否充足**。

    样本不足**显式声明**，绝不返回一个具体数字（本项目反面教材：
    「五个维度全缺失，界面仍显示『市场温度 50.0° 适中』」）。
    """
    e = group_entry(cache, group)
    if e is None:
        return {"group": group, "group_n": None, "percentile": None,
                "insufficient_data": True, "reason": "该组未建参照系", "nav_asof": nav_asof}
    if e.get("quality_level") == "insufficient":
        return {"group": group, "group_n": e.get("n"), "percentile": None,
                "insufficient_data": True,
                "reason": "组内样本不足（n=%s < %d）" % (e.get("n"), 30), "nav_asof": nav_asof}
    p = percentile(cache, group, metric, value)
    if p is None:
        return {"group": group, "group_n": e.get("n"), "percentile": None,
                "insufficient_data": True, "reason": "该指标无数据", "nav_asof": nav_asof}
    return {"group": group, "group_n": e.get("n"), "percentile": round(p, 1),
            "insufficient_data": False, "reason": None, "nav_asof": nav_asof}

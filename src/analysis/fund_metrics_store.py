"""基金指标物化表（E2 分层宽表的 **DWS 层**）。

问题
----
`docs/审计修复记录.md` 第四批 §3 E2：
> 现在**每次筛选都从原始 NAV 重算**全部指标 —— 这是本项目最大的可维护性/性能浪费。

现状：`fund_scorer` / `drawdown_warning` / `peer_percentile` **各自**从 `fund_nav`
（2,290 万行）拉数据、各自算一遍指标。同一只基金的夏普，一天内可能被算 3 次。

分层与职责
----------
| 层 | 是什么 | 现状 |
|---|---|---|
| **ODS** | `fund_nav` 原始净值 | 已有（2,290 万行） |
| **DWD** | 清洗后的估值净值序列（`nav_series.valuation_nav_series`） | 已有（函数级） |
| **DWS** | **本模块**：每只基金 × 最新净值日的全部指标，**增量刷新** | ← 新增 |
| **ADS** | `peer_distributions.json` / `analysis_snapshot` | 已有 |

设计纪律
--------
1. **指标口径不重写** —— 一律委托 `nav_metrics.compute`（它是 SSOT）。
   本模块只负责"**什么时候算、算完存哪**"。
2. **只增不改的增量语义**：只有 `MAX(nav_date) > asof` 的基金才重算。
   首次全量慢，之后每天只算当天有新净值的那些。
3. **可回退**：调用方读不到物化值时**回退实时算**（`load_or_compute`），
   所以本表**不是新的单点故障**。
4. **不做隐式全量刷新** —— `refresh()` 必须显式调用（脚本 / CLI），
   避免在请求路径里意外触发几十分钟的计算。
"""
from __future__ import annotations

import time

import pandas as pd

from .nav_metrics import WINDOW_YEARS, compute as _compute
from .nav_series import valuation_nav_series

TABLE = "fund_metrics"

#: 物化表里存哪些列（= `nav_metrics.compute` 的返回键 + 元信息）
_COLS = ["asof", "window_years", "window_points", "annual_return", "ann_vol",
         "max_drawdown_1y", "max_drawdown_win", "max_drawdown_all", "momentum_3m",
         "sharpe", "sortino", "calmar", "downside_dev"]

_DDL = """
CREATE TABLE IF NOT EXISTS %s (
    fund_code       TEXT PRIMARY KEY,
    asof            TEXT,
    window_years    REAL,
    window_points   INTEGER,
    annual_return   REAL,
    ann_vol         REAL,
    max_drawdown_1y REAL,
    max_drawdown_win REAL,
    max_drawdown_all REAL,
    momentum_3m     REAL,
    sharpe          REAL,
    sortino         REAL,
    calmar          REAL,
    downside_dev    REAL,
    computed_at     REAL
)
""" % TABLE

#: `asof` 等元信息列（不参与指标比较，用于判断是否过期）
_META = ("asof", "window_years", "window_points")


def ensure_table(db) -> None:
    db.conn.execute(_DDL)
    db.conn.commit()


def stale_codes(db, codes=None) -> list:
    """哪些基金需要刷新：**没有物化记录**，或 **最新净值日晚于物化 asof**。

    这是增量刷新的全部逻辑 —— 只看"该基金的最新净值日有没有前进"，
    不去猜"指标会不会变"（净值只增不改，所以日期没动 = 指标没动）。
    """
    ensure_table(db)
    latest = {r[0]: str(r[1]) for r in db.conn.execute(
        "SELECT fund_code, MAX(nav_date) FROM fund_nav WHERE unit_nav IS NOT NULL"
        " GROUP BY fund_code")}
    have = {r[0]: (str(r[1]) if r[1] else None) for r in db.conn.execute(
        "SELECT fund_code, asof FROM %s" % TABLE)}
    out = []
    for code, d in latest.items():
        if codes is not None and code not in set(codes):
            continue
        if have.get(code, "__missing__") != d:
            out.append(code)
    return sorted(out)


def _load_series(db, codes):
    """按批读估值净值序列（复用 `fund_scorer` 验证过的批读手法）。"""
    out = {}
    if not codes:
        return out
    CHUNK = 500
    codes = list(codes)
    for i in range(0, len(codes), CHUNK):
        part = codes[i:i + CHUNK]
        q = ("SELECT fund_code, nav_date, unit_nav, acc_nav FROM fund_nav "
             "WHERE unit_nav IS NOT NULL AND fund_code IN (%s) "
             "ORDER BY fund_code, nav_date" % ",".join("?" * len(part)))
        df = pd.read_sql_query(q, db.conn, params=part)
        for code, sub in df.groupby("fund_code", sort=False):
            out[code] = (valuation_nav_series(sub).to_numpy(dtype=float),
                         sub["nav_date"].astype(str).tolist())
    return out


def refresh(db, codes=None, force: bool = False, progress=None) -> dict:
    """增量刷新物化指标表。**写库**（调用方自行负责快照纪律）。

    Args:
        codes: 只刷新这些（`None` = 全部过期的）
        force: 忽略 asof，重算指定 `codes`（`codes=None` 时=全量，慢）
        progress: `callable(done, total)`，用于长任务打印进度

    Returns:
        `{scanned, refreshed, skipped, failed, seconds}`
    """
    ensure_table(db)
    targets = list(codes) if (codes and force) else stale_codes(db, codes)
    if codes and force and not stale_codes(db, codes):
        targets = list(codes)
    total = len(targets)
    if total == 0:
        return {"scanned": 0, "refreshed": 0, "skipped": 0, "failed": 0, "seconds": 0.0}

    t0 = time.time()
    series = _load_series(db, targets)
    rows, failed = [], 0
    for i, code in enumerate(targets, 1):
        s = series.get(code)
        if not s or s[0].size == 0:
            failed += 1
            continue
        vals, dates = s
        m = _compute(vals, dates)              # ← 指标口径由 SSOT 决定，本模块不重写
        if not m:
            failed += 1
            continue
        rows.append([
            code, dates[-1], m.get("window_years"), m.get("window_points"),
            m.get("annual_return"), m.get("ann_vol"), m.get("max_drawdown_1y"),
            m.get("max_drawdown_win"), m.get("max_drawdown_all"), m.get("momentum_3m"),
            m.get("sharpe"), m.get("sortino"), m.get("calmar"), m.get("downside_dev"),
            time.time(),
        ])
        if progress and i % 200 == 0:
            progress(i, total)

    if rows:
        placeholders = ",".join("?" * (1 + len(_COLS) + 1))
        db.conn.executemany(
            "INSERT OR REPLACE INTO %s (fund_code, %s, computed_at) VALUES (%s)"
            % (TABLE, ", ".join(_COLS), placeholders), rows)
        db.conn.commit()
    return {"scanned": total, "refreshed": len(rows), "skipped": total - len(rows) - failed,
            "failed": failed, "seconds": round(time.time() - t0, 1)}


def load(db, codes=None) -> dict:
    """读物化指标 → `{code: {metric: value}}`。**不触发计算**。"""
    ensure_table(db)
    if codes:
        codes = list(codes)
        out = {}
        CHUNK = 500
        for i in range(0, len(codes), CHUNK):
            part = codes[i:i + CHUNK]
            q = "SELECT * FROM %s WHERE fund_code IN (%s)" % (TABLE, ",".join("?" * len(part)))
            for r in db.conn.execute(q, part):
                d = dict(r)
                out[d.pop("fund_code")] = d
        return out
    return {r["fund_code"]: {k: r[k] for k in r.keys() if k != "fund_code"}
            for r in db.conn.execute("SELECT * FROM %s" % TABLE)}


def load_or_compute(db, codes) -> dict:
    """**首选物化、缺失回退实时算** —— 所以本表不是新的单点故障。

    回退的部分会在返回值里标 `_computed_at` 为 `None`，调用方可据此判断
    "这个数字是物化的还是刚算的"（口径可追溯）。
    """
    got = load(db, codes)
    missing = [c for c in (codes or []) if c not in got]
    if missing:
        series = _load_series(db, missing)
        for code in missing:
            s = series.get(code)
            if not s or s[0].size == 0:
                continue
            vals, dates = s
            m = _compute(vals, dates)
            if m:
                got[code] = dict(m, asof=dates[-1], computed_at=None)
    return got


def stats(db) -> dict:
    """物化表概览（给体检脚本用）。"""
    ensure_table(db)
    r = db.conn.execute(
        "SELECT COUNT(*) n, MIN(asof) a, MAX(asof) b, MAX(computed_at) t FROM %s" % TABLE
    ).fetchone()
    return {"rows": r[0], "asof_min": r[1], "asof_max": r[2], "computed_at": r[3],
            "window_years": WINDOW_YEARS}

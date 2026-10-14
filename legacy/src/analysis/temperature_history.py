"""市场温度**历史序列**的重建 —— 按 `thermometer.py` 的真实公式逐项复刻。

## 为什么需要重建

温度是**实时算的**，没有历史存档；而 `index_daily` 的两个分位列当初全表留了 0
（见 `percentiles.py` 模块 docstring）。所以"给温度做历史回测"只能按真实公式重建。

2026-10-01 实测：按本模块重建出的序列**末值 46.2 == 项目实报 46.2，差 0.0 分**
（见 `docs/审计修复记录.md` 第四批 §11）。

## 与实时实现的两处刻意差别（都写在这里，改要一起改）

* **分位全用扩张窗口（无未来函数）**：原 `_calc_valuation_scores` 读的是
  `index_valuation` 快照，其分位口径未知（可能是全样本）。回测**必须**无前视，
  所以本模块统一用 `percentiles.expanding_percentile`。
* **LPR 用离线回退值 3.0**：原 `_get_risk_free_rate` 在线拉 1 年期 LPR、离线回退 3.0。
  历史重建拿不到逐日 LPR，故固定 3.0（真实 LPR 在 3.0~3.85 间波动）。
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from .percentiles import MIN_PERIODS, expanding_percentile

#: 与 `MarketThermometer.DEFAULT_WEIGHTS` **保持一致**（改温度权重时同步这里）
WEIGHTS = {"pe": 0.25, "pb": 0.15, "erp": 0.25, "volume": 0.20, "sentiment": 0.15}
#: 三指数在 PE/PB 分位里的权重（与 `_calc_valuation_scores` 一致）
INDEX_WEIGHTS = {"000300": 0.5, "000905": 0.3, "000016": 0.2}
#: 无风险利率（1 年期 LPR 的离线回退值，`_get_risk_free_rate`）
BOND_YIELD = 3.0


def _pct_of_past(series: pd.Series) -> pd.Series:
    """每个值在**它之前（含自身）全部值**里的分位（0-100）。无未来函数，O(n log n)。"""
    out = np.full(len(series), np.nan)
    seen: list[float] = []
    for i, v in enumerate(series.values):
        if pd.isna(v):
            continue
        bisect.insort(seen, float(v))
        out[i] = bisect.bisect_left(seen, float(v)) / len(seen) * 100.0
    return pd.Series(out, index=series.index)


def reconstruct(db) -> pd.DataFrame:
    """重建历史温度，返回 DataFrame：index=trade_date，列 temperature/pe_score/…（0-100）。"""
    codes = INDEX_WEIGHTS
    frames: dict[str, pd.DataFrame] = {}
    for code in codes:
        d = pd.read_sql_query(
            "SELECT trade_date, close, volume, pe, pb FROM index_daily"
            " WHERE index_code = ? AND close IS NOT NULL ORDER BY trade_date",
            db.conn, params=[code])
        d["trade_date"] = pd.to_datetime(d["trade_date"])
        frames[code] = d.set_index("trade_date")

    hs = frames["000300"]
    idx = hs.index

    # ── 1/2. PE / PB 分位（三指数加权）───────────────────────────────
    def weighted_pct(col: str) -> pd.Series:
        num = pd.Series(0.0, index=idx)
        den = pd.Series(0.0, index=idx)
        for code, w in codes.items():
            s = frames[code][col].reindex(idx)
            s = pd.to_numeric(s, errors="coerce").replace(0, np.nan)   # 0 是占位符
            p = expanding_percentile(s)
            ok = p.notna()
            num[ok] += (p[ok] * w)
            den[ok] += w
        return (num / den.replace(0, np.nan)).fillna(50.0)

    pe_score = weighted_pct("pe")
    pb_score = weighted_pct("pb")

    # ── 3. ERP ───────────────────────────────────────────────────────
    pe_hs = pd.to_numeric(hs["pe"], errors="coerce").replace(0, np.nan)
    erp = (1.0 / pe_hs) * 100 - BOND_YIELD
    erp_score = (100 - (erp - 2.0) / (6.5 - 2.0) * 100).clip(5, 95)

    # ── 4. 成交量 ────────────────────────────────────────────────────
    vol = pd.to_numeric(hs["volume"], errors="coerce").replace(0, np.nan)
    vol_20d = vol.rolling(20).mean()
    vol_pct = _pct_of_past(vol_20d)
    pc20 = (hs["close"] / hs["close"].shift(20) - 1) * 100
    vc20 = (vol_20d / vol.rolling(20).mean().shift(20) - 1) * 100
    adj = np.select([(pc20 < -3) & (vc20 < -20), (pc20 > 5) & (vc20 < -10),
                     (pc20 < -5) & (vc20 > 20)], [-10.0, 10.0, 5.0], default=0.0)
    volume_score = (vol_pct + adj).clip(5, 95)

    # ── 5. 情绪（波动率分位 30% + 涨跌档位 30% + 量能趋势 40%）────────
    ret = hs["close"].pct_change()
    annvol = ret.rolling(20).std() * np.sqrt(252) * 100
    s_vol = annvol.expanding(min_periods=MIN_PERIODS).rank(pct=True) * 100
    r20 = (hs["close"] / hs["close"].shift(20) - 1) * 100
    r60 = (hs["close"] / hs["close"].shift(60) - 1) * 100
    s_ret = pd.Series(np.select([(r20 > 10) & (r60 > 30), r20 > 5, r20 < -10, r20 < -5],
                                [90.0, 65.0, 20.0, 35.0], default=50.0), index=idx)
    vr = vol.rolling(5).mean() / vol.shift(5).rolling(55).mean()
    s_vt = pd.Series(np.select([vr > 2.0, vr > 1.5, vr > 1.2, vr < 0.6, vr < 0.8],
                               [85.0, 65.0, 55.0, 20.0, 35.0], default=50.0), index=idx)
    sentiment_score = (s_vol.fillna(50) * 0.30 + s_ret * 0.30 + s_vt * 0.40).clip(5, 95)

    # ── 合成（权重按 WEIGHTS，逐项填充缺省 50 后归一）─────────────────
    parts = {"pe": pe_score, "pb": pb_score, "erp": erp_score,
             "volume": volume_score, "sentiment": sentiment_score}
    num = sum(parts[k].fillna(50.0) * w for k, w in WEIGHTS.items())
    temp = (num / sum(WEIGHTS.values())).clip(0, 100)

    out = pd.DataFrame({"temperature": temp, **parts}).dropna(subset=["temperature"])
    out.index.name = "trade_date"
    return out


def upsert(db, df: pd.DataFrame, computed_at: float) -> int:
    """把重建结果写进 `market_temperature`（按日期覆盖）。返回写入行数。"""
    rows = [(str(d.date()), float(r["temperature"]),
             float(r["pe"]), float(r["pb"]), float(r["erp"]),
             float(r["volume"]), float(r["sentiment"]), computed_at)
            for d, r in df.iterrows()]
    with db.immediate():
        db.conn.executemany(
            "INSERT OR REPLACE INTO market_temperature"
            " (trade_date, temperature, pe_score, pb_score, erp_score,"
            "  volume_score, sentiment_score, computed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)

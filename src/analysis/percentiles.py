"""扩展窗口分位数 —— **单一实现**（口径 SSOT）。

## 为什么单独成模块（2026-10-01）

`index_daily.pe_percentile` / `pb_percentile` 在**采集时根本没算** ——
全表 15,215 行（三个指数）留了 `0`。`vol_predictor.load_index_data` 当初就地写了一份补算逻辑，
于是"分位怎么算"散落在模块内部；后来给温度做**历史回测**时又需要同一份逻辑
（见 `docs/审计修复记录.md` 第四批 §11）。三处各写一遍必然漂移，故收敛到本模块。

## 口径（写死在这里，要改就全项目一起改）

* **扩展窗口**：只用 ≤ 当日的数据 ⇒ **无未来函数**。回测的生命线 ——
  用全样本分位会让"当时的估值处在历史什么位置"变成一句作弊的陈述。
* **严格小于、分母为"非 NaN 个数"**：与采集侧的权威口径**逐位一致** ——
  `collector.save_index_val_to_db` 里是 `(pe_values < current_pe).sum() / len(pe_values)`，
  且 `pe_values` 先 `dropna()`（NaN 不进分母）。
  `vol_predictor` 里那份老代码**曾把 NaN 也算进分母**（`(w < w[-1]).sum() / len(w)`），
  会轻微压低分位 —— 2026-10-01 统一到采集侧口径，老代码改走本模块（差 ≤0.0022、仅 3 行）。
* **返回 0–100**（与 `index_valuation.*_percentile` 的百分数口径一致）。
  需要 `[0,1]` 的调用方自行 `/ 100`。
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

#: 计算分位所需的最少样本数（约一年交易日）。与原实现一致。
MIN_PERIODS = 252


def expanding_percentile(values, min_periods: int = MIN_PERIODS) -> pd.Series:
    """逐点扩展窗口分位（0–100），**只用当日及之前的数据**。

    第 t 个点的含义：在 `values[:t+1]` 的**非 NaN 值**里，严格小于 `values[t]` 的比例（×100）。
    NaN 不进分母（与采集侧权威口径一致，见模块 docstring）。
    前 `min_periods` 个点为 NaN（样本不足，**不猜** —— 不拿 50 伪装成中性读数）。
    """
    s = pd.Series(values, dtype="float64")
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    valid = s.dropna()
    if valid.empty:
        return out
    seen: list[float] = []
    for i, v in enumerate(valid.values):
        bisect.insort(seen, float(v))
        if len(seen) >= min_periods:
            out.loc[valid.index[i]] = bisect.bisect_left(seen, float(v)) / len(seen) * 100.0
    return out


def recompute_index_percentiles(db, index_code: str) -> int:
    """按库内 `pe`/`pb` 重算并写回 `index_daily` 的两个分位列。返回处理的行数。

    ## 为什么需要"重算"这件事

    `pe_percentile` / `pb_percentile` 是**派生列**，但**采集侧算不出来** ——
    它拿不到"该指数到今天为止的完整历史"（这正是分位的分母）。于是两处采集都留了 0：

      · `collector.py`：`"pe_percentile": pe_data.get("pe_pct", 0)`、**`"pb_percentile": 0`（硬编码）**
      · `hithink_collector.py`：`"pe": 0, "pb": 0, "pe_percentile": 0, "pb_percentile": 0`（全硬编码）

    实测后果：全表 15,215 行（三个指数）的两个分位列**全是 0**。而 `0` 恰好是一个
    **看起来合法的值**（"PE 分位 0 = 极冷"）→ 任何直接读它的代码都会静默拿到极端结论。
    所以正确做法是：**采集只写原始 pe/pb；分位由本函数在写库后统一重算**。

    ⚠️ `pe`/`pb` 为 0 的行按**缺失**处理 —— 0 倍 PE/PB 在经济上不可能，那是占位符
    （`hithink_collector` 就写 0）。同理 NaN。

    **幂等**：同一份数据重复跑结果完全相同。
    """
    df = pd.read_sql_query(
        "SELECT trade_date, pe, pb FROM index_daily WHERE index_code = ? ORDER BY trade_date",
        db.conn, params=[index_code])
    if df.empty:
        return 0

    def _series(col: str) -> pd.Series:
        s = pd.to_numeric(df[col], errors="coerce")
        return s.replace(0, np.nan)          # 0 是占位符，不是真实估值

    pe_pct, pb_pct = expanding_percentile(_series("pe")), expanding_percentile(_series("pb"))

    def _v(x):
        return None if pd.isna(x) else round(float(x), 2)

    rows = [(_v(p), _v(q), index_code, str(d))
            for d, p, q in zip(df["trade_date"], pe_pct, pb_pct)]
    with db.immediate():
        db.conn.executemany(
            "UPDATE index_daily SET pe_percentile = ?, pb_percentile = ?"
            " WHERE index_code = ? AND trade_date = ?", rows)
    return len(rows)
